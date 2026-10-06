from datetime import datetime

from backend.app.domain.schemas import Location, TimeSeriesRequest
from backend.app.providers.mock_provider import MockClimateProvider
from backend.app.services.climate_service import ClimateService


def test_climate_service_gets_timeseries_from_provider() -> None:
    service = ClimateService(provider=MockClimateProvider())
    request = TimeSeriesRequest(
        variable="temperature",
        location=Location(latitude=10.8231, longitude=106.6297),
        start=datetime(2025, 1, 1, 0),
        end=datetime(2025, 1, 1, 2),
    )

    response = service.get_timeseries(request)

    assert response.variable == "temperature"
    assert response.unit == "K"
    assert response.location == request.location
    assert [point.value for point in response.data] == [299.15, 300.444, 301.65]
