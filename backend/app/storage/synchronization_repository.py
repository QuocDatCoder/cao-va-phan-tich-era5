from datetime import UTC, date, datetime
from pathlib import Path
import sqlite3

from backend.app.domain.schemas import (
    DownloadArea,
    SynchronizationRequest,
    SynchronizationState,
    SynchronizationStatus,
)


class SynchronizationRepository:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._ensure_schema()

    def get(self, sync_id: str) -> SynchronizationState | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM synchronization_states WHERE sync_id = ?",
                (sync_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_state(row)

    def upsert_queued(
        self,
        sync_id: str,
        request: SynchronizationRequest,
        latest_available_date: date,
        job_start_date: date,
        job_end_date: date,
        job_id: str,
    ) -> SynchronizationState:
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO synchronization_states (
                    sync_id, variable, start_date, north, west, south, east, frequency,
                    latest_available_date, last_synced_date, job_start_date, job_end_date,
                    job_id, status, error, created_at, updated_at, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, NULL, ?, ?, NULL)
                ON CONFLICT(sync_id) DO UPDATE SET
                    latest_available_date = excluded.latest_available_date,
                    job_start_date = excluded.job_start_date,
                    job_end_date = excluded.job_end_date,
                    job_id = excluded.job_id,
                    status = excluded.status,
                    error = NULL,
                    updated_at = excluded.updated_at,
                    completed_at = NULL
                """,
                (
                    sync_id,
                    request.variable,
                    request.start_date.isoformat(),
                    request.area.north,
                    request.area.west,
                    request.area.south,
                    request.area.east,
                    request.frequency,
                    latest_available_date.isoformat(),
                    job_start_date.isoformat(),
                    job_end_date.isoformat(),
                    job_id,
                    SynchronizationStatus.QUEUED.value,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        state = self.get(sync_id)
        if state is None:
            raise RuntimeError("Queued synchronization could not be loaded.")
        return state

    def mark_running(self, sync_id: str) -> SynchronizationState | None:
        return self._update_status(
            sync_id=sync_id,
            status=SynchronizationStatus.RUNNING,
            error=None,
            completed=False,
        )

    def mark_completed(self, sync_id: str, last_synced_date: date) -> SynchronizationState | None:
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE synchronization_states
                SET status = ?, last_synced_date = ?, error = NULL,
                    updated_at = ?, completed_at = ?
                WHERE sync_id = ?
                """,
                (
                    SynchronizationStatus.COMPLETED.value,
                    last_synced_date.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                    sync_id,
                ),
            )
        return self.get(sync_id)

    def mark_up_to_date(
        self,
        sync_id: str,
        request: SynchronizationRequest,
        latest_available_date: date,
        last_synced_date: date | None,
    ) -> SynchronizationState:
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO synchronization_states (
                    sync_id, variable, start_date, north, west, south, east, frequency,
                    latest_available_date, last_synced_date, job_start_date, job_end_date,
                    job_id, status, error, created_at, updated_at, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, NULL, ?, ?, ?)
                ON CONFLICT(sync_id) DO UPDATE SET
                    latest_available_date = excluded.latest_available_date,
                    last_synced_date = COALESCE(excluded.last_synced_date, last_synced_date),
                    job_start_date = NULL,
                    job_end_date = NULL,
                    job_id = NULL,
                    status = excluded.status,
                    error = NULL,
                    updated_at = excluded.updated_at,
                    completed_at = excluded.completed_at
                """,
                (
                    sync_id,
                    request.variable,
                    request.start_date.isoformat(),
                    request.area.north,
                    request.area.west,
                    request.area.south,
                    request.area.east,
                    request.frequency,
                    latest_available_date.isoformat(),
                    last_synced_date.isoformat() if last_synced_date else None,
                    SynchronizationStatus.UP_TO_DATE.value,
                    now.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        state = self.get(sync_id)
        if state is None:
            raise RuntimeError("Up-to-date synchronization could not be loaded.")
        return state

    def mark_failed(self, sync_id: str, error: str) -> SynchronizationState | None:
        return self._update_status(
            sync_id=sync_id,
            status=SynchronizationStatus.FAILED,
            error=error,
            completed=True,
        )

    def _update_status(
        self,
        sync_id: str,
        status: SynchronizationStatus,
        error: str | None,
        completed: bool,
    ) -> SynchronizationState | None:
        now = self._now()
        completed_at = now.isoformat() if completed else None
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE synchronization_states
                SET status = ?, error = ?, updated_at = ?, completed_at = ?
                WHERE sync_id = ?
                """,
                (status.value, error, now.isoformat(), completed_at, sync_id),
            )
        return self.get(sync_id)

    def _ensure_schema(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS synchronization_states (
                    sync_id TEXT PRIMARY KEY,
                    variable TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    north REAL NOT NULL,
                    west REAL NOT NULL,
                    south REAL NOT NULL,
                    east REAL NOT NULL,
                    frequency TEXT NOT NULL,
                    latest_available_date TEXT,
                    last_synced_date TEXT,
                    job_start_date TEXT,
                    job_end_date TEXT,
                    job_id TEXT,
                    status TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _row_to_state(self, row: sqlite3.Row) -> SynchronizationState:
        return SynchronizationState(
            sync_id=row["sync_id"],
            variable=row["variable"],
            start_date=date.fromisoformat(row["start_date"]),
            area=DownloadArea(
                north=row["north"],
                west=row["west"],
                south=row["south"],
                east=row["east"],
            ),
            frequency=row["frequency"],
            latest_available_date=(
                date.fromisoformat(row["latest_available_date"])
                if row["latest_available_date"]
                else None
            ),
            last_synced_date=(
                date.fromisoformat(row["last_synced_date"]) if row["last_synced_date"] else None
            ),
            job_start_date=(
                date.fromisoformat(row["job_start_date"]) if row["job_start_date"] else None
            ),
            job_end_date=date.fromisoformat(row["job_end_date"]) if row["job_end_date"] else None,
            job_id=row["job_id"],
            status=SynchronizationStatus(row["status"]),
            error=row["error"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            completed_at=(
                datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None
            ),
        )

    def _now(self) -> datetime:
        return datetime.now(UTC)
