from functools import lru_cache

from backend.app.core.config import get_settings
from backend.app.providers.base import ClimateDataProvider
from backend.app.providers.era5_provider import ERA5ClimateProvider
from backend.app.providers.mock_provider import MockClimateProvider


@lru_cache
def get_climate_provider() -> ClimateDataProvider:
    settings = get_settings()
    if settings.climate_provider == "era5":
        return ERA5ClimateProvider(settings=settings)
    return MockClimateProvider()
