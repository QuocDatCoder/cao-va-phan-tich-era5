"""Shared Open-Meteo observations and in-process request coalescing."""
from collections import defaultdict
from collections.abc import Callable, Hashable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from itertools import groupby
from pathlib import Path
import sqlite3
from threading import Event, Lock
from time import time
from typing import Any, TypeVar

from backend.app.domain.schemas import ClimatePoint, Location
from backend.app.domain.time_utils import to_utc_naive


class SharedOpenMeteoCache:
    def __init__(self, db_path: Path):
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS shared_location_grids (
                    dataset TEXT, req_lat REAL, req_lon REAL, grid_lat REAL,
                    grid_lon REAL, updated_at INTEGER,
                    PRIMARY KEY (dataset, req_lat, req_lon)
                );
                CREATE TABLE IF NOT EXISTS shared_hourly_observations (
                    dataset TEXT, grid_lat REAL, grid_lon REAL, time_utc TEXT,
                    variable TEXT, unit TEXT, value REAL, fetched_at INTEGER,
                    is_historical INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (dataset, grid_lat, grid_lon, time_utc, variable)
                );
                CREATE INDEX IF NOT EXISTS ix_shared_obs_lookup
                    ON shared_hourly_observations(dataset, grid_lat, grid_lon, time_utc);
            """)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self._db_path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def lookup_grid(self, dataset: str, location: Location) -> Location | None:
        with self._connect() as connection:
            grid = connection.execute("""
                SELECT grid_lat, grid_lon FROM shared_location_grids
                WHERE dataset=? AND req_lat=? AND req_lon=?
            """, (dataset, round(location.latitude, 4), round(location.longitude, 4))).fetchone()
        if grid is None:
            return None
        return Location(latitude=grid[0], longitude=grid[1])

    def read_range(
        self, dataset: str, grid: Location, start_utc: datetime, end_utc: datetime, *,
        required_variables: set[str], live_ttl_seconds: int = 1800, now: int | None = None,
    ) -> tuple[dict[str, list[ClimatePoint]], set[datetime]]:
        now = int(time()) if now is None else now
        series = defaultdict(list)
        valid_hours = set()
        with self._connect() as connection:
            rows = connection.execute("""
                SELECT time_utc, variable, value FROM shared_hourly_observations
                WHERE dataset=? AND grid_lat=? AND grid_lon=? AND time_utc BETWEEN ? AND ?
                  AND (is_historical=1 OR fetched_at>=?)
                ORDER BY time_utc
            """, (dataset, round(grid.latitude, 10), round(grid.longitude, 10),
                  to_utc_naive(start_utc).isoformat(timespec="microseconds"),
                  to_utc_naive(end_utc).isoformat(timespec="microseconds"), now - live_ttl_seconds))
            for timestamp, observations in groupby(rows, key=lambda row: row[0]):
                values = {variable: value for _, variable, value in observations}
                if required_variables <= values.keys():
                    hour = datetime.fromisoformat(timestamp)
                    valid_hours.add(hour)
                    for variable, value in values.items():
                        series[variable].append(ClimatePoint(time=hour, value=value))
        return dict(series), valid_hours

    def write_range(
        self, dataset: str, requested_location: Location, grid: Location,
        series: dict[str, list[ClimatePoint]], units: dict[str, str], *,
        historical_cutoff_date: date, now: int | None = None,
    ) -> None:
        now = int(time()) if now is None else now
        grid_lat, grid_lon = round(grid.latitude, 10), round(grid.longitude, 10)

        def records():
            for variable, points in series.items():
                for point in points:
                    stamp = to_utc_naive(point.time)
                    yield (dataset, grid_lat, grid_lon, stamp.isoformat(timespec="microseconds"),
                           variable, units[variable], point.value, now,
                           int(point.time.date() < historical_cutoff_date))

        with self._connect() as connection:
            connection.execute("""
                INSERT INTO shared_location_grids VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (dataset, req_lat, req_lon) DO UPDATE SET
                    grid_lat=excluded.grid_lat, grid_lon=excluded.grid_lon, updated_at=excluded.updated_at
            """, (dataset, round(requested_location.latitude, 4), round(requested_location.longitude, 4),
                  grid.latitude, grid.longitude, now))
            connection.executemany("""
                INSERT INTO shared_hourly_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (dataset, grid_lat, grid_lon, time_utc, variable) DO UPDATE SET
                    unit=excluded.unit, value=excluded.value, fetched_at=excluded.fetched_at,
                    is_historical=excluded.is_historical
            """, records())

    def stats(self) -> dict[str, int]:
        with self._connect() as connection:
            total_rows = connection.execute("SELECT COUNT(*) FROM shared_hourly_observations").fetchone()[0]
            unique_grids = connection.execute("""
                SELECT COUNT(*) FROM (
                    SELECT DISTINCT dataset, grid_lat, grid_lon FROM shared_hourly_observations
                )
            """).fetchone()[0]
        return dict(total_rows=total_rows, unique_grids=unique_grids, db_bytes=self._db_path.stat().st_size)


T = TypeVar("T")


@dataclass
class _Flight:
    done: Event = field(default_factory=Event)
    result: Any = None
    error: BaseException | None = None


class Singleflight:
    """Share the outcome of an in-progress call among callers with the same key."""

    def __init__(self):
        self._lock = Lock()
        self._flights: dict[Hashable, _Flight] = {}

    def do(self, key: Hashable, fn: Callable[[], T]) -> T:
        with self._lock:
            flight = self._flights.get(key)
            leader = flight is None
            if leader:
                flight = self._flights[key] = _Flight()

        if not leader:
            flight.done.wait()
            if flight.error is not None:
                raise flight.error
            return flight.result

        try:
            flight.result = fn()
            return flight.result
        except BaseException as exc:
            flight.error = exc
            raise
        finally:
            with self._lock:
                flight.done.set()
                del self._flights[key]
