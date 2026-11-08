from functools import lru_cache

from backend.app.core.config import get_settings
from backend.app.providers.base import ClimateDataProvider
from backend.app.providers.era5_land_timeseries_provider import ERA5LandTimeseriesProvider
from backend.app.providers.era5_provider import ERA5ClimateProvider
from backend.app.providers.mock_provider import MockClimateProvider
from backend.app.storage.climate_repository import ClimateRepository


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