"""Explicit metadata for database-only responses; legacy schemas stay unchanged."""
from datetime import datetime
from typing import Literal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.app.domain.schemas import DataSource, Location, TimeSeriesResponse


class MissingRange(BaseModel):
    variable: str
    start: datetime
    end: datetime
    count: int
    time_basis: Literal["observation_time"] = "observation_time"


class StoredMetadata(BaseModel):
    source: DataSource
    dataset: str
    query_mode: Literal["db_only"] = "db_only"
    grid_location: Location | None
    expected_count: int
    available_count: int
    count: int
    coverage: float
    complete: bool
    status: Literal["complete", "partial", "missing"]
    missing_ranges: list[MissingRange]
    undefined_count: int = 0


class StoredTimeSeriesResponse(TimeSeriesResponse, StoredMetadata):
    pass


class StoredAnalysisResponse(StoredMetadata):
    """Shared coverage contract; operation-specific analysis fields remain additive."""
    model_config = ConfigDict(extra="allow")
    variable: str
    unit: str
    location: Location
    data: list[dict[str, Any]] = Field(default_factory=list)
