from datetime import datetime

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
