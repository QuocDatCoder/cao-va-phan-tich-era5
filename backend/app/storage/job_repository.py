from datetime import UTC, datetime
from pathlib import Path
from backend.app.domain.quality import QualityReport
import sqlite3
import json
from hashlib import sha256

from backend.app.domain.schemas import (
    DownloadArea,
    DownloadArtifact,
    DownloadJob,
    DownloadJobStatus,
    DownloadRequest,
    Location,
)


class JobRepository:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._ensure_schema()

    def create(self, job_id: str, request: DownloadRequest, *, dataset: str | None = None,
               retrieval_variables: list[str] | None = None) -> DownloadJob:
        now = self._now()
        location = request.location
        area = request.area or DownloadArea(north=location.latitude, south=location.latitude,
                                            west=location.longitude, east=location.longitude)
        fingerprint = sha256(json.dumps({"request": request.model_dump(mode="json"),
            "dataset": dataset, "variables": retrieval_variables or []}, sort_keys=True).encode()).hexdigest()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute("SELECT * FROM download_jobs WHERE request_key=? AND status IN ('queued','running') ORDER BY created_at LIMIT 1",
                                        (fingerprint,)).fetchone()
            if active is not None:
                return self._row_to_job(active)
            connection.execute(
                """
                INSERT INTO download_jobs (
                    job_id, variable, start_date, end_date, north, west, south, east,
                    frequency, status, progress, created_at, updated_at,
                    source, location_latitude, location_longitude, retrieval_variables, dataset, request_key
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    request.variable,
                    request.start_date.isoformat(),
                    request.end_date.isoformat(),
                    area.north,
                    area.west,
                    area.south,
                    area.east,
                    request.frequency,
                    DownloadJobStatus.QUEUED.value,
                    0,
                    now.isoformat(),
                    now.isoformat(),
                    request.source,
                    location.latitude if location else None,
                    location.longitude if location else None,
                    json.dumps(retrieval_variables or []),
                    dataset,
                    fingerprint,
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

    def claim_running(self, job_id: str) -> bool:
        """Only one background worker may claim a queued job."""
        with self._connect() as connection:
            return connection.execute("UPDATE download_jobs SET status='running',updated_at=? WHERE job_id=? AND status='queued'",
                                      (self._now().isoformat(), job_id)).rowcount == 1

    def list_jobs(self, limit=100, offset=0):
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM download_jobs ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return [self._row_to_job(row) for row in rows]

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
            # Additive migration: retain existing jobs, status, files and timestamps.
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(download_jobs)")}
            for name, sql_type in {
                "source": "TEXT", "location_latitude": "REAL", "location_longitude": "REAL",
                "retrieval_variables": "TEXT NOT NULL DEFAULT '[]'",
                "quality_json": "TEXT",
                "request_key": "TEXT",
            }.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE download_jobs ADD COLUMN {name} {sql_type}")
            connection.execute("""CREATE TABLE IF NOT EXISTS download_quality_issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, payload TEXT NOT NULL)""")
            connection.execute("CREATE INDEX IF NOT EXISTS quality_job_idx ON download_quality_issues(job_id, id)")
            connection.execute("CREATE INDEX IF NOT EXISTS active_request_idx ON download_jobs(request_key, status)")

    def add_quality_issues(self, job_id, issues):
        with self._connect() as connection:
            connection.executemany("INSERT INTO download_quality_issues(job_id,payload) VALUES (?,?)",
                                   [(job_id, json.dumps(issue, ensure_ascii=False)) for issue in issues])

    def save_quality(self, job_id, report):
        with self._connect() as connection:
            connection.execute("UPDATE download_jobs SET quality_json=? WHERE job_id=?",
                               (report.model_dump_json(), job_id))

    def quality_issues(self, job_id, offset=0, limit=100):
        with self._connect() as connection:
            total = connection.execute("SELECT COUNT(*) FROM download_quality_issues WHERE job_id=?", (job_id,)).fetchone()[0]
            rows = connection.execute("SELECT payload FROM download_quality_issues WHERE job_id=? ORDER BY id LIMIT ? OFFSET ?",
                                      (job_id, limit, offset)).fetchall()
        return {"total": total, "offset": offset, "limit": limit, "issues": [json.loads(r[0]) for r in rows]}

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
            area=None if row["location_latitude"] is not None else DownloadArea(
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
            source=row["source"],
            location=(Location(latitude=row["location_latitude"], longitude=row["location_longitude"])
                      if row["location_latitude"] is not None else None),
            spatial_mode="point" if row["location_latitude"] is not None else "area",
            retrieval_variables=json.loads(row["retrieval_variables"]),
            quality=QualityReport.model_validate_json(row["quality_json"]) if row["quality_json"] else None,
        )

    def _now(self) -> datetime:
        return datetime.now(UTC)
