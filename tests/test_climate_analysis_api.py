from datetime import datetime

from fastapi.testclient import TestClient

from backend.app.domain.schemas import (
    AnomalyPoint,
    AnomalyResponse,
    ComparePeriodSummary,
    CompareResponse,
    HeatmapCell,
    HeatmapResponse,
    Location,
    MapPoint,
    MapResponse,
    StatisticsResponse,
    TrendResponse,
)
from backend.app.main import app
from backend.app.services.climate_analysis_service import ClimateAnalysisError
from backend.app.services.dependencies import get_climate_analysis_service


client = TestClient(app)


class StubAnalysisService:
    def statistics(self, request):
        return StatisticsResponse(
            variable=request.variable,
            unit="K",
            location=request.location,
            mean=3.0,
            min=1.0,
            max=5.0,
            std=1.632993,
            count=3,
        )

    def trend(self, request):
        return TrendResponse(
            variable=request.variable,
            unit="K",
            location=request.location,
            slope=2.0,
            direction="increasing",
            period="2025-01-01-2025-01-01",
            count=3,
        )

    def anomaly(self, request, baseline_request):
        return AnomalyResponse(
            variable=request.variable,
            unit="K",
            location=request.location,
            baseline_start=baseline_request.start,
            baseline_end=baseline_request.end,
            baseline_mean=3.0,
            data=[
                AnomalyPoint(time=request.start, value=4.0, anomaly=1.0),
                AnomalyPoint(time=request.end, value=6.0, anomaly=3.0),
            ],
        )

    def compare(self, period_a_request, period_b_request):
        return CompareResponse(
            variable=period_a_request.variable,
            unit="K",
            location=period_a_request.location,
            period_a=ComparePeriodSummary(
                start=period_a_request.start,
                end=period_a_request.end,
                mean=5.0,
                count=2,
            ),
            period_b=ComparePeriodSummary(
                start=period_b_request.start,
                end=period_b_request.end,
                mean=2.0,
                count=2,
            ),
            difference=3.0,
        )

    def heatmap(self, request):
        return HeatmapResponse(
            variable=request.variable,
            unit="K",
            location=request.location,
            data=[
                HeatmapCell(year=2025, month=1, value=2.0, count=2),
                HeatmapCell(year=2025, month=2, value=5.0, count=1),
            ],
        )

    def map(self, request):
        return MapResponse(
            variable=request.variable,
            unit="K",
            data=[
                MapPoint(
                    latitude=request.location.latitude,
                    longitude=request.location.longitude,
                    value=3.0,
                )
            ],
        )


class FailingAnalysisService(StubAnalysisService):
    def statistics(self, request):
        raise ClimateAnalysisError("Analysis requires at least one observation.")


def common_params() -> dict[str, object]:
    return {
        "variable": "temperature",
        "latitude": 10.0,
        "longitude": 106.0,
        "start": "2025-01-01T00:00:00",
        "end": "2025-01-01T02:00:00",
    }


def test_statistics_endpoint_returns_calculated_values() -> None:
    app.dependency_overrides[get_climate_analysis_service] = lambda: StubAnalysisService()
    try:
        response = client.get("/api/v1/climate/statistics", params=common_params())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["mean"] == 3.0
    assert response.json()["std"] == 1.632993
    assert response.json()["count"] == 3


def test_trend_endpoint_returns_slope_and_direction() -> None:
    app.dependency_overrides[get_climate_analysis_service] = lambda: StubAnalysisService()
    try:
        response = client.get("/api/v1/climate/trend", params=common_params())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["slope"] == 2.0
    assert response.json()["direction"] == "increasing"


def test_anomaly_endpoint_returns_baseline_and_anomalies() -> None:
    params = {
        **common_params(),
        "baseline_start": "2024-01-01T00:00:00",
        "baseline_end": "2024-01-01T02:00:00",
    }
    app.dependency_overrides[get_climate_analysis_service] = lambda: StubAnalysisService()
    try:
        response = client.get("/api/v1/climate/anomaly", params=params)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["baseline_mean"] == 3.0
    assert [point["anomaly"] for point in payload["data"]] == [1.0, 3.0]


def test_compare_endpoint_returns_period_difference() -> None:
    params = {
        "variable": "temperature",
        "latitude": 10.0,
        "longitude": 106.0,
        "period_a_start": "2025-01-02T00:00:00",
        "period_a_end": "2025-01-02T01:00:00",
        "period_b_start": "2025-01-01T00:00:00",
        "period_b_end": "2025-01-01T01:00:00",
    }
    app.dependency_overrides[get_climate_analysis_service] = lambda: StubAnalysisService()
    try:
        response = client.get("/api/v1/climate/compare", params=params)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["period_a"]["mean"] == 5.0
    assert response.json()["period_b"]["mean"] == 2.0
    assert response.json()["difference"] == 3.0


def test_heatmap_endpoint_returns_monthly_cells() -> None:
    app.dependency_overrides[get_climate_analysis_service] = lambda: StubAnalysisService()
    try:
        response = client.get("/api/v1/climate/heatmap", params=common_params())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["data"] == [
        {"year": 2025, "month": 1, "value": 2.0, "count": 2},
        {"year": 2025, "month": 2, "value": 5.0, "count": 1},
    ]


def test_map_endpoint_returns_spatial_point() -> None:
    app.dependency_overrides[get_climate_analysis_service] = lambda: StubAnalysisService()
    try:
        response = client.get("/api/v1/climate/map", params=common_params())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["data"] == [{"latitude": 10.0, "longitude": 106.0, "value": 3.0}]


def test_analysis_endpoint_rejects_invalid_date_range() -> None:
    params = {
        **common_params(),
        "start": "2025-01-02T00:00:00",
        "end": "2025-01-01T00:00:00",
    }

    response = client.get("/api/v1/climate/statistics", params=params)

    assert response.status_code == 422


def test_analysis_endpoint_returns_controlled_domain_error() -> None:
    app.dependency_overrides[get_climate_analysis_service] = lambda: FailingAnalysisService()
    try:
        response = client.get("/api/v1/climate/statistics", params=common_params())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json() == {"detail": "Analysis requires at least one observation."}


def test_openapi_contains_phase_6_routes() -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/v1/climate/statistics" in paths
    assert "/api/v1/climate/trend" in paths
    assert "/api/v1/climate/anomaly" in paths
    assert "/api/v1/climate/compare" in paths
    assert "/api/v1/climate/heatmap" in paths
    assert "/api/v1/climate/map" in paths
