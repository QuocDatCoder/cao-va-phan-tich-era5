"""Canonical source identities shared by collection, storage and DB-only queries."""
from backend.app.domain.schemas import DataSource
from backend.app.core.config import Settings


OPEN_METEO_DATASET = "open-meteo-historical-forecast-gfs-global"
OPEN_METEO_BEST_MATCH_DATASET = "open-meteo-forecast-best-match"

def configured_source(settings: Settings) -> DataSource:
    return "land" if settings.climate_provider == "era5_land_timeseries" else settings.climate_provider


def source_dataset(source: DataSource, settings: Settings) -> str:
    if len({settings.era5_dataset, settings.era5_land_dataset, "mock"}) != 3:
        raise ValueError("ERA5, Land and mock dataset identities must be distinct.")
    if source == "land":
        return settings.era5_land_dataset
    if source == "era5":
        return settings.era5_dataset
    if source == "open_meteo":
        return OPEN_METEO_DATASET
    if source == "mock":
        return "mock"
    raise ValueError("Unknown data source")
