from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError

from backend.app.domain.schemas import (
    AnomalyResponse,
    CompareResponse,
    HeatmapResponse,
    Location,
    MapResponse,
    StatisticsResponse,
    TimeSeriesRequest,
    TimeSeriesResponse,
    TrendResponse,
)
from backend.app.services.climate_analysis_service import ClimateAnalysisService
from backend.app.services.climate_service import ClimateService
from backend.app.services.dependencies import get_climate_analysis_service, get_climate_service

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


def build_timeseries_request(
    variable: str,
    latitude: float,
    longitude: float,
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


@router.get("/statistics", response_model=StatisticsResponse)
def get_statistics(
    request: Annotated[TimeSeriesRequest, Depends(get_timeseries_request)],
    service: Annotated[ClimateAnalysisService, Depends(get_climate_analysis_service)],
) -> StatisticsResponse:
    return service.statistics(request)


@router.get("/trend", response_model=TrendResponse)
def get_trend(
    request: Annotated[TimeSeriesRequest, Depends(get_timeseries_request)],
    service: Annotated[ClimateAnalysisService, Depends(get_climate_analysis_service)],
) -> TrendResponse:
    return service.trend(request)


@router.get("/anomaly", response_model=AnomalyResponse)
def get_anomaly(
    variable: Annotated[str, Query(min_length=1)],
    latitude: Annotated[float, Query(ge=-90, le=90)],
    longitude: Annotated[float, Query(ge=-180, le=180)],
    start: datetime,
    end: datetime,
    baseline_start: datetime,
    baseline_end: datetime,
    service: Annotated[ClimateAnalysisService, Depends(get_climate_analysis_service)],
) -> AnomalyResponse:
    request = build_timeseries_request(variable, latitude, longitude, start, end)
    baseline_request = build_timeseries_request(
        variable,
        latitude,
        longitude,
        baseline_start,
        baseline_end,
    )
    return service.anomaly(request=request, baseline_request=baseline_request)


@router.get("/compare", response_model=CompareResponse)
def get_compare(
    variable: Annotated[str, Query(min_length=1)],
    latitude: Annotated[float, Query(ge=-90, le=90)],
    longitude: Annotated[float, Query(ge=-180, le=180)],
    period_a_start: datetime,
    period_a_end: datetime,
    period_b_start: datetime,
    period_b_end: datetime,
    service: Annotated[ClimateAnalysisService, Depends(get_climate_analysis_service)],
) -> CompareResponse:
    period_a_request = build_timeseries_request(
        variable,
        latitude,
        longitude,
        period_a_start,
        period_a_end,
    )
    period_b_request = build_timeseries_request(
        variable,
        latitude,
        longitude,
        period_b_start,
        period_b_end,
    )
    return service.compare(
        period_a_request=period_a_request,
        period_b_request=period_b_request,
    )


@router.get("/heatmap", response_model=HeatmapResponse)
def get_heatmap(
    request: Annotated[TimeSeriesRequest, Depends(get_timeseries_request)],
    service: Annotated[ClimateAnalysisService, Depends(get_climate_analysis_service)],
) -> HeatmapResponse:
    return service.heatmap(request)


@router.get("/map", response_model=MapResponse)
def get_map(
    request: Annotated[TimeSeriesRequest, Depends(get_timeseries_request)],
    service: Annotated[ClimateAnalysisService, Depends(get_climate_analysis_service)],
) -> MapResponse:
    return service.map(request)
