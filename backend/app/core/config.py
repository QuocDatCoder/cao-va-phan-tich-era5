from functools import lru_cache
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[3] / ".env",
        extra="ignore",
    )

    climate_provider: Literal["mock", "era5", "era5_land_timeseries", "open_meteo"] = "open_meteo"
    data_dir: Path = Path("data")
    auth_enabled: bool = True
    auth_db_path: Path = Path("data/cache/auth.sqlite3")
    admin_username: str = "admin"
    admin_password: str | None = Field(default=None, min_length=8, max_length=128)
    job_metadata_db_path: Path = Path("data/cache/jobs.sqlite3")
    climate_data_db_path: Path = Path("data/cache/climate.sqlite3")

    cds_max_attempts: int = Field(default=3, ge=1, le=5)
    cds_retry_base_seconds: float = Field(default=2, ge=0, le=30)
    cds_request_timeout_seconds: int = Field(default=60, ge=1, le=300)

    cds_url: str | None = None
    cds_api_key: str | None = None

    era5_dataset: str = "reanalysis-era5-single-levels"
    era5_max_days_per_request: int = Field(default=7, ge=1, le=31)
    era5_max_query_days: int = Field(default=366, ge=1, le=3660)
    era5_point_buffer_degrees: float = Field(default=0.25, gt=0, le=2)

    # Dataset ERA5-Land time-series (như slide môn học): một điểm, nhiều biến, xuất CSV.
    era5_land_dataset: str = "reanalysis-era5-land-timeseries"
    era5_land_max_days_per_request: int = Field(default=366, ge=1, le=3660)

    era5_sync_enabled: bool = False
    era5_sync_interval_hours: int = Field(default=6, ge=1)
    era5_sync_availability_lag_days: int = Field(default=5, ge=0)
    era5_sync_max_days_per_run: int = Field(default=7, ge=1, le=31)
    era5_sync_variable: str = "2m_temperature"
    era5_sync_start_date: date = date(2025, 1, 1)
    era5_sync_area_north: float = Field(default=11.0, ge=-90, le=90)
    era5_sync_area_west: float = Field(default=106.0, ge=-180, le=180)
    era5_sync_area_south: float = Field(default=10.0, ge=-90, le=90)
    era5_sync_area_east: float = Field(default=107.0, ge=-180, le=180)
    era5_sync_frequency: Literal["hourly"] = "hourly"

    @property
    def raw_era5_dir(self) -> Path:
        return self.data_dir / "raw" / "era5"


@lru_cache
def get_settings() -> Settings:
    return Settings()
