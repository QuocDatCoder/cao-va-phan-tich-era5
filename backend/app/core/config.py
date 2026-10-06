from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    climate_provider: Literal["mock", "era5"] = "mock"
    data_dir: Path = Path("data")
    job_metadata_db_path: Path = Path("data/cache/jobs.sqlite3")

    cds_url: str | None = None
    cds_api_key: str | None = None

    era5_dataset: str = "reanalysis-era5-single-levels"
    era5_max_days_per_request: int = Field(default=7, ge=1, le=31)
    era5_point_buffer_degrees: float = Field(default=0.25, gt=0, le=2)

    @property
    def raw_era5_dir(self) -> Path:
        return self.data_dir / "raw" / "era5"


@lru_cache
def get_settings() -> Settings:
    return Settings()
