from contextlib import contextmanager
from collections.abc import Iterable
from datetime import datetime
from math import isfinite
from pathlib import Path
import sqlite3
import json

from backend.app.domain.schemas import ClimatePoint, DownloadArea, Location
from backend.app.domain.time_utils import hourly_timestamps, to_utc_naive


class ClimateRepository:
    """Provider values keyed by dataset, canonical variable, actual grid and UTC."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS climate_points (
                    dataset TEXT NOT NULL,
                    variable TEXT NOT NULL,
                    grid_latitude REAL NOT NULL,
                    grid_longitude REAL NOT NULL,
                    time_utc TEXT NOT NULL,
                    unit TEXT NOT NULL,
                    value REAL NOT NULL,
                    PRIMARY KEY (dataset, variable, grid_latitude, grid_longitude, time_utc)
                );
                CREATE TABLE IF NOT EXISTS climate_locations (
                    dataset TEXT NOT NULL,
                    variable TEXT NOT NULL,
                    requested_latitude REAL NOT NULL,
                    requested_longitude REAL NOT NULL,
                    grid_latitude REAL NOT NULL,
                    grid_longitude REAL NOT NULL,
                    PRIMARY KEY (dataset, variable, requested_latitude, requested_longitude)
                );
                CREATE TABLE IF NOT EXISTS climate_areas (
                    dataset TEXT NOT NULL,
                    variable TEXT NOT NULL,
                    north REAL NOT NULL, west REAL NOT NULL,
                    south REAL NOT NULL, east REAL NOT NULL,
                    latitudes_json TEXT NOT NULL,
                    longitudes_json TEXT NOT NULL,
                    PRIMARY KEY (dataset, variable, north, west, south, east)
                );
            """)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self._db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def save(
        self, *, dataset: str, variable: str, requested_location: Location,
        grid_location: Location, unit: str, points: list[ClimatePoint],
    ) -> None:
        records = [
            (dataset, variable, grid_location.latitude, grid_location.longitude,
             to_utc_naive(point.time).isoformat(timespec="microseconds"), unit, point.value)
            for point in points if isfinite(point.value)
        ]
        with self._connect() as connection:
            connection.executemany("""
                INSERT INTO climate_points VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (dataset, variable, grid_latitude, grid_longitude, time_utc)
                DO UPDATE SET unit=excluded.unit, value=excluded.value
            """, records)
            connection.execute("""
                INSERT INTO climate_locations VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (dataset, variable, requested_latitude, requested_longitude)
                DO UPDATE SET grid_latitude=excluded.grid_latitude,
                              grid_longitude=excluded.grid_longitude
            """, (dataset, variable, requested_location.latitude, requested_location.longitude,
                  grid_location.latitude, grid_location.longitude))

    def save_area(
        self, *, dataset: str, variable: str, unit: str, area: DownloadArea,
        latitudes: list[float], longitudes: list[float],
        records: Iterable[tuple[float, float, datetime, float]],
    ) -> int:
        """Import a grid and its lookup metadata atomically, without buffering all rows."""
        count = 0

        def rows():
            nonlocal count
            for latitude, longitude, timestamp, value in records:
                if isfinite(value):
                    count += 1
                    yield (dataset, variable, latitude, longitude,
                           to_utc_naive(timestamp).isoformat(timespec="microseconds"), unit, value)

        with self._connect() as connection:
            connection.executemany("""
                INSERT INTO climate_points VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (dataset, variable, grid_latitude, grid_longitude, time_utc)
                DO UPDATE SET unit=excluded.unit, value=excluded.value
            """, rows())
            if count == 0:
                raise ValueError("The area contains no finite observations.")
            connection.execute("""
                INSERT INTO climate_areas VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (dataset, variable, north, west, south, east)
                DO UPDATE SET latitudes_json=excluded.latitudes_json,
                              longitudes_json=excluded.longitudes_json
            """, (dataset, variable, area.north, area.west, area.south, area.east,
                  json.dumps(latitudes), json.dumps(longitudes)))
        return count

    def _resolve_grid(self, connection, dataset: str, variable: str, location: Location):
        mapped = connection.execute("""
            SELECT grid_latitude, grid_longitude FROM climate_locations
            WHERE dataset=? AND variable=? AND requested_latitude=? AND requested_longitude=?
        """, (dataset, variable, location.latitude, location.longitude)).fetchone()
        if mapped is not None:
            return mapped["grid_latitude"], mapped["grid_longitude"]
        areas = connection.execute("""
            SELECT latitudes_json, longitudes_json FROM climate_areas
            WHERE dataset=? AND variable=? AND south<=? AND north>=? AND west<=? AND east>=?
            ORDER BY (north-south)*(east-west)
        """, (dataset, variable, location.latitude, location.latitude,
              location.longitude, location.longitude)).fetchall()
        for area in areas:
            latitudes = json.loads(area["latitudes_json"])
            longitudes = json.loads(area["longitudes_json"])
            requested_lon = location.longitude
            if min(longitudes) >= 0 and requested_lon < 0:
                requested_lon %= 360
            # Match xarray's nearest-coordinate tie break (larger coordinate).
            latitude = min(latitudes, key=lambda value: (abs(value-location.latitude), -value))
            longitude = min(longitudes, key=lambda value: (abs(value-requested_lon), -value))
            return latitude, (longitude + 180) % 360 - 180
        return None

    def get_complete_series(
        self, *, dataset: str, variable: str, location: Location, unit: str,
        start: datetime, end: datetime,
    ) -> list[ClimatePoint] | None:
        """A cache hit requires every expected hourly observation, including gaps."""
        expected = hourly_timestamps(start, end)
        if not expected:
            return None
        points = self.get_series(dataset=dataset, variable=variable, location=location,
                                 unit=unit, start=start, end=end)
        if [point.time for point in points] != expected:
            return None
        return points

    def get_series(
        self, *, dataset: str, variable: str, location: Location, unit: str,
        start: datetime, end: datetime,
    ) -> list[ClimatePoint]:
        """Read existing observations, including partially covered intervals."""
        start, end = to_utc_naive(start), to_utc_naive(end)
        with self._connect() as connection:
            grid = self._resolve_grid(connection, dataset, variable, location)
            if grid is None:
                return []
            rows = connection.execute("""
                SELECT time_utc, value FROM climate_points
                WHERE dataset=? AND variable=? AND grid_latitude=? AND grid_longitude=?
                  AND unit=? AND time_utc>=? AND time_utc<=?
                ORDER BY time_utc
            """, (dataset, variable, grid[0], grid[1], unit,
                  start.isoformat(timespec="microseconds"), end.isoformat(timespec="microseconds"))).fetchall()
        return [ClimatePoint(time=datetime.fromisoformat(row["time_utc"]), value=row["value"])
                for row in rows]
