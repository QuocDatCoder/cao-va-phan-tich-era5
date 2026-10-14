from datetime import datetime, timedelta, timezone

import pytest

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
    assert response.unit == "°C"
    assert response.location == request.location
    assert [point.value for point in response.data] == [26.0, 27.294, 28.5]
    assert response.data[0].time.isoformat() == "2025-01-01T07:00:00+07:00"


def test_vietnam_calendar_day_queries_previous_utc_date_and_returns_24_hours():
    class RecordingProvider(MockClimateProvider):
        def get_timeseries(self, **kwargs):
            self.request = kwargs
            return super().get_timeseries(**kwargs)

    provider = RecordingProvider()
    vn = timezone(timedelta(hours=7))
    request = TimeSeriesRequest(
        variable="temperature",
        location=Location(latitude=10.8231, longitude=106.6297),
        start=datetime(2025, 2, 1, 0, tzinfo=vn),
        end=datetime(2025, 2, 1, 23, tzinfo=vn),
    )
    response = ClimateService(provider).get_timeseries(request)
    assert provider.request["start"] == datetime(2025, 1, 31, 17)
    assert provider.request["end"] == datetime(2025, 2, 1, 16)
    assert len(response.data) == 24
    assert response.data[0].time == request.start
    assert response.data[-1].time == request.end
    assert all(point.time.date() == request.start.date() for point in response.data)


def test_precipitation_values_and_units_are_preserved():
    request = TimeSeriesRequest(
        variable="precipitation", location=Location(latitude=10, longitude=106),
        start=datetime(2025, 1, 1), end=datetime(2025, 1, 1, 2),
    )
    response = ClimateService(MockClimateProvider()).get_timeseries(request)
    assert response.unit == "mm"
    assert [point.value for point in response.data] == [0.0, 0.518, 1.0]


def test_statistics_and_anomaly_use_celsius_without_changing_differences():
    from backend.app.services.climate_analysis_service import ClimateAnalysisService

    request = TimeSeriesRequest(
        variable="temperature", location=Location(latitude=10, longitude=106),
        start=datetime(2025, 1, 1), end=datetime(2025, 1, 1, 2),
    )
    analysis = ClimateAnalysisService(ClimateService(MockClimateProvider()))
    stats = analysis.statistics(request)
    assert stats.unit == "°C"
    assert stats.mean == pytest.approx((26 + 27.294 + 28.5) / 3, abs=1e-6)
    anomaly = analysis.anomaly(request, request)
    assert anomaly.unit == "°C"
    assert sum(point.anomaly for point in anomaly.data) == pytest.approx(0, abs=2e-6)


def test_heatmap_groups_by_vietnam_month_at_utc_month_boundary():
    from backend.app.services.climate_analysis_service import ClimateAnalysisService

    request = TimeSeriesRequest(
        variable="temperature", location=Location(latitude=10, longitude=106),
        start=datetime(2025, 1, 31, 17), end=datetime(2025, 1, 31, 23),
    )
    response = ClimateAnalysisService(ClimateService(MockClimateProvider())).heatmap(request)
    assert len(response.data) == 1
    assert response.data[0].month == 2
    assert response.data[0].count == 7
