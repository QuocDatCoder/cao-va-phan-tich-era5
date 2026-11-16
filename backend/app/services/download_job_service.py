from uuid import uuid4
from collections.abc import Callable

from backend.app.domain.schemas import (
    DownloadCreateResponse,
    DownloadJob,
    DownloadJobStatus,
    DownloadRequest,
    DataSource,
    Location,
)
from backend.app.core.config import Settings
from backend.app.domain.sources import configured_source, source_dataset
from backend.app.providers.era5_provider import ERA5_VARIABLES
from backend.app.domain.wind import DERIVED_WIND_UNITS, WIND_COMPONENTS
from backend.app.providers.errors import UnsupportedVariableError
from backend.app.providers.base import ClimateDataProvider
from backend.app.storage.job_repository import JobRepository
from backend.app.domain.quality import QualityCollector, current_quality


class JobNotFoundError(Exception):
    pass


class JobPersistenceError(Exception):
    pass


class DownloadJobService:
    def __init__(self, provider: ClimateDataProvider, repository: JobRepository, *,
                 provider_resolver: Callable[[DataSource], ClimateDataProvider] | None = None,
                 settings: Settings | None = None) -> None:
        self._provider = provider
        self._repository = repository
        self._provider_resolver = provider_resolver
        self._settings = settings

    def create_job(self, request: DownloadRequest) -> DownloadCreateResponse:
        if self._settings is not None:
            source = request.source or configured_source(self._settings)
            updates = {"source": source}
            if source == "land" and request.location is None:
                # Preserve legacy area callers, but persist the true point semantics.
                updates.update(location=Location(
                    latitude=round((request.area.north + request.area.south) / 2, 4),
                    longitude=round((request.area.west + request.area.east) / 2, 4)), area=None)
            request = DownloadRequest(**{**request.model_dump(), **updates})
            if request.variable not in ERA5_VARIABLES:
                raise UnsupportedVariableError(f"Unsupported variable '{request.variable}'.")
            variable = ERA5_VARIABLES[request.variable].cds_name
            variables = (sorted({spec.cds_name for name, spec in ERA5_VARIABLES.items()
                                 if name not in DERIVED_WIND_UNITS}) if source == "land" else
                         list(WIND_COMPONENTS) if variable in DERIVED_WIND_UNITS else [variable])
            job = self._repository.create(
                job_id=str(uuid4()), request=request, dataset=source_dataset(source, self._settings),
                retrieval_variables=variables,
            )
        else:
            if request.source is not None:
                raise ValueError("Source-aware collection requires configured provider resolution.")
            job = self._repository.create(job_id=str(uuid4()), request=request)
        return DownloadCreateResponse(job_id=job.job_id, status=job.status)

    def get_job(self, job_id: str) -> DownloadJob:
        job = self._repository.get(job_id)
        if job is None:
            raise JobNotFoundError(f"Download job '{job_id}' was not found.")
        return job

    def get_quality(self, job_id: str, offset: int = 0, limit: int = 100):
        job = self.get_job(job_id)
        return {"job_id": job_id, "quality": job.quality,
                **self._repository.quality_issues(job_id, offset, limit)}

    def run_job(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if job.status != DownloadJobStatus.QUEUED:
            return

        try:
            self._repository.mark_running(job_id)
        except Exception as exc:
            raise JobPersistenceError("Download job could not be marked running.") from exc

        try:
            if self._provider_resolver is not None:
                if job.source is None:
                    raise ValueError("This legacy job has no recorded source; create a new download job.")
                if self._settings is not None and job.dataset != source_dataset(job.source, self._settings):
                    raise ValueError("Dataset configuration changed since this job was created; create a new job.")
                provider = self._provider_resolver(job.source)
            else:
                provider = self._provider
            spatial = {"location": job.location} if job.location is not None else {"area": job.area}
            download = provider.download_point if job.location is not None else provider.download_area
            collector = (QualityCollector(lambda issues: self._repository.add_quality_issues(job_id, issues))
                         if hasattr(self._repository, "save_quality") else None)
            token = current_quality.set(collector)
            try:
                artifact = download(variable=job.variable, **spatial, start_date=job.start_date,
                                    end_date=job.end_date, frequency=job.frequency)
                if collector is not None and (collector.report.rows_read or collector.report.issue_count) and not collector.report.imported_values:
                    raise ValueError("No valid observations were imported; original files were preserved.")
            except Exception:
                if collector is not None:
                    self._repository.save_quality(job_id, collector.finish(failed=True))
                raise
            else:
                if collector is not None and (collector.report.rows_read or collector.report.issue_count):
                    self._repository.save_quality(job_id, collector.finish())
            finally:
                current_quality.reset(token)
            if job.source is not None and artifact.dataset != job.dataset:
                raise ValueError("Provider returned a different dataset than the recorded collection source.")
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
