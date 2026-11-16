"""Explicit SQLite-only endpoints. Legacy /climate endpoints may still download."""
import csv
from io import StringIO
import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from backend.app.api.climate import get_timeseries_request
from backend.app.domain.schemas import DataSource, TimeSeriesRequest
from backend.app.domain.stored import StoredTimeSeriesResponse, StoredAnalysisResponse
from backend.app.services.dependencies import get_stored_climate_service
from backend.app.services.stored_climate_service import StoredClimateService

router = APIRouter(prefix="/api/v1/stored/climate", tags=["stored climate (SQLite only)"])
Request = Annotated[TimeSeriesRequest, Depends(get_timeseries_request)]
Service = Annotated[StoredClimateService, Depends(get_stored_climate_service)]
Source = Annotated[DataSource, Query(description="land=ERA5-Land point, era5=ERA5 global, mock=stored demo only")]


def _query(service, operation, source, request, **kwargs):
    try:
        return service.query(operation, source, request, **kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except sqlite3.Error as exc:
        raise HTTPException(status_code=503, detail="Unable to read the stored observations database.") from exc


@router.get("/timeseries", response_model=StoredTimeSeriesResponse)
def timeseries(source: Source, request: Request, service: Service):
    """Return available data plus explicit gaps; never call CDS or import raw files."""
    return _query(service, "timeseries", source, request)


@router.get("/timeseries.csv", response_class=Response)
def timeseries_csv(source: Source, request: Request, service: Service):
    payload = _query(service, "timeseries", source, request)
    stream = StringIO(newline="")
    fields = ["source", "dataset", "variable", "unit", "requested_latitude", "requested_longitude",
              "grid_latitude", "grid_longitude", "time", "value", "coverage", "complete"]
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    grid = payload["grid_location"] or {}
    for point in payload["data"]:
        writer.writerow({
            **{k: payload[k] for k in ["source", "dataset", "variable", "unit", "coverage", "complete"]},
            "requested_latitude": request.location.latitude, "requested_longitude": request.location.longitude,
            "grid_latitude": grid.get("latitude"), "grid_longitude": grid.get("longitude"), **point,
        })
    return Response(stream.getvalue().encode("utf-8-sig"), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{source}_{payload["variable"]}.csv"',
        "X-Data-Source": source, "X-Dataset": payload["dataset"],
        "X-Data-Complete": str(payload["complete"]).lower(), "X-Data-Coverage": str(payload["coverage"]),
    })


@router.get("/{operation}", response_model=StoredAnalysisResponse)
def analyze(
    operation: Literal["aggregate", "statistics", "trend", "heatmap", "map"],
    source: Source, request: Request, service: Service,
    frequency: Literal["hourly", "daily", "monthly", "yearly"] = "daily",
    aggregation: Literal["auto", "mean", "min", "max", "sum"] = "auto",
) -> dict:
    """Analysis of stored data; source/dataset/coverage/gaps accompany every result.

    Partial results are explicitly labelled. A trend is not fitted over missing hours.
    missing_ranges always describe source observation timestamps in UTC+7, including
    when accumulated aggregates use hour_start for their output buckets.
    """
    return _query(service, operation, source, request, frequency=frequency, aggregation=aggregation)
