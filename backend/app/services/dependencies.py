from functools import lru_cache

from backend.app.core.config import get_settings
from backend.app.providers.dependencies import get_climate_provider
from backend.app.services.climate_analysis_service import ClimateAnalysisService
from backend.app.services.climate_service import ClimateService
from backend.app.services.download_job_service import DownloadJobService
from backend.app.services.synchronization_scheduler import SynchronizationScheduler
from backend.app.services.synchronization_service import (
    LatestAvailableDateProvider,
    SynchronizationService,
)
from backend.app.storage.job_repository import JobRepository
from backend.app.storage.synchronization_repository import SynchronizationRepository


@lru_cache
def get_climate_service() -> ClimateService:
    return ClimateService(provider=get_climate_provider(), max_query_days=get_settings().era5_max_query_days)


@lru_cache
def get_climate_analysis_service() -> ClimateAnalysisService:
    return ClimateAnalysisService(climate_service=get_climate_service())


@lru_cache
def get_job_repository() -> JobRepository:
    settings = get_settings()
    return JobRepository(db_path=settings.job_metadata_db_path)


@lru_cache
def get_download_job_service() -> DownloadJobService:
    return DownloadJobService(provider=get_climate_provider(), repository=get_job_repository())


@lru_cache
def get_synchronization_repository() -> SynchronizationRepository:
    settings = get_settings()
    return SynchronizationRepository(db_path=settings.job_metadata_db_path)


@lru_cache
def get_latest_available_date_provider() -> LatestAvailableDateProvider:
    settings = get_settings()
    return LatestAvailableDateProvider(lag_days=settings.era5_sync_availability_lag_days)


@lru_cache
def get_synchronization_service() -> SynchronizationService:
    settings = get_settings()
    return SynchronizationService(
        download_service=get_download_job_service(),
        repository=get_synchronization_repository(),
        latest_available_provider=get_latest_available_date_provider(),
        max_days_per_run=settings.era5_sync_max_days_per_run,
    )


@lru_cache
def get_synchronization_scheduler() -> SynchronizationScheduler:
    return SynchronizationScheduler(
        settings=get_settings(),
        service=get_synchronization_service(),
    )
