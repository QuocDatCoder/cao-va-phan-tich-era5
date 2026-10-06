from functools import lru_cache

from backend.app.providers.base import ClimateDataProvider
from backend.app.providers.mock_provider import MockClimateProvider


@lru_cache
def get_climate_provider() -> ClimateDataProvider:
    return MockClimateProvider()
