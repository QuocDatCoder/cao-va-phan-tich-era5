from datetime import date, datetime
import sqlite3

from fastapi.testclient import TestClient
import numpy as np
import pytest
import xarray as xr

from backend.app.core.config import Settings
from backend.app.domain.schemas import (
    DownloadArea, DownloadJobStatus, DownloadRequest, Location,
    SynchronizationRequest, SynchronizationStatus, TimeSeriesRequest,
)
from backend.app.main import app
from backend.app.providers.era5_provider import ERA5ClimateProvider
from backend.app.providers.errors import ClimateDataError
from backend.app.services.climate_service import ClimateService
from backend.app.services.dependencies import get_climate_service, get_download_job_service
from backend.app.services.download_job_service import DownloadJobService
from backend.app.services.synchronization_service import LatestAvailableDateProvider, SynchronizationService
from backend.app.storage.climate_repository import ClimateRepository
from backend.app.storage.job_repository import JobRepository
from backend.app.storage.synchronization_repository import SynchronizationRepository


AREA = DownloadArea(north=11, west=106.5, south=10.5, east=107)


@pytest.fixture
def components(tmp_path):
    class AreaClient:
        def __init__(self):
            self.calls = []
            self.drop_last_hour = False
            self.variable = "t2m"
            self.scale = 1

        def retrieve(self, dataset, selection, target):
            self.calls.append(selection)
            timestamps = [np.datetime64(f"{day}T{hour}")
                          for day in selection["date"] for hour in selection["time"]]
            if self.drop_last_hour:
                timestamps = timestamps[:-1]
            values = (300 + np.arange(len(timestamps))[:, None, None] / 10
                      + np.array([0, 1])[None, :, None]
                      + np.array([0, .01])[None, None, :]) * self.scale
            xr.Dataset(
                {self.variable: (("valid_time", "latitude", "longitude"), values)},
                coords={"valid_time": timestamps, "latitude": [11., 10.75],
                        "longitude": [106.5, 106.75]},
            ).to_netcdf(target, engine="scipy")

    settings = Settings(data_dir=tmp_path, cds_url="https://example.test", cds_api_key="test")
    repository = ClimateRepository(tmp_path / "climate.sqlite3")
    client = AreaClient()
    provider = ERA5ClimateProvider(settings, client_factory=lambda _: client, repository=repository)
    jobs = DownloadJobService(provider, JobRepository(tmp_path / "jobs.sqlite3"))
    return settings, repository, client, provider, jobs


def forbid_download(*args, **kwargs):
    raise AssertionError("Area observations must be reused without new CDS requests or NetCDF reads")


def area_request():
    return DownloadRequest(variable="temperature", area=AREA,
                           start_date=date(2025, 1, 31), end_date=date(2025, 2, 1))


def local_request():
    return TimeSeriesRequest(
        variable="temperature", location=Location(latitude=10.8231, longitude=106.6297),
        start=datetime.fromisoformat("2025-02-01T00:00:00+07:00"),
        end=datetime.fromisoformat("2025-02-01T23:00:00+07:00"),
    )


def test_download_job_imports_every_grid_and_api_reuses_unknown_coordinates(components, monkeypatch):
    settings, repository, client, provider, jobs = components
    app.dependency_overrides[get_download_job_service] = lambda: jobs
    app.dependency_overrides[get_climate_service] = lambda: ClimateService(provider)
    try:
        with TestClient(app) as api:
            created = api.post("/api/v1/downloads", json=area_request().model_dump(mode="json"))
            assert created.status_code == 202
            status = api.get(f"/api/v1/jobs/{created.json()['job_id']}").json()
            assert status["status"] == "completed"
            monkeypatch.setattr(provider, "_download", forbid_download)
            monkeypatch.setattr(provider, "_read_timeseries", forbid_download)
            query = local_request()
            response = api.get("/api/v1/climate/timeseries", params={
                "variable": query.variable, "latitude": query.location.latitude,
                "longitude": query.location.longitude,
                "start": query.start.isoformat(), "end": query.end.isoformat(),
            })
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    result = response.json()
    assert result["unit"] == "°C"
    assert len(result["data"]) == 24
    assert result["data"][0]["time"] == "2025-02-01T00:00:00+07:00"
    assert result["data"][0]["value"] == pytest.approx(29.56)
    with sqlite3.connect(settings.data_dir / "climate.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM climate_points").fetchone()[0] == 48 * 4
    assert len(client.calls) == 1


def test_reimport_cached_area_is_idempotent_and_survives_restart(components, monkeypatch):
    settings, repository, client, provider, jobs = components
    for _ in range(2):
        job = jobs.create_job(area_request())
        jobs.run_job(job.job_id)
        assert jobs.get_job(job.job_id).status == DownloadJobStatus.COMPLETED
    assert len(client.calls) == 1
    restarted = ERA5ClimateProvider(
        settings.model_copy(update={"cds_api_key": None, "cds_url": None}),
        client_factory=forbid_download, repository=ClimateRepository(settings.data_dir / "climate.sqlite3"),
    )
    monkeypatch.setattr(restarted, "_read_timeseries", forbid_download)
    assert len(ClimateService(restarted).get_timeseries(local_request()).data) == 24
    with sqlite3.connect(settings.data_dir / "climate.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM climate_points").fetchone()[0] == 192


def test_area_lookup_respects_bounds_and_nearest_grid_ties(components):
    settings, repo, client, provider, jobs = components
    r = area_request()
    provider.download_area(r.variable, r.area, r.start_date, r.end_date, r.frequency)
    query = dict(dataset=settings.era5_dataset, variable="2m_temperature", unit="K",
                 start=datetime(2025, 2, 1), end=datetime(2025, 2, 1))
    tied = repo.get_series(**query, location=Location(latitude=10.875, longitude=106.625))
    assert tied[0].value == pytest.approx(302.41)  # larger latitude=11 and longitude=106.75
    assert repo.get_series(**query, location=Location(latitude=10.49, longitude=106.6)) == []
    assert repo.get_series(**query, location=Location(latitude=10.8, longitude=107.1)) == []


def test_area_precipitation_is_converted_once(components):
    settings, repo, client, provider, jobs = components
    client.variable, client.scale = "tp", .00001
    provider.download_area("precipitation", AREA, date(2025, 2, 1), date(2025, 2, 1), "hourly")
    request = local_request().model_copy(update={
        "variable": "total_precipitation", "start": datetime(2025, 2, 1),
        "end": datetime(2025, 2, 1),
    })
    series = ClimateService(provider).get_timeseries(request)
    assert series.unit == "mm"
    assert series.data[0].value == pytest.approx(3.0101)
    assert len(client.calls) == 1


def test_sync_finishes_only_after_import_and_point_query_uses_synced_data(components, monkeypatch):
    settings, repo, client, provider, jobs = components
    service = SynchronizationService(jobs, SynchronizationRepository(settings.data_dir / "jobs.sqlite3"),
                                     LatestAvailableDateProvider(5))
    state = service.synchronize(SynchronizationRequest(
        variable="2m_temperature", area=AREA, start_date=date(2025, 1, 31),
        latest_available_date=date(2025, 2, 1),
    ))
    done = service.run_synchronization(state.sync_id)
    assert done.status == SynchronizationStatus.COMPLETED
    assert done.last_synced_date == date(2025, 2, 1)
    monkeypatch.setattr(provider, "_download", forbid_download)
    monkeypatch.setattr(provider, "_read_timeseries", forbid_download)
    assert len(ClimateService(provider).get_timeseries(local_request()).data) == 24


def test_import_failure_rolls_back_and_sync_does_not_advance(components, monkeypatch):
    settings, repo, client, provider, jobs = components
    save_area = repo.save_area

    def failing_import(**kwargs):
        original = kwargs["records"]

        def partial_rows():
            for index, row in enumerate(original):
                if index == 10:
                    raise RuntimeError("simulated import interruption")
                yield row

        kwargs["records"] = partial_rows()
        return save_area(**kwargs)

    monkeypatch.setattr(repo, "save_area", failing_import)
    service = SynchronizationService(jobs, SynchronizationRepository(settings.data_dir / "jobs.sqlite3"),
                                     LatestAvailableDateProvider(5))
    state = service.synchronize(SynchronizationRequest(
        variable="temperature", area=AREA, start_date=date(2025, 1, 31),
        latest_available_date=date(2025, 2, 1),
    ))
    done = service.run_synchronization(state.sync_id)
    assert done.status == SynchronizationStatus.FAILED
    assert done.last_synced_date is None
    assert jobs.get_job(done.job_id).status == DownloadJobStatus.FAILED
    with sqlite3.connect(settings.data_dir / "climate.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM climate_points").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM climate_areas").fetchone()[0] == 0


def test_missing_hour_in_downloaded_area_keeps_partial_data_with_warning(components):
    settings, repo, client, provider, jobs = components
    client.drop_last_hour = True
    job = jobs.create_job(area_request())
    jobs.run_job(job.job_id)
    result = jobs.get_job(job.job_id)
    assert result.status == DownloadJobStatus.COMPLETED
    assert result.quality.issue_counts["missing_hour"] == 1
    assert not result.quality.complete
    assert result.quality.imported_values == 47 * 4


def test_partial_sync_retains_previous_watermark(components):
    settings, _, client, _, jobs = components
    sync = SynchronizationService(jobs, SynchronizationRepository(settings.data_dir / "jobs.sqlite3"),
                                  LatestAvailableDateProvider(5), max_days_per_run=1)
    request = SynchronizationRequest(variable="temperature", area=AREA,
        start_date=date(2025, 1, 31), latest_available_date=date(2025, 2, 1))
    first = sync.synchronize(request)
    assert sync.run_synchronization(first.sync_id).last_synced_date == date(2025, 1, 31)
    client.drop_last_hour = True
    second = sync.synchronize(request)
    partial = sync.run_synchronization(second.sync_id)
    assert partial.status == SynchronizationStatus.FAILED
    assert partial.last_synced_date == date(2025, 1, 31)
