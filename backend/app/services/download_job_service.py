from uuid import uuid4
from threading import RLock
from collections.abc import Callable
from time import monotonic

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
from backend.app.storage.collection_repository import CollectionRepository, current_collection, recover_abandoned
from backend.app.domain.cancellation import current_cancellation, check_cancelled


# One collection executes at a time in this process; this is not a distributed queue.
_JOB_LOCK = RLock()
_CANCEL_LOCK = RLock()


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
        self.collections = CollectionRepository(settings.climate_data_db_path) if settings else None
        if self.collections:
            recover_abandoned(self.collections, self._repository)

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
                                 if name not in DERIVED_WIND_UNITS}) if source == "land" or (source == "era5" and request.all_variables) else
                         list(WIND_COMPONENTS) if variable in DERIVED_WIND_UNITS else [variable])
            job = self._repository.create(
                job_id=str(uuid4()), request=request, dataset=source_dataset(source, self._settings),
                retrieval_variables=variables,
            )
        else:
            if request.source is not None:
                raise ValueError("Source-aware collection requires configured provider resolution.")
            job = self._repository.create(job_id=str(uuid4()), request=request)
        if self.collections:
            self.collections.create(job, request, variable)
        return DownloadCreateResponse(job_id=job.job_id, status=job.status,
                                      collection_id=job.job_id if self.collections else None)

    def get_job(self, job_id: str) -> DownloadJob:
        job = self._repository.get(job_id)
        if job is None:
            raise JobNotFoundError(f"Download job '{job_id}' was not found.")
        entry = self.collections.get(job_id) if self.collections else None
        if entry:
            job.collection_id = job_id
            job.collection_name = entry['metadata'].get('name')
        return job

    def get_quality(self, job_id: str, offset: int = 0, limit: int = 100):
        job = self.get_job(job_id)
        return {"job_id": job_id, "quality": job.quality,
                **self._repository.quality_issues(job_id, offset, limit)}

    def list_jobs(self, limit=100, offset=0):
        return [self.get_job(j.job_id) for j in self._repository.list_jobs(limit, offset)]

    def run_job(self, job_id: str) -> None:
        with _JOB_LOCK:
            last_check, cancelled = float('-inf'), False
            def is_cancelled():
                nonlocal last_check, cancelled
                now = monotonic()
                if now-last_check >= .1:
                    stored_job = self._repository.get(job_id)
                    cancelled = stored_job is None or stored_job.status in {'cancelling','cancelled'}
                    last_check = now
                return cancelled
            token = current_cancellation.set(is_cancelled)
            try:
                self._run_job_locked(job_id)
            except JobNotFoundError:
                # A queued background callback may outlive cancellation/deletion.
                return
            finally:
                try:
                    # Cover cancellation racing with the success/error finalizer.
                    stored_job = self._repository.get(job_id)
                    if stored_job is not None and stored_job.status == 'cancelling':
                        self._finish_cancelled(job_id)
                finally:
                    current_cancellation.reset(token)

    def cancel_job(self, job_id):
        with _CANCEL_LOCK:
            return self._cancel_job_locked(job_id)

    def _cancel_job_locked(self, job_id):
        self.get_job(job_id)
        job = self._repository.request_cancel(job_id)
        if self.collections and job.status in {'cancelled', 'cancelling'}:
            self.collections.set_cancel_state(job_id, job.status)
            if job.status == 'cancelled':
                self._finish_cancelled(job_id)
        return self.get_job(job_id)

    def _finish_cancelled(self, job_id):
        with _CANCEL_LOCK:
            self._finish_cancelled_locked(job_id)

    def _finish_cancelled_locked(self, job_id):
        if self.collections:
            self._finish_collection(job_id, None, error='Cancelled by user; previously imported data retained.')
            self.collections.set_cancel_state(job_id, 'cancelled')
        self._repository.mark_cancelled(job_id)

    def _run_job_locked(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if job.status != DownloadJobStatus.QUEUED:
            return

        try:
            if hasattr(self._repository, "claim_running"):
                if not self._repository.claim_running(job_id):
                    return
            else:  # Compatibility with repository adapters used by legacy callers.
                self._repository.mark_running(job_id)
        except Exception as exc:
            raise JobPersistenceError("Download job could not be marked running.") from exc

        snapshot_token = current_collection.set((job_id, job.dataset) if job.collection_id else None)
        if job.collection_id:
            self.collections.running(job_id)
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
                bundle = job.source == 'era5' and len(job.retrieval_variables) > len(WIND_COMPONENTS)
                variables = job.retrieval_variables if bundle else [job.variable]
                artifact = None
                for variable in variables:
                    check_cancelled()
                    downloaded = download(variable=variable, **spatial, start_date=job.start_date,
                                          end_date=job.end_date, frequency=job.frequency)
                    check_cancelled()
                    if job.source is not None and downloaded.dataset != job.dataset:
                        raise ValueError("Provider returned a different dataset than the recorded collection source.")
                    if artifact is None or variable == job.variable:
                        artifact = downloaded
                    if job.collection_id:
                        self.collections.artifact(job_id, downloaded.file_path)
                if collector is not None and (collector.report.rows_read or collector.report.issue_count) and not collector.report.imported_values:
                    raise ValueError("No valid observations were imported; original files were preserved.")
                if job.collection_id:
                    summary = self._finish_collection(job_id, collector)
                    if bundle and any(not summary['counts_by_variable'].get(v) for v in variables):
                        raise ValueError('ERA5 collection is missing one or more requested variables.')
                    if summary['available_count'] == 0:
                        raise ValueError('No valid observations for the requested variable and interval; additional variables do not count as success.')
                    if summary['missing_count']:
                        collector.issue('incomplete_requested_coverage', 'The requested collection interval is incomplete.')
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
            if self.get_job(job_id).status in {'cancelling', 'cancelled'}:
                self._finish_cancelled(job_id)
                return
            if job.collection_id:
                self._finish_collection(job_id, None, error=self._safe_error(exc))
            self._mark_failed_or_raise(job_id=job_id, error=self._safe_error(exc))
            return
        finally:
            current_collection.reset(snapshot_token)

        if job.collection_id:
            self._finish_collection(job_id, None)

        try:
            self._repository.mark_completed(job_id=job_id, artifact=artifact)
            if self.get_job(job_id).status in {'cancelling','cancelled'}:
                self._finish_cancelled(job_id)
        except Exception as exc:
            self._mark_failed_or_raise(
                job_id=job_id,
                error=f"Download job could not persist completion metadata: {self._safe_error(exc)}",
            )

    def _finish_collection(self, job_id, collector, error=None):
        if collector:
            self._repository.save_quality(job_id, collector.finish(failed=bool(error)))
        job = self._repository.get(job_id)
        issues, offset = [], 0
        while True:
            page = self._repository.quality_issues(job_id, offset, 500)
            issues.extend(page['issues'])
            if len(issues) >= page['total']:
                break
            offset = len(issues)
        summary = self.collections.finish(job_id, issues, job.quality.model_dump(mode='json') if job.quality else None, error)
        if job.quality and summary and job.quality.imported_values != summary['imported_values']:
            job.quality.imported_values = summary['imported_values']
            self._repository.save_quality(job_id, job.quality)
            summary = self.collections.finish(job_id, issues, job.quality.model_dump(mode='json'), error)
        return summary

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
