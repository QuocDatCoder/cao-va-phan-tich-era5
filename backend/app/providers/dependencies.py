from functools import lru_cache
from pathlib import Path
from threading import Lock

from backend.app.core.config import Settings, get_settings
from backend.app.core.user_context import current_user_id
from backend.app.core.user_settings import get_settings_for_user, settings_cache_key
from backend.app.domain.schemas import DataSource
from backend.app.providers.base import ClimateDataProvider
from backend.app.providers.era5_land_timeseries_provider import ERA5LandTimeseriesProvider
from backend.app.providers.era5_provider import ERA5ClimateProvider
from backend.app.providers.mock_provider import MockClimateProvider
from backend.app.providers.open_meteo import OpenMeteoProvider
from backend.app.storage.climate_repository import ClimateRepository
from backend.app.storage.shared_open_meteo_cache import SharedOpenMeteoCache, Singleflight


_OPEN_METEO_SINGLEFLIGHT = Singleflight()
_OPEN_METEO_CACHE_LOCK = Lock()


@lru_cache
def _get_shared_open_meteo_cache(db_path: Path) -> SharedOpenMeteoCache:
    return SharedOpenMeteoCache(db_path)


def _build_provider_for_source(
    source: DataSource, settings: Settings, *, base_settings: Settings,
) -> ClimateDataProvider:
    repository = ClimateRepository(settings.climate_data_db_path)
    if source == "era5":
        return ERA5ClimateProvider(settings=settings, repository=repository)
    if source == "land":
        return ERA5LandTimeseriesProvider(settings=settings, repository=repository)
    if source == "open_meteo":
        # lru_cache can otherwise run its initializer more than once on a concurrent miss.
        with _OPEN_METEO_CACHE_LOCK:
            shared_cache = _get_shared_open_meteo_cache(base_settings.data_dir / "cache" / "shared_open_meteo.sqlite3")
        return OpenMeteoProvider(settings, repository, shared_cache=shared_cache, singleflight=_OPEN_METEO_SINGLEFLIGHT)
    if source == "mock":
        return MockClimateProvider(data_dir=settings.data_dir, repository=repository)
    raise ValueError("Unknown data source")


_source_provider_cache: dict[tuple[DataSource, int | None, str], ClimateDataProvider] = {}


def get_provider_for_source(
    source: DataSource, *, user_id: int | None = None
) -> ClimateDataProvider:
    if user_id is None:
        user_id = current_user_id.get()
    base_settings = get_settings()
    settings_key = settings_cache_key(user_id, base_settings)
    cache_key = (source, user_id, settings_key[1])
    if cache_key not in _source_provider_cache:
        _source_provider_cache[cache_key] = _build_provider_for_source(
            source, get_settings_for_user(user_id, base_settings), base_settings=base_settings,
        )
    return _source_provider_cache[cache_key]


setattr(get_provider_for_source, "cache_clear", _source_provider_cache.clear)


@lru_cache
def get_climate_provider() -> ClimateDataProvider:
    settings = get_settings()
    if settings.climate_provider == "open_meteo":
        return _build_provider_for_source("open_meteo", settings, base_settings=settings)
    if settings.climate_provider == "era5":
        return ERA5ClimateProvider(
            settings=settings,
            repository=ClimateRepository(settings.climate_data_db_path),
        )
    if settings.climate_provider == "era5_land_timeseries":
        return ERA5LandTimeseriesProvider(
            settings=settings,
            repository=ClimateRepository(settings.climate_data_db_path),
        )
    return MockClimateProvider(data_dir=settings.data_dir)


@lru_cache
def _get_climate_provider_for_user(
    user_id: int | None, settings_key: tuple[int | None, str]
) -> ClimateDataProvider:
    base_settings = get_settings()
    settings = get_settings_for_user(user_id, base_settings)
    if settings.climate_provider == "open_meteo":
        return _build_provider_for_source("open_meteo", settings, base_settings=base_settings)
    if settings.climate_provider == "era5":
        return ERA5ClimateProvider(
            settings=settings,
            repository=ClimateRepository(settings.climate_data_db_path),
        )
    if settings.climate_provider == "era5_land_timeseries":
        return ERA5LandTimeseriesProvider(
            settings=settings,
            repository=ClimateRepository(settings.climate_data_db_path),
        )
    if user_id is None and not settings.auth_enabled:
        return MockClimateProvider(data_dir=settings.data_dir)
    return MockClimateProvider(
        data_dir=settings.data_dir,
        repository=ClimateRepository(settings.climate_data_db_path),
    )


def get_climate_provider_for_user(user_id: int | None) -> ClimateDataProvider:
    return _get_climate_provider_for_user(user_id, settings_cache_key(user_id))
