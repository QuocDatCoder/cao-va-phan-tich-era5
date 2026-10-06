from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Location(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class ClimatePoint(BaseModel):
    time: datetime
    value: float


class TimeSeriesRequest(BaseModel):
    variable: str = Field(min_length=1)
    location: Location
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def validate_date_range(self) -> "TimeSeriesRequest":
        if self.end < self.start:
            raise ValueError("end must be greater than or equal to start")
        return self


class TimeSeriesResponse(BaseModel):
    variable: str
    unit: str
    location: Location
    data: list[ClimatePoint]


class StatisticsResponse(BaseModel):
    variable: str
    unit: str
    location: Location
    mean: float
    min: float
    max: float
    std: float
    count: int


class TrendResponse(BaseModel):
    variable: str
    unit: str
    location: Location
    slope: float
    direction: Literal["increasing", "decreasing", "stable"]
    period: str
    count: int


class AnomalyPoint(BaseModel):
    time: datetime
    value: float
    anomaly: float


class AnomalyResponse(BaseModel):
    variable: str
    unit: str
    location: Location
    baseline_start: datetime
    baseline_end: datetime
    baseline_mean: float
    data: list[AnomalyPoint]


class ComparePeriodSummary(BaseModel):
    start: datetime
    end: datetime
    mean: float
    count: int


class CompareResponse(BaseModel):
    variable: str
    unit: str
    location: Location
    period_a: ComparePeriodSummary
    period_b: ComparePeriodSummary
    difference: float


class HeatmapCell(BaseModel):
    year: int
    month: int
    value: float
    count: int


class HeatmapResponse(BaseModel):
    variable: str
    unit: str
    location: Location
    data: list[HeatmapCell]


class MapPoint(BaseModel):
    latitude: float
    longitude: float
    value: float


class MapResponse(BaseModel):
    variable: str
    unit: str
    data: list[MapPoint]


class DownloadArea(BaseModel):
    north: float = Field(ge=-90, le=90)
    west: float = Field(ge=-180, le=180)
    south: float = Field(ge=-90, le=90)
    east: float = Field(ge=-180, le=180)

    @model_validator(mode="after")
    def validate_bounds(self) -> "DownloadArea":
        if self.north < self.south:
            raise ValueError("north must be greater than or equal to south")
        if self.east < self.west:
            raise ValueError("east must be greater than or equal to west")
        return self


class DownloadJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DownloadRequest(BaseModel):
    variable: str = Field(min_length=1)
    start_date: date
    end_date: date
    area: DownloadArea
    frequency: Literal["hourly"] = "hourly"

    @model_validator(mode="after")
    def validate_date_range(self) -> "DownloadRequest":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be greater than or equal to start_date")
        return self


class DownloadCreateResponse(BaseModel):
    job_id: str
    status: DownloadJobStatus


class DownloadArtifact(BaseModel):
    file_path: Path
    storage_path: str
    dataset: str
    variable: str


class DownloadJob(BaseModel):
    job_id: str
    variable: str
    start_date: date
    end_date: date
    area: DownloadArea
    frequency: Literal["hourly"]
    status: DownloadJobStatus
    progress: int = Field(ge=0, le=100)
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    dataset: str | None = None
    file_path: str | None = None
    error: str | None = None
