from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError

from backend.app.domain.schemas import Location, TimeSeriesRequest, TimeSeriesResponse
from backend.app.services.climate_service import ClimateService
from backend.app.services.dependencies import get_climate_service

router = APIRouter(prefix="/api/v1/climate", tags=["climate"])


def get_timeseries_request(
    variable: Annotated[str, Query(min_length=1)],
    latitude: Annotated[float, Query(ge=-90, le=90)],
    longitude: Annotated[float, Query(ge=-180, le=180)],
    start: datetime,
    end: datetime,
) -> TimeSeriesRequest:
    try:
        return TimeSeriesRequest(
            variable=variable,
            location=Location(latitude=latitude, longitude=longitude),
            start=start,
            end=end,
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=[
                {
                    "loc": ["query", "end"],
                    "msg": "end must be greater than or equal to start",
                    "type": "value_error",
                }
            ],
        ) from exc


@router.get("/timeseries", response_model=TimeSeriesResponse)
def get_timeseries(
    request: Annotated[TimeSeriesRequest, Depends(get_timeseries_request)],
    service: Annotated[ClimateService, Depends(get_climate_service)],
) -> TimeSeriesResponse:
    return service.get_timeseries(request)
