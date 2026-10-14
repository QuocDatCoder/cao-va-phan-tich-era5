from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.providers.errors import ClimateConfigurationError
from backend.app.services.dependencies import get_climate_service


client = TestClient(app)


def test_timeseries_endpoint_returns_mock_data() -> None:
    response = client.get(
        "/api/v1/climate/timeseries",
        params={
            "variable": "temperature",
            "latitude": 10.8231,
            "longitude": 106.6297,
            "start": "2025-01-01T00:00:00",
            "end": "2025-01-01T02:00:00",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "variable": "temperature",
        "unit": "°C",
        "location": {
            "latitude": 10.8231,
            "longitude": 106.6297,
        },
        "data": [
            {"time": "2025-01-01T07:00:00+07:00", "value": 26.0},
            {"time": "2025-01-01T08:00:00+07:00", "value": 27.294},
            {"time": "2025-01-01T09:00:00+07:00", "value": 28.5},
        ],
    }


def test_timeseries_endpoint_rejects_invalid_latitude() -> None:
    response = client.get(
        "/api/v1/climate/timeseries",
        params={
            "variable": "temperature",
            "latitude": 91,
            "longitude": 106.6297,
            "start": "2025-01-01T00:00:00",
            "end": "2025-01-01T02:00:00",
        },
    )

    assert response.status_code == 422


def test_timeseries_endpoint_accepts_local_day_and_returns_explicit_offset():
    response = client.get("/api/v1/climate/timeseries", params={
        "variable": "temperature", "latitude": 10.8231, "longitude": 106.6297,
        "start": "2025-02-01T00:00:00+07:00", "end": "2025-02-01T23:00:00+07:00",
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["unit"] == "°C"
    assert len(payload["data"]) == 24
    assert payload["data"][0]["time"] == "2025-02-01T00:00:00+07:00"
    assert payload["data"][-1]["time"] == "2025-02-01T23:00:00+07:00"


def test_timeseries_endpoint_validates_mixed_naive_and_offset_datetimes():
    response = client.get("/api/v1/climate/timeseries", params={
        "variable": "temperature", "latitude": 10.8231, "longitude": 106.6297,
        "start": "2025-02-01T00:00:00", "end": "2025-02-01T01:00:00+07:00",
    })
    assert response.status_code == 422


def test_timeseries_endpoint_rejects_invalid_date_range() -> None:
    response = client.get(
        "/api/v1/climate/timeseries",
        params={
            "variable": "temperature",
            "latitude": 10.8231,
            "longitude": 106.6297,
            "start": "2025-01-02T00:00:00",
            "end": "2025-01-01T00:00:00",
        },
    )

    assert response.status_code == 422


def test_timeseries_endpoint_handles_provider_configuration_error() -> None:
    class FailingService:
        def get_timeseries(self, request):
            raise ClimateConfigurationError("ERA5 provider requires CDS configuration.")

    app.dependency_overrides[get_climate_service] = lambda: FailingService()
    try:
        response = client.get(
            "/api/v1/climate/timeseries",
            params={
                "variable": "temperature",
                "latitude": 10.8231,
                "longitude": 106.6297,
                "start": "2025-01-01T00:00:00",
                "end": "2025-01-01T02:00:00",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"detail": "ERA5 provider requires CDS configuration."}
