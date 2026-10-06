from functools import lru_cache

from backend.app.providers.dependencies import get_climate_provider
from backend.app.services.climate_service import ClimateService


@lru_cache
def get_climate_service() -> ClimateService:
    return ClimateService(provider=get_climate_provider())
