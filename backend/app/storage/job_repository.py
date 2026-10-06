from datetime import UTC, datetime
from pathlib import Path
import sqlite3

from backend.app.domain.schemas import (
    DownloadArea,
    DownloadArtifact,
    DownloadJob,
    DownloadJobStatus,
    DownloadRequest,
)


class JobRepository:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._ensure_schema()

    def create(self, job_id: str, request: DownloadRequest) -> DownloadJob:
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO download_jobs (
                    job_id, variable, start_date, end_date, north, west, south, east,
                    frequency, status, progress, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    request.variable,
                    request.start_date.isoformat(),
                    request.end_date.isoformat(),
                    request.area.north,
                    request.area.west,
                    request.area.south,
                    request.area.east,
                    request.frequency,
                    DownloadJobStatus.QUEUED.value,
                    0,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        job = self.get(job_id)
        if job is None:
            raise RuntimeError("Created job could not be loaded.")
        return job

    def get(self, job_id: str) -> DownloadJob | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM download_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_job(row)

    def mark_running(self, job_id: str) -> DownloadJob | None:
        return self._update_status(
            job_id=job_id,
            status=DownloadJobStatus.RUNNING,
            progress=0,
            error=None,
        )

    def mark_completed(self, job_id: str, artifact: DownloadArtifact) -> DownloadJob | None:
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE download_jobs
                SET status = ?, progress = ?, updated_at = ?, completed_at = ?,
                    dataset = ?, file_path = ?, error = NULL
                WHERE job_id = ?
                """,
                (
                    DownloadJobStatus.COMPLETED.value,
                    100,
                    now.isoformat(),
                    now.isoformat(),
                    artifact.dataset,
                    artifact.storage_path,
                    job_id,
                ),
            )
        return self.get(job_id)

    def mark_failed(self, job_id: str, error: str) -> DownloadJob | None:
        return self._update_status(
            job_id=job_id,
            status=DownloadJobStatus.FAILED,
            progress=0,
            error=error,
            completed=True,
        )

    def _update_status(
        self,
        job_id: str,
        status: DownloadJobStatus,
        progress: int,
        error: str | None,
        completed: bool = False,
    ) -> DownloadJob | None:
        now = self._now()
        completed_at = now.isoformat() if completed else None
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE download_jobs
                SET status = ?, progress = ?, updated_at = ?, completed_at = ?, error = ?
                WHERE job_id = ?
                """,
                (
                    status.value,
                    progress,
                    now.isoformat(),
                    completed_at,
                    error,
                    job_id,
                ),
            )
        return self.get(job_id)

    def _ensure_schema(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS download_jobs (
                    job_id TEXT PRIMARY KEY,
                    variable TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    north REAL NOT NULL,
                    west REAL NOT NULL,
                    south REAL NOT NULL,
                    east REAL NOT NULL,
                    frequency TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    dataset TEXT,
                    file_path TEXT,
                    error TEXT
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _row_to_job(self, row: sqlite3.Row) -> DownloadJob:
        return DownloadJob(
            job_id=row["job_id"],
            variable=row["variable"],
            start_date=row["start_date"],
            end_date=row["end_date"],
            area=DownloadArea(
                north=row["north"],
                west=row["west"],
                south=row["south"],
                east=row["east"],
            ),
            frequency=row["frequency"],
            status=DownloadJobStatus(row["status"]),
            progress=row["progress"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            completed_at=(
                datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None
            ),
            dataset=row["dataset"],
            file_path=row["file_path"],
            error=row["error"],
        )

    def _now(self) -> datetime:
        return datetime.now(UTC)
