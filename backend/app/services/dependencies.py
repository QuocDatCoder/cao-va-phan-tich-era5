from functools import lru_cache
from pathlib import Path
from functools import partial
import logging

from backend.app.core.config import Settings, get_settings
from backend.app.core.user_context import current_user_id
from backend.app.core.user_settings import get_settings_for_user, settings_cache_key
from backend.app.domain.schemas import SynchronizationStatus
from backend.app.services.auth_service import AuthService
from backend.app.storage.auth_repository import AuthRepository
from backend.app.providers.dependencies import get_climate_provider_for_user, get_provider_for_source
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
from backend.app.storage.climate_repository import ClimateRepository
from backend.app.services.stored_climate_service import StoredClimateService

logger = logging.getLogger(__name__)


@lru_cache
def _get_auth_repository(db_path: Path) -> AuthRepository:
    return AuthRepository(db_path)


def get_auth_service() -> AuthService:
    return AuthService(_get_auth_repository(get_settings().auth_db_path))


@lru_cache
def _get_stored_climate_service(user_id: int | None, settings_key: tuple[int | None, str]) -> StoredClimateService:
    settings = get_settings_for_user(user_id)
    return StoredClimateService(ClimateRepository(settings.climate_data_db_path, read_only=True), settings)


def get_stored_climate_service() -> StoredClimateService:
    user_id = current_user_id.get()
    return _get_stored_climate_service(user_id, settings_cache_key(user_id))


@lru_cache
def _get_climate_service(user_id: int | None, settings_key: tuple[int | None, str]) -> ClimateService:
    settings = get_settings_for_user(user_id)
    return ClimateService(
        provider=get_climate_provider_for_user(user_id),
        max_query_days=settings.era5_max_query_days,
    )


def get_climate_service() -> ClimateService:
    user_id = current_user_id.get()
    return _get_climate_service(user_id, settings_cache_key(user_id))


@lru_cache
def _get_climate_analysis_service(
    user_id: int | None, settings_key: tuple[int | None, str]
) -> ClimateAnalysisService:
    return ClimateAnalysisService(
        climate_service=_get_climate_service(user_id, settings_key)
    )


def get_climate_analysis_service() -> ClimateAnalysisService:
    user_id = current_user_id.get()
    return _get_climate_analysis_service(user_id, settings_cache_key(user_id))


@lru_cache
def _get_job_repository(user_id: int | None, settings_key: tuple[int | None, str]) -> JobRepository:
    settings = get_settings_for_user(user_id)
    return JobRepository(db_path=settings.job_metadata_db_path)


def get_job_repository() -> JobRepository:
    user_id = current_user_id.get()
    return _get_job_repository(user_id, settings_cache_key(user_id))


@lru_cache
def _get_download_job_service(
    user_id: int | None, settings_key: tuple[int | None, str]
) -> DownloadJobService:
    settings = get_settings_for_user(user_id)
    return DownloadJobService(
        provider=get_climate_provider_for_user(user_id),
        repository=_get_job_repository(user_id, settings_key),
        provider_resolver=partial(get_provider_for_source, user_id=user_id),
        settings=settings,
    )


def get_download_job_service() -> DownloadJobService:
    user_id = current_user_id.get()
    return _get_download_job_service(user_id, settings_cache_key(user_id))


@lru_cache
def _get_synchronization_repository(
    user_id: int | None, settings_key: tuple[int | None, str]
) -> SynchronizationRepository:
    settings = get_settings_for_user(user_id)
    return SynchronizationRepository(db_path=settings.job_metadata_db_path)


def get_synchronization_repository() -> SynchronizationRepository:
    user_id = current_user_id.get()
    return _get_synchronization_repository(user_id, settings_cache_key(user_id))


@lru_cache
def get_latest_available_date_provider() -> LatestAvailableDateProvider:
    settings = get_settings()
    return LatestAvailableDateProvider(lag_days=settings.era5_sync_availability_lag_days)


@lru_cache
def _get_synchronization_service(
    user_id: int | None, settings_key: tuple[int | None, str]
) -> SynchronizationService:
    settings = get_settings_for_user(user_id)
    return SynchronizationService(
        download_service=_get_download_job_service(user_id, settings_key),
        repository=_get_synchronization_repository(user_id, settings_key),
        latest_available_provider=get_latest_available_date_provider(),
        max_days_per_run=settings.era5_sync_max_days_per_run,
    )


def get_synchronization_service() -> SynchronizationService:
    user_id = current_user_id.get()
    return _get_synchronization_service(user_id, settings_cache_key(user_id))


def _run_synchronization_for_all_users(settings: Settings) -> None:
    if not settings.auth_db_path.exists():
        return
    user_ids = _get_auth_repository(settings.auth_db_path).list_user_ids()
    for user_id in user_ids:
        try:
            user_settings = get_settings_for_user(user_id)
            service = _get_synchronization_service(
                user_id, settings_cache_key(user_id)
            )
            state = service.synchronize_configured(user_settings)
            if state.status == SynchronizationStatus.QUEUED:
                service.run_synchronization(state.sync_id)
        except Exception:
            logger.exception(
                "Scheduled ERA5 synchronization failed for account id %s",
                user_id,
            )


@lru_cache
def get_synchronization_scheduler() -> SynchronizationScheduler:
    settings = get_settings()
    if settings.auth_enabled:
        return SynchronizationScheduler(
            settings=settings,
            service=None,
            account_runner=_run_synchronization_for_all_users,
        )
    return SynchronizationScheduler(
        settings=settings,
        service=get_synchronization_service(),
    )
