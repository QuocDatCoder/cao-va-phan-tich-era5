from datetime import date, datetime, timedelta
import sqlite3

from fastapi.testclient import TestClient
import numpy as np
import pytest
import xarray as xr

from backend.app.core.config import Settings
from backend.app.domain.schemas import ClimatePoint, DownloadArea, DownloadRequest, Location, TimeSeriesRequest, TimeSeriesResponse
from backend.app.domain.wind import WIND_COMPONENTS, circular_statistics, derive_wind
from backend.app.main import app
from backend.app.providers.era5_provider import ERA5ClimateProvider
from backend.app.services.climate_service import ClimateService
from backend.app.services.climate_analysis_service import ClimateAnalysisService, ClimateAnalysisError
from backend.app.services.dependencies import get_climate_service
from backend.app.services.download_job_service import DownloadJobService
from backend.app.storage.climate_repository import ClimateRepository
from backend.app.storage.job_repository import JobRepository


def series(values, variable="wind_u", unit="m/s"):
    return TimeSeriesResponse(variable=variable, unit=unit, location=Location(latitude=10.8, longitude=106.6),
                              data=[ClimatePoint(time=datetime(2025, 2, 1)+timedelta(hours=i), value=v)
                                    for i, v in enumerate(values)])


def test_wind_speed_direction_cardinals_and_calm():
    u, v = series([0, -1, 0, 1, 3, 0]), series([-1, 0, 1, 0, 4, 0])
    speed = derive_wind("wind_speed", u, v)
    direction = derive_wind("wind_direction", u, v)
    assert [p.value for p in speed.data] == [1, 1, 1, 1, 5, 0]
    assert [p.value for p in direction.data] == [0, 90, 180, 270, 216.869898]
    assert len(direction.data) == 5  # No invented direction for the calm observation.


def test_wind_pairs_by_timestamp_and_rejects_missing_component():
    u, v = series([3, 0]), series([4, -1])
    v.data.reverse()
    assert [p.value for p in derive_wind("wind_speed", u, v).data] == [5, 1]
    with pytest.raises(ValueError, match="matching hourly"):
        derive_wind("wind_speed", u, series([4]))


def test_circular_mean_does_not_treat_north_as_south():
    mean, std = circular_statistics([359, 1])
    assert mean == 0
    assert 1 < std < 1.001
    assert circular_statistics([0, 180]) == (None, None)


def test_wind_direction_analysis_is_circular_and_rejects_linear_tools():
    class DirectionService:
        def get_timeseries(self, request):
            return series([359, 1], variable="wind_direction", unit="°")

    service = ClimateAnalysisService(DirectionService())
    request = TimeSeriesRequest(variable="wind_direction", location=Location(latitude=10.8, longitude=106.6),
                                start=datetime(2025, 2, 1), end=datetime(2025, 2, 1, 1))
    stats = service.statistics(request)
    assert stats.mean == 0
    assert stats.min is None and stats.max is None
    assert service.map(request).data[0].value == 0
    assert service.heatmap(request).data[0].value == 0
    with pytest.raises(ClimateAnalysisError, match="circular"):
        service.trend(request)
    with pytest.raises(ClimateAnalysisError, match="circular"):
        service.anomaly(request, request)
    with pytest.raises(ClimateAnalysisError, match="circular"):
        service.compare(request, request)


@pytest.fixture
def provider(tmp_path):
    class Client:
        calls = []
        missing_v = False

        def retrieve(self, dataset, selection, target):
            self.calls.append(selection)
            timestamps = [np.datetime64(f"{day}T{hour}")
                          for day in selection["date"] for hour in selection["time"]]
            names = {
                "2m_temperature": ("t2m", 300), "2m_dewpoint_temperature": ("d2m", 295),
                WIND_COMPONENTS[0]: ("u10", 3), WIND_COMPONENTS[1]: ("v10", 4),
                "surface_solar_radiation_downwards": ("ssrd", 1800000),
            }
            variables = {}
            for name in selection["variable"]:
                if self.missing_v and name == WIND_COMPONENTS[1]:
                    continue
                short_name, value = names[name]
                variables[short_name] = (("time", "latitude", "longitude"),
                                         np.full((len(timestamps), 2, 2), value, dtype=float))
            xr.Dataset(variables, coords={"time": timestamps, "latitude": [11., 10.75],
                                         "longitude": [106.5, 106.75]}).to_netcdf(target, engine="scipy")

    settings = Settings(_env_file=None, data_dir=tmp_path, cds_url="https://example.test", cds_api_key="test")
    client = Client()
    client.calls = []
    repo = ClimateRepository(tmp_path / "climate.sqlite3")
    return ERA5ClimateProvider(settings, client_factory=lambda _: client, repository=repo), client, settings


def request(variable):
    return TimeSeriesRequest(variable=variable, location=Location(latitude=10.8231, longitude=106.6297),
                            start=datetime(2025, 2, 1), end=datetime(2025, 2, 1, 2))


def test_point_wind_reuses_components_for_speed_direction_and_source_queries(provider):
    data, client, settings = provider
    climate = ClimateService(data)
    speed = climate.get_timeseries(request("wind_speed"))
    assert speed.unit == "m/s"
    assert [p.value for p in speed.data] == [5, 5, 5]
    assert [call["variable"] for call in client.calls] == [[WIND_COMPONENTS[0]], [WIND_COMPONENTS[1]]]
    direction = climate.get_timeseries(request("wind_direction"))
    assert direction.unit == "°"
    assert direction.data[0].value == 216.869898
    assert climate.get_timeseries(request("u10")).data[0].value == 3
    assert len(client.calls) == 2


@pytest.mark.parametrize("variable,expected,unit", [
    ("dewpoint", 21.85, "°C"), ("solar_radiation", 1.8, "MJ/m²"),
])
def test_dewpoint_and_radiation_units_are_not_converted_twice(provider, variable, expected, unit):
    data, client, settings = provider
    climate = ClimateService(data)
    first = climate.get_timeseries(request(variable))
    second = climate.get_timeseries(request(variable))
    assert first == second
    assert first.unit == unit
    assert first.data[0].value == pytest.approx(expected)
    assert first.data[0].time.isoformat() == "2025-02-01T07:00:00+07:00"
    assert len(client.calls) == 1


def test_area_wind_download_requests_both_components_then_api_uses_sqlite(provider, monkeypatch):
    data, client, settings = provider
    jobs = DownloadJobService(data, JobRepository(settings.data_dir / "jobs.sqlite3"))
    job = jobs.create_job(DownloadRequest(variable="wind_speed", start_date=date(2025, 2, 1),
                                         end_date=date(2025, 2, 1),
                                         area=DownloadArea(north=11, south=10.5, west=106.5, east=107)))
    jobs.run_job(job.job_id)
    assert jobs.get_job(job.job_id).status == "completed"
    assert client.calls[0]["variable"] == list(WIND_COMPONENTS)

    def forbidden(*args, **kwargs):
        raise AssertionError("No extra download or NetCDF read is allowed")

    monkeypatch.setattr(data, "_download", forbidden)
    monkeypatch.setattr(data, "_read_timeseries", forbidden)
    assert ClimateService(data).get_timeseries(request("wind_direction")).data[0].value == 216.869898
    with sqlite3.connect(settings.data_dir / "climate.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM climate_points").fetchone()[0] == 24 * 4 * 2


def test_area_wind_missing_v_does_not_report_completed(provider):
    data, client, settings = provider
    client.missing_v = True
    jobs = DownloadJobService(data, JobRepository(settings.data_dir / "jobs.sqlite3"))
    job = jobs.create_job(DownloadRequest(variable="wind_direction", start_date=date(2025, 2, 1),
                                         end_date=date(2025, 2, 1),
                                         area=DownloadArea(north=11, south=10.5, west=106.5, east=107)))
    jobs.run_job(job.job_id)
    assert jobs.get_job(job.job_id).status == "failed"


@pytest.mark.parametrize("download_name,query_name,unit,value", [
    ("solar_radiation", "ssrd", "MJ/m²", 1.8),
    ("dewpoint", "2m_dewpoint_temperature", "°C", 21.85),
])
def test_new_source_area_import_can_be_queried_by_alias_without_redownload(
    provider, monkeypatch, download_name, query_name, unit, value,
):
    data, client, settings = provider
    data.download_area(download_name, DownloadArea(north=11, south=10.5, west=106.5, east=107),
                       date(2025, 2, 1), date(2025, 2, 1), "hourly")

    def forbidden(*args, **kwargs):
        raise AssertionError("Imported source must be read from SQLite")

    monkeypatch.setattr(data, "_download", forbidden)
    monkeypatch.setattr(data, "_read_timeseries", forbidden)
    response = ClimateService(data).get_timeseries(request(query_name))
    assert response.unit == unit
    assert response.data[0].value == pytest.approx(value)
    assert len(client.calls) == 1


@pytest.mark.parametrize("variable,unit,value", [
    ("wind_speed", "m/s", 5), ("wind_direction", "°", 216.869898),
    ("dewpoint", "°C", 21.85), ("solar_radiation", "MJ/m²", 1.8),
])
def test_new_variables_through_http_api(provider, variable, unit, value):
    data, client, settings = provider
    climate = ClimateService(data)
    app.dependency_overrides[get_climate_service] = lambda: climate
    try:
        req = request(variable)
        response = TestClient(app).get("/api/v1/climate/timeseries", params={
            "variable": variable, "latitude": req.location.latitude, "longitude": req.location.longitude,
            "start": req.start.isoformat(), "end": req.end.isoformat(),
        })
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["unit"] == unit
    assert response.json()["data"][0]["value"] == pytest.approx(value)
