from datetime import date, datetime, timedelta
import json

import numpy as np
import pytest
import xarray as xr

from backend.app.core.config import Settings
from backend.app.domain.schemas import DownloadArea, DownloadRequest
from backend.app.providers.era5_provider import ERA5ClimateProvider
from backend.app.providers.errors import ClimateDataError
from backend.app.services.download_job_service import DownloadJobService
from backend.app.storage.climate_repository import ClimateRepository
from backend.app.storage.job_repository import JobRepository


@pytest.fixture
def components(tmp_path):
    class Client:
        def __init__(self):
            self.calls = []

        def retrieve(self, dataset, selection, target):
            self.calls.append(selection)
            stamps = [np.datetime64(f"{day}T{hour}") for day in selection["date"] for hour in selection["time"]]
            variables = {}
            for variable in selection["variable"]:
                name, value = {"2m_temperature": ("t2m", 300),
                               "10m_u_component_of_wind": ("u10", 3),
                               "10m_v_component_of_wind": ("v10", 4)}[variable]
                variables[name] = (("time", "latitude", "longitude"), np.full((len(stamps), 1, 1), value, dtype=float))
            xr.Dataset(variables, coords={"time": stamps, "latitude": [10.75], "longitude": [106.75]}).to_netcdf(target, engine="scipy")

    settings = Settings(_env_file=None, data_dir=tmp_path, cds_url="https://example.test", cds_api_key="test",
                        era5_max_days_per_request=2)
    client = Client()
    provider = ERA5ClimateProvider(settings, client_factory=lambda _: client,
                                   repository=ClimateRepository(tmp_path / "climate.sqlite3"))
    return provider, client, settings


def query(provider, variable="temperature", end=datetime(2025, 1, 10, 23)):
    return provider.get_timeseries(variable, 10.8231, 106.6297, datetime(2025, 1, 1), end)


def test_long_point_query_batches_missing_full_days_without_duplicate_downloads(components):
    provider, client, settings = components
    query(provider, end=datetime(2025, 1, 2, 23))
    series = query(provider)
    assert len(series.data) == 240
    assert len({point.time for point in series.data}) == 240
    assert len(client.calls) == 5
    assert all(len(call["date"]) <= 2 for call in client.calls)
    dates = [day for call in client.calls for day in call["date"]]
    assert len(dates) == len(set(dates)) == 10
    assert query(provider) == series and len(client.calls) == 5


def test_long_point_query_without_sqlite_still_splits_requests(components):
    provider, client, settings = components
    provider._repository = None
    assert len(query(provider).data) == 240
    assert len(client.calls) == 5
    assert all(len(call["date"]) <= 2 for call in client.calls)


def test_long_wind_query_reuses_source_components_for_direction(components):
    provider, client, settings = components
    speed = query(provider, "wind_speed")
    assert len(speed.data) == 240 and all(point.value == 5 for point in speed.data)
    assert len(client.calls) == 10
    assert len(query(provider, "wind_direction").data) == 240
    assert len(client.calls) == 10


def test_long_area_job_returns_manifest_after_importing_all_chunks(components, monkeypatch):
    provider, client, settings = components
    jobs = DownloadJobService(provider, JobRepository(settings.data_dir / "jobs.sqlite3"))
    request = DownloadRequest(variable="temperature", start_date=date(2025, 1, 1), end_date=date(2025, 1, 10),
                              area=DownloadArea(north=11, south=10.5, west=106.5, east=107))
    job = jobs.create_job(request)
    jobs.run_job(job.job_id)
    completed = jobs.get_job(job.job_id)
    assert completed.status == "completed"
    manifest = json.loads((settings.data_dir / completed.file_path).read_text())
    assert len(manifest["files"]) == 5

    def forbidden(*args, **kwargs):
        raise AssertionError("Imported area data must be reusable from SQLite")

    monkeypatch.setattr(provider, "_download", forbidden)
    monkeypatch.setattr(provider, "_read_timeseries", forbidden)
    assert len(query(provider).data) == 240
    assert len(client.calls) == 5


def test_total_query_limit_is_separate_from_per_download_limit(components):
    provider, client, settings = components
    provider._settings = settings.model_copy(update={"era5_max_query_days": 3})
    with pytest.raises(ClimateDataError, match="limited to 3 days"):
        query(provider)
    assert not client.calls
