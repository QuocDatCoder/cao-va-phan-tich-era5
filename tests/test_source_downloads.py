"""Explicit collection and additive migration, using fake CDS files only."""
from datetime import date, datetime, timedelta
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
import numpy as np
import pytest
import xarray as xr

from backend.app.core.config import Settings
from backend.app.domain.schemas import DownloadRequest, Location, DownloadArea
from backend.app.main import create_app
from backend.app.providers.era5_provider import ERA5ClimateProvider
from backend.app.providers.era5_land_timeseries_provider import ERA5LandTimeseriesProvider, BASE_VARIABLES
from backend.app.providers.mock_provider import MockClimateProvider
from backend.app.providers import dependencies as provider_dependencies
from backend.app.services.dependencies import get_download_job_service, get_stored_climate_service
from backend.app.services.download_job_service import DownloadJobService
from backend.app.services.stored_climate_service import StoredClimateService
from backend.app.storage.climate_repository import ClimateRepository
from backend.app.storage.job_repository import JobRepository

POINT = {"latitude": 10.8231, "longitude": 106.6297}
AREA = {"north": 11, "south": 10, "west": 106, "east": 107}
DATES = {"start_date": "2025-01-01", "end_date": "2025-01-01"}


class FakeCDS:
    def __init__(self):
        self.calls = []

    def retrieve(self, dataset, request, target):
        self.calls.append((dataset, request))
        if dataset == "reanalysis-era5-land-timeseries":
            first, last = request["date"][0].split("/")
            days = (date.fromisoformat(last)-date.fromisoformat(first)).days+1
            start = datetime.fromisoformat(first)
            lines = ["valid_time,t2m,d2m,u10,v10,tp,ssrd,latitude,longitude"]
            lines.extend(f"{start+timedelta(hours=h)},300,295,3,4,0.001,2000000,10.8,106.7"
                         for h in range(days*24))
            Path(target).write_text("\n".join(lines), encoding="utf-8")
        else:
            times = np.array([np.datetime64("2025-01-01T00")+np.timedelta64(h, "h") for h in range(24)])
            ds = xr.Dataset({"t2m": (("valid_time", "latitude", "longitude"),
                                      np.full((24, 1, 1), 290.0))},
                            coords={"valid_time": times, "latitude": [10.75], "longitude": [106.75]})
            ds.to_netcdf(target, engine="scipy")


@pytest.fixture
def collection(tmp_path):
    settings = Settings(_env_file=None, climate_provider="mock", cds_url="https://example.invalid",
                        cds_api_key="fixture-only", data_dir=tmp_path/"data",
                        climate_data_db_path=tmp_path/"climate.sqlite3", job_metadata_db_path=tmp_path/"jobs.sqlite3")
    observations = ClimateRepository(settings.climate_data_db_path)
    cds = FakeCDS()
    providers = {
        "land": ERA5LandTimeseriesProvider(settings, client_factory=lambda _: cds, repository=observations),
        "era5": ERA5ClimateProvider(settings, client_factory=lambda _: cds, repository=observations),
        "mock": MockClimateProvider(settings.data_dir, repository=observations),
    }
    jobs = JobRepository(settings.job_metadata_db_path)
    service = DownloadJobService(providers["mock"], jobs, provider_resolver=providers.__getitem__, settings=settings)
    app = create_app()
    app.dependency_overrides[get_download_job_service] = lambda: service
    app.dependency_overrides[get_stored_climate_service] = lambda: StoredClimateService(
        ClimateRepository(settings.climate_data_db_path, read_only=True), settings)
    with TestClient(app) as client:
        yield settings, observations, jobs, service, providers, cds, client


def test_explicit_land_point_and_era5_area_roundtrip_remain_separate(collection):
    settings, _, jobs, _, _, cds, client = collection
    for source, spatial in [("land", {"location": POINT}), ("era5", {"area": AREA})]:
        created = client.post("/api/v1/downloads", json={"source": source, "variable": "temperature", **DATES, **spatial})
        assert created.status_code == 202, created.text
        job = client.get("/api/v1/jobs/"+created.json()["job_id"]).json()
        assert job["status"] == "completed", job
        assert job["source"] == source
        assert job["dataset"] == (settings.era5_land_dataset if source == "land" else settings.era5_dataset)
        assert job["spatial_mode"] == ("point" if source == "land" else "area")
        assert job["location"] == (POINT if source == "land" else None)
        assert job["area"] == (None if source == "land" else AREA)
        assert job["retrieval_variables"] == (sorted(BASE_VARIABLES) if source == "land" else ["2m_temperature"])
        reloaded = JobRepository(settings.job_metadata_db_path).get(job["job_id"])
        assert reloaded.source == source and reloaded.dataset == job["dataset"]
    assert settings.climate_provider == "mock"  # No global provider switch.
    before = len(cds.calls)
    for source, expected in [("land", 26.85), ("era5", 16.85), ("mock", None)]:
        r = client.get("/api/v1/stored/climate/timeseries", params={
            "source": source, "variable": "temperature", **POINT,
            "start": "2025-01-01T00:00:00Z", "end": "2025-01-01T23:00:00Z"})
        assert r.status_code == 200, r.text
        payload = r.json()
        if expected is not None:
            assert payload["complete"] and payload["data"][0]["value"] == pytest.approx(expected)
        else:
            assert payload["status"] == "missing" and payload["data"] == []
    assert len(cds.calls) == before  # No additional retrieve for stored queries.


def test_queued_job_keeps_source_if_default_provider_changes(collection):
    settings, _, jobs, service, providers, cds, _ = collection
    created = service.create_job(DownloadRequest(source="land", variable="temperature", location=POINT, **DATES))
    queued = jobs.get(created.job_id)
    assert queued.source == "land" and queued.dataset == settings.era5_land_dataset
    changed = settings.model_copy(update={"climate_provider": "era5"})
    restarted = DownloadJobService(providers["era5"], JobRepository(settings.job_metadata_db_path),
                                    provider_resolver=providers.__getitem__, settings=changed)
    restarted.run_job(created.job_id)
    assert cds.calls[0][0] == settings.era5_land_dataset
    assert jobs.get(created.job_id).status == "completed"


def test_changed_dataset_configuration_cannot_silently_retarget_a_queued_job(collection):
    settings, _, jobs, service, providers, cds, _ = collection
    created = service.create_job(DownloadRequest(source="land", variable="temperature", location=POINT, **DATES))
    changed = settings.model_copy(update={"era5_land_dataset": "different-dataset"})
    restarted = DownloadJobService(providers["mock"], jobs, provider_resolver=providers.__getitem__, settings=changed)
    restarted.run_job(created.job_id)
    assert jobs.get(created.job_id).status == "failed" and not cds.calls
    assert jobs.get(created.job_id).dataset == settings.era5_land_dataset


def test_provider_cannot_complete_job_with_another_dataset(collection, monkeypatch):
    settings, _, jobs, service, providers, _, _ = collection
    from backend.app.domain.schemas import DownloadArtifact
    monkeypatch.setattr(providers["land"], "download_point", lambda **kwargs: DownloadArtifact(
        dataset=settings.era5_dataset, variable="temperature", file_path=settings.data_dir/"dummy.nc",
        storage_path="dummy.nc"))
    created = service.create_job(DownloadRequest(source="land", variable="temperature", location=POINT, **DATES))
    service.run_job(created.job_id)
    job = jobs.get(created.job_id)
    assert job.status == "failed" and job.dataset == settings.era5_land_dataset


def test_old_queued_job_with_unknown_source_is_not_run_under_current_default(collection):
    settings, _, jobs, service, _, cds, _ = collection
    jobs.create("old-queued", DownloadRequest(variable="temperature", area=AREA, **DATES))
    service.run_job("old-queued")
    assert jobs.get("old-queued").status == "failed" and not cds.calls


def test_legacy_land_area_becomes_honestly_labelled_center_point(collection):
    settings, _, jobs, _, providers, _, _ = collection
    service = DownloadJobService(providers["land"], jobs, provider_resolver=providers.__getitem__,
                                  settings=settings.model_copy(update={"climate_provider": "era5_land_timeseries"}))
    created = service.create_job(DownloadRequest(variable="temperature", area=AREA, **DATES))
    job = jobs.get(created.job_id)
    assert job.source == "land" and job.spatial_mode == "point" and job.area is None
    assert job.location == Location(latitude=10.5, longitude=106.5)


def test_mock_collection_is_explicit_and_saved_only_under_mock(collection):
    _, _, _, _, _, cds, client = collection
    r = client.post("/api/v1/downloads", json={"source": "mock", "variable": "temperature", **DATES, "location": POINT})
    assert r.status_code == 202
    params = {"variable": "temperature", **POINT, "start": "2025-01-01T00:00:00Z", "end": "2025-01-01T23:00:00Z"}
    assert client.get("/api/v1/stored/climate/timeseries", params={**params, "source": "mock"}).json()["complete"]
    assert client.get("/api/v1/stored/climate/timeseries", params={**params, "source": "land"}).json()["status"] == "missing"
    assert not cds.calls


@pytest.mark.parametrize("spatial", [
    {"source": "land", "area": AREA}, {"source": "era5", "location": POINT},
    {"source": "land"}, {"source": "land", "location": POINT, "area": AREA},
])
def test_new_collection_contract_rejects_misleading_or_missing_spatial_selection(collection, spatial):
    client = collection[-1]
    assert client.post("/api/v1/downloads", json={"variable": "temperature", **DATES, **spatial}).status_code == 422


def test_source_factories_do_not_mutate_settings_or_share_providers(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, climate_provider="mock", data_dir=tmp_path,
                        climate_data_db_path=tmp_path/"observations.sqlite3")
    monkeypatch.setattr(provider_dependencies, "get_settings", lambda: settings)
    factory = provider_dependencies.get_provider_for_source
    factory.cache_clear()
    try:
        land, era5, mock = [factory(source) for source in ["land", "era5", "mock"]]
        assert type(land) is ERA5LandTimeseriesProvider
        assert type(era5) is ERA5ClimateProvider
        assert type(mock) is MockClimateProvider
        assert land._settings.era5_dataset == settings.era5_land_dataset
        assert era5._settings.era5_dataset == settings.era5_dataset
        assert settings.climate_provider == "mock" and settings.era5_dataset == "reanalysis-era5-single-levels"
    finally:
        factory.cache_clear()


def test_job_migration_is_additive_and_idempotent(tmp_path):
    path = tmp_path/"legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
        CREATE TABLE download_jobs (
          job_id TEXT PRIMARY KEY, variable TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
          north REAL NOT NULL, west REAL NOT NULL, south REAL NOT NULL, east REAL NOT NULL,
          frequency TEXT NOT NULL, status TEXT NOT NULL, progress INTEGER NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT, dataset TEXT, file_path TEXT, error TEXT);
        INSERT INTO download_jobs VALUES ('old','temperature','2025-01-01','2025-01-01',11,106,10,107,
          'hourly','completed',100,'2025-01-01T00:00:00','2025-01-01T00:00:00','2025-01-01T00:00:00',
          'reanalysis-era5-single-levels','raw/existing.nc',NULL);
        """)
    for _ in range(2):
        job = JobRepository(path).get("old")
        assert job.source is None  # Do not guess historical source identities.
        assert job.status == "completed" and job.file_path == "raw/existing.nc"
        assert job.dataset == "reanalysis-era5-single-levels" and job.retrieval_variables == []
