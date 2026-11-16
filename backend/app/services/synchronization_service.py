from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
import json

from backend.app.core.config import Settings
from backend.app.domain.schemas import (
    DownloadArea,
    DownloadJob,
    DownloadJobStatus,
    DownloadRequest,
    SynchronizationRequest,
    SynchronizationState,
    SynchronizationStatus,
)
from backend.app.services.download_job_service import DownloadJobService, JobNotFoundError
from backend.app.storage.synchronization_repository import SynchronizationRepository


class SynchronizationError(Exception):
    pass


class SynchronizationNotFoundError(Exception):
    pass


class LatestAvailableDateProvider:
    def __init__(self, lag_days: int) -> None:
        self._lag_days = lag_days

    def latest_available_date(self) -> date:
        return datetime.now(UTC).date() - timedelta(days=self._lag_days)


class SynchronizationService:
    def __init__(
        self,
        download_service: DownloadJobService,
        repository: SynchronizationRepository,
        latest_available_provider: LatestAvailableDateProvider,
        max_days_per_run: int = 7,
    ) -> None:
        self._download_service = download_service
        self._repository = repository
        self._latest_available_provider = latest_available_provider
        self._max_days_per_run = max_days_per_run

    def synchronize(self, request: SynchronizationRequest) -> SynchronizationState:
        latest_available_date = (
            request.latest_available_date or self._latest_available_provider.latest_available_date()
        )
        if latest_available_date < request.start_date:
            raise SynchronizationError(
                "latest available ERA5 date is earlier than the requested sync start date"
            )

        sync_id = self._sync_id(request)
        current = self._repository.get(sync_id)
        if current and current.status in {
            SynchronizationStatus.QUEUED,
            SynchronizationStatus.RUNNING,
        }:
            current = self._reconcile_in_flight_state(current)
            if current.status in {
                SynchronizationStatus.QUEUED,
                SynchronizationStatus.RUNNING,
                SynchronizationStatus.FAILED,
            }:
                return current

        if current and current.last_synced_date:
            if current.last_synced_date >= latest_available_date:
                return self._repository.mark_up_to_date(
                    sync_id=sync_id,
                    request=request,
                    latest_available_date=latest_available_date,
                    last_synced_date=current.last_synced_date,
                )
            job_start_date = current.last_synced_date + timedelta(days=1)
        else:
            job_start_date = request.start_date

        if job_start_date > latest_available_date:
            return self._repository.mark_up_to_date(
                sync_id=sync_id,
                request=request,
                latest_available_date=latest_available_date,
                last_synced_date=current.last_synced_date if current else None,
            )

        job_end_date = min(
            latest_available_date,
            job_start_date + timedelta(days=self._max_days_per_run - 1),
        )

        download_request = DownloadRequest(
            variable=request.variable,
            start_date=job_start_date,
            end_date=job_end_date,
            area=request.area,
            frequency=request.frequency,
        )
        job = self._download_service.create_job(download_request)
        return self._repository.upsert_queued(
            sync_id=sync_id,
            request=request,
            latest_available_date=latest_available_date,
            job_start_date=job_start_date,
            job_end_date=job_end_date,
            job_id=job.job_id,
        )

    def run_synchronization(self, sync_id: str) -> SynchronizationState:
        state = self.get_state(sync_id)
        if state.status != SynchronizationStatus.QUEUED or state.job_id is None:
            return state

        self._repository.mark_running(sync_id)
        self._download_service.run_job(state.job_id)
        job = self._download_service.get_job(state.job_id)

        if job.status == DownloadJobStatus.COMPLETED:
            if job.quality is not None and not job.quality.complete:
                return self._mark_sync_failed(sync_id, "Collection contains quality warnings; synchronization coverage was not advanced.")
            completed = self._repository.mark_completed(
                sync_id=sync_id,
                last_synced_date=job.end_date,
            )
            if completed is None:
                raise SynchronizationError("Synchronization completion metadata was not persisted.")
            return completed

        if job.status == DownloadJobStatus.FAILED:
            failed = self._repository.mark_failed(
                sync_id=sync_id,
                error=job.error or "Download job failed.",
            )
            if failed is None:
                raise SynchronizationError("Synchronization failure metadata was not persisted.")
            return failed

        return self.get_state(sync_id)

    def _reconcile_in_flight_state(
        self,
        state: SynchronizationState,
    ) -> SynchronizationState:
        if state.job_id is None:
            return self._mark_sync_failed(
                sync_id=state.sync_id,
                error="Synchronization is missing its related download job.",
            )

        try:
            job = self._download_service.get_job(state.job_id)
        except JobNotFoundError:
            return self._mark_sync_failed(
                sync_id=state.sync_id,
                error=f"Related download job '{state.job_id}' was not found.",
            )

        if job.status in {DownloadJobStatus.QUEUED, DownloadJobStatus.RUNNING}:
            return state

        if job.status == DownloadJobStatus.COMPLETED:
            if job.quality is not None and not job.quality.complete:
                return self._mark_sync_failed(state.sync_id, "Collection contains quality warnings; synchronization coverage was not advanced.")
            return self._mark_sync_completed(sync_id=state.sync_id, job=job)

        return self._mark_sync_failed(
            sync_id=state.sync_id,
            error=job.error or f"Related download job finished with status '{job.status}'.",
        )

    def _mark_sync_completed(self, sync_id: str, job: DownloadJob) -> SynchronizationState:
        completed = self._repository.mark_completed(
            sync_id=sync_id,
            last_synced_date=job.end_date,
        )
        if completed is None:
            raise SynchronizationError("Synchronization completion metadata was not persisted.")
        return completed

    def _mark_sync_failed(self, sync_id: str, error: str) -> SynchronizationState:
        failed = self._repository.mark_failed(sync_id=sync_id, error=error)
        if failed is None:
            raise SynchronizationError("Synchronization failure metadata was not persisted.")
        return failed

    def get_state(self, sync_id: str) -> SynchronizationState:
        state = self._repository.get(sync_id)
        if state is None:
            raise SynchronizationNotFoundError(f"Synchronization '{sync_id}' was not found.")
        return state

    def list_states(self, limit: int = 100) -> list[SynchronizationState]:
        return self._repository.list_states(limit)

    def synchronize_configured(self, settings: Settings) -> SynchronizationState:
        return self.synchronize(
            SynchronizationRequest(
                variable=settings.era5_sync_variable,
                start_date=settings.era5_sync_start_date,
                area=DownloadArea(
                    north=settings.era5_sync_area_north,
                    west=settings.era5_sync_area_west,
                    south=settings.era5_sync_area_south,
                    east=settings.era5_sync_area_east,
                ),
                frequency=settings.era5_sync_frequency,
            )
        )

    def _sync_id(self, request: SynchronizationRequest) -> str:
        identity = {
            "variable": request.variable,
            "start_date": request.start_date.isoformat(),
            "area": {
                "north": round(request.area.north, 6),
                "west": round(request.area.west, 6),
                "south": round(request.area.south, 6),
                "east": round(request.area.east, 6),
            },
            "frequency": request.frequency,
        }
        encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return sha256(encoded).hexdigest()
