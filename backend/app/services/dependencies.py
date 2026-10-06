from functools import lru_cache

from backend.app.core.config import get_settings
from backend.app.providers.dependencies import get_climate_provider
from backend.app.services.climate_service import ClimateService
from backend.app.services.download_job_service import DownloadJobService
from backend.app.storage.job_repository import JobRepository


@lru_cache
def get_climate_service() -> ClimateService:
    return ClimateService(provider=get_climate_provider())


@lru_cache
def get_job_repository() -> JobRepository:
    settings = get_settings()
    return JobRepository(db_path=settings.job_metadata_db_path)


@lru_cache
def get_download_job_service() -> DownloadJobService:
    return DownloadJobService(provider=get_climate_provider(), repository=get_job_repository())
