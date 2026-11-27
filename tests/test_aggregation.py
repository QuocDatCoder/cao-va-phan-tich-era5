from datetime import datetime, timedelta

from fastapi.testclient import TestClient
import pytest

from backend.app.domain.schemas import AggregationRequest, ClimatePoint, Location, TimeSeriesResponse
from backend.app.domain.time_utils import hourly_timestamps
from backend.app.main import app
from backend.app.services.climate_service import ClimateService
from backend.app.services.dependencies import get_climate_service


class RecordingProvider:
    def __init__(self):
        self.calls = []
        self.omit_last = False

    def get_timeseries(self, variable, latitude, longitude, start, end):
        self.calls.append((start, end))
        stamps = hourly_timestamps(start, end)
        if self.omit_last:
            stamps = stamps[:-1]
        accumulated = variable in {"precipitation", "solar_radiation"}
        return TimeSeriesResponse(
            variable=variable, unit=("mm" if variable == "precipitation" else "MJ/m²") if accumulated else "K",
            location=Location(latitude=latitude, longitude=longitude),
            data=[ClimatePoint(time=t, value=t.day*100+t.hour if accumulated else 273.15+t.hour) for t in stamps],
        )


def request(**overrides):
    params = dict(variable="temperature", location=Location(latitude=10.8, longitude=106.6),
                  start=datetime.fromisoformat("2025-02-01T00:00:00+07:00"),
                  end=datetime.fromisoformat("2025-02-01T23:00:00+07:00"), frequency="daily")
    params.update(overrides)
    return AggregationRequest(**params)


def test_daily_temperature_preserves_mean_min_max_and_coverage():
    response = ClimateService(RecordingProvider()).get_aggregate(request())
    point = response.data[0]
    assert response.unit == "°C" and response.aggregation == "mean"
    assert point.time.isoformat() == "2025-02-01T00:00:00+07:00"
    assert point.value == 11.5 and point.min == 0 and point.max == 23
    assert point.count == point.expected_count == 24
    assert point.complete and not point.partial_period and point.coverage == 1


@pytest.mark.parametrize("variable,unit", [("precipitation", "mm"), ("solar_radiation", "MJ/m²")])
def test_accumulations_use_hour_ending_data_for_correct_local_day(variable, unit):
    provider = RecordingProvider()
    response = ClimateService(provider).get_aggregate(request(variable=variable))
    assert provider.calls == [(datetime(2025, 1, 31, 18), datetime(2025, 2, 1, 17))]
    assert response.aggregation == "sum" and response.time_basis == "hour_start"
    assert response.unit == unit
    assert response.data[0].value == 20676  # Jan 31 18..23 UTC plus Feb 1 00..17 UTC.
    assert response.data[0].complete


def test_monthly_partial_periods_era_explicit():
    response = ClimateService(RecordingProvider()).get_aggregate(request(
        start=datetime.fromisoformat("2025-01-31T00:00:00+07:00"),
        end=datetime.fromisoformat("2025-02-02T23:00:00+07:00"), frequency="monthly",
    ))
    assert [point.count for point in response.data] == [24, 48]
    assert all(point.partial_period and not point.complete and point.coverage == 1 for point in response.data)


def test_full_leap_year_has_8784_hours_and_one_complete_year_bucket():
    response = ClimateService(RecordingProvider()).get_aggregate(request(
        start=datetime.fromisoformat("2024-01-01T00:00:00+07:00"),
        end=datetime.fromisoformat("2024-12-31T23:00:00+07:00"), frequency="yearly",
    ))
    assert len(response.data) == 1
    assert response.data[0].count == 8784
    assert response.data[0].complete
    assert response.data[0].period_end.isoformat() == "2025-01-01T00:00:00+07:00"


def test_missing_observation_is_not_reported_complete():
    provider = RecordingProvider()
    provider.omit_last = True
    point = ClimateService(provider).get_aggregate(request()).data[0]
    assert point.count == 23 and point.expected_count == 24
    assert point.coverage == pytest.approx(23/24, abs=1e-6)
    assert not point.complete


def test_circular_aggregation_and_empty_calm_direction_bucket():
    class DirectionProvider:
        calm = False

        def get_timeseries(self, variable, latitude, longitude, start, end):
            return TimeSeriesResponse(variable=variable, unit="°", location=Location(latitude=latitude, longitude=longitude),
                                      data=[] if self.calm else [
                                          ClimatePoint(time=start, value=359), ClimatePoint(time=start+timedelta(hours=1), value=1),
                                      ])

    provider = DirectionProvider()
    climate = ClimateService(provider)
    point = climate.get_aggregate(request(variable="wind_direction")).data[0]
    assert point.value == 0 and point.min is None and point.max is None
    provider.calm = True
    point = climate.get_aggregate(request(variable="wind_direction")).data[0]
    assert point.value is None and point.count == 0 and point.coverage == 0


def test_invalid_sum_is_rejected_before_provider_call():
    provider = RecordingProvider()
    with pytest.raises(ValueError, match="only supported"):
        ClimateService(provider).get_aggregate(request(aggregation="sum"))
    assert not provider.calls


def test_aggregate_api_validates_frequency_and_aggregation():
    app.dependency_overrides[get_climate_service] = lambda: ClimateService(RecordingProvider())
    params = dict(variable="temperature", latitude=10.8, longitude=106.6,
                  start="2025-02-01T00:00:00+07:00", end="2025-02-01T23:00:00+07:00")
    try:
        api = TestClient(app)
        response = api.get("/api/v1/climate/aggregate", params={**params, "frequency": "daily"})
        assert response.status_code == 200
        assert response.json()["data"][0]["count"] == 24
        assert api.get("/api/v1/climate/aggregate", params={**params, "frequency": "weekly"}).status_code == 422
        assert api.get("/api/v1/climate/aggregate", params={**params, "aggregation": "sum"}).status_code == 422
    finally:
        app.dependency_overrides.clear()
