from functools import lru_cache

from backend.app.core.config import get_settings
from backend.app.domain.schemas import DataSource
from backend.app.providers.base import ClimateDataProvider
from backend.app.providers.era5_land_timeseries_provider import ERA5LandTimeseriesProvider
from backend.app.providers.era5_provider import ERA5ClimateProvider
from backend.app.providers.mock_provider import MockClimateProvider
from backend.app.storage.climate_repository import ClimateRepository


@lru_cache
def get_provider_for_source(source: DataSource) -> ClimateDataProvider:
    """Per-source factory: selecting a source never mutates process settings."""
    settings = get_settings()
    repository = ClimateRepository(settings.climate_data_db_path)
    if source == "era5":
        return ERA5ClimateProvider(settings=settings, repository=repository)
    if source == "land":
        return ERA5LandTimeseriesProvider(settings=settings, repository=repository)
    if source == "mock":
        return MockClimateProvider(data_dir=settings.data_dir, repository=repository)
    raise ValueError("Unknown data source")


@lru_cache
def get_climate_provider() -> ClimateDataProvider:
    settings = get_settings()
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
