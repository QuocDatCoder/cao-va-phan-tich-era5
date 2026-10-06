from uuid import uuid4

from backend.app.domain.schemas import (
    DownloadCreateResponse,
    DownloadJob,
    DownloadJobStatus,
    DownloadRequest,
)
from backend.app.providers.base import ClimateDataProvider
from backend.app.storage.job_repository import JobRepository


class JobNotFoundError(Exception):
    pass


class JobPersistenceError(Exception):
    pass


class DownloadJobService:
    def __init__(self, provider: ClimateDataProvider, repository: JobRepository) -> None:
        self._provider = provider
        self._repository = repository

    def create_job(self, request: DownloadRequest) -> DownloadCreateResponse:
        job = self._repository.create(job_id=str(uuid4()), request=request)
        return DownloadCreateResponse(job_id=job.job_id, status=job.status)

    def get_job(self, job_id: str) -> DownloadJob:
        job = self._repository.get(job_id)
        if job is None:
            raise JobNotFoundError(f"Download job '{job_id}' was not found.")
        return job

    def run_job(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if job.status != DownloadJobStatus.QUEUED:
            return

        try:
            self._repository.mark_running(job_id)
        except Exception as exc:
            raise JobPersistenceError("Download job could not be marked running.") from exc

        try:
            artifact = self._provider.download_area(
                variable=job.variable,
                area=job.area,
                start_date=job.start_date,
                end_date=job.end_date,
                frequency=job.frequency,
            )
        except Exception as exc:
            self._mark_failed_or_raise(job_id=job_id, error=self._safe_error(exc))
            return

        try:
            self._repository.mark_completed(job_id=job_id, artifact=artifact)
        except Exception as exc:
            self._mark_failed_or_raise(
                job_id=job_id,
                error=f"Download job could not persist completion metadata: {self._safe_error(exc)}",
            )

    def _safe_error(self, exc: Exception) -> str:
        message = str(exc).strip()
        if not message:
            return exc.__class__.__name__
        return message

    def _mark_failed_or_raise(self, job_id: str, error: str) -> None:
        try:
            self._repository.mark_failed(job_id=job_id, error=error)
        except Exception as exc:
            raise JobPersistenceError("Download job failure status could not be persisted.") from exc
