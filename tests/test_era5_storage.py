from datetime import datetime
from pathlib import Path
from shutil import copyfile

import numpy as np
import pytest
import xarray as xr

from backend.app.core.config import Settings
from backend.app.domain.schemas import ClimatePoint, Location, TimeSeriesRequest
from backend.app.providers.era5_provider import ERA5ClimateProvider
from backend.app.services.climate_service import ClimateService
from backend.app.storage.climate_repository import ClimateRepository
from backend.app.domain.time_utils import hourly_timestamps


@pytest.fixture
def stored_provider(tmp_path):
    source = tmp_path / "source.nc"
    xr.Dataset(
        {"t2m": (("time", "latitude", "longitude"), np.array([300., 302., 304.]).reshape(3, 1, 1))},
        coords={"time": np.array(["2025-02-01T00", "2025-02-01T01", "2025-02-01T02"], dtype="datetime64[h]"),
                "latitude": [10.75], "longitude": [106.75]},
    ).to_netcdf(source, engine="scipy")
    calls = []

    class Client:
        def retrieve(self, dataset, request, target):
            calls.append(request)
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            copyfile(source, target)

    settings = Settings(data_dir=tmp_path, cds_url="https://example.test", cds_api_key="test",
                        climate_data_db_path=tmp_path / "climate.sqlite3")
    repo = ClimateRepository(settings.climate_data_db_path)
    provider = ERA5ClimateProvider(settings, client_factory=lambda _: Client(), repository=repo)
    return provider, settings, calls


def request(provider, **overrides):
    params = dict(variable="temperature", latitude=10.8231, longitude=106.6297,
                  start=datetime(2025, 2, 1), end=datetime(2025, 2, 1, 2))
    params.update(overrides)
    return provider.get_timeseries(**params)


def forbid_read(*args, **kwargs):
    raise AssertionError("Complete SQLite queries must not read NetCDF or call CDS")


def test_repeat_subset_and_alias_use_sqlite_without_reading_file(stored_provider, monkeypatch):
    provider, settings, calls = stored_provider
    first = request(provider)
    monkeypatch.setattr(provider, "_read_timeseries", forbid_read)
    monkeypatch.setattr(provider, "_download", forbid_read)
    assert request(provider) == first
    subset = request(provider, variable="2m_temperature", start=datetime(2025, 2, 1, 1))
    assert [p.value for p in subset.data] == [302, 304]
    assert len(calls) == 1


def test_cache_survives_provider_restart_without_cds_credentials(stored_provider, monkeypatch):
    provider, settings, calls = stored_provider
    request(provider)
    offline_settings = settings.model_copy(update={"cds_url": None, "cds_api_key": None})
    restarted = ERA5ClimateProvider(offline_settings, client_factory=forbid_read,
                                   repository=ClimateRepository(settings.climate_data_db_path))
    monkeypatch.setattr(restarted, "_read_timeseries", forbid_read)
    result = ClimateService(restarted).get_timeseries(TimeSeriesRequest(
        variable="temperature", location=Location(latitude=10.8231, longitude=106.6297),
        start=datetime(2025, 2, 1), end=datetime(2025, 2, 1, 2),
    ))
    assert result.unit == "°C"
    assert [p.value for p in result.data] == [26.85, 28.85, 30.85]
    assert result.data[0].time.isoformat() == "2025-02-01T07:00:00+07:00"


def test_extending_cache_downloads_only_missing_hours(stored_provider):
    provider, settings, calls = stored_provider
    request(provider, end=datetime(2025, 2, 1, 1))
    result = request(provider)
    assert len(calls) == 2
    assert calls[1]["date"] == ["2025-02-01"]
    assert calls[1]["time"] == ["02:00"]
    assert len(result.data) == 3


def test_interior_gap_downloads_only_missing_hour(stored_provider):
    provider, settings, calls = stored_provider
    provider._repository.save(
        dataset=settings.era5_dataset, variable="2m_temperature", unit="K",
        requested_location=Location(latitude=10.8231, longitude=106.6297),
        grid_location=Location(latitude=10.75, longitude=106.75),
        points=[ClimatePoint(time=datetime(2025, 2, 1, 0), value=300),
                ClimatePoint(time=datetime(2025, 2, 1, 2), value=304)],
    )
    result = request(provider)
    assert [p.value for p in result.data] == [300, 302, 304]
    assert len(calls) == 1
    assert calls[0]["time"] == ["01:00"]


def test_existing_exact_raw_file_imports_without_new_download(stored_provider, monkeypatch):
    provider, settings, calls = stored_provider
    request(provider)
    # A new DB simulates upgrading while retaining previously downloaded files.
    provider._repository = ClimateRepository(settings.data_dir / "fresh.sqlite3")
    monkeypatch.setattr(provider, "_download", forbid_read)
    result = request(provider)
    assert len(result.data) == 3
    assert len(calls) == 1


def test_failure_preserves_previously_stored_points(stored_provider, monkeypatch):
    provider, settings, calls = stored_provider
    request(provider, end=datetime(2025, 2, 1, 1))

    def fail(*args, **kwargs):
        from backend.app.providers.errors import ClimateProviderError
        raise ClimateProviderError("CDS unavailable")

    monkeypatch.setattr(provider, "_download", fail)
    from backend.app.providers.errors import ClimateProviderError
    with pytest.raises(ClimateProviderError, match="CDS unavailable"):
        request(provider)
    assert len(request(provider, end=datetime(2025, 2, 1, 1)).data) == 2


def test_partial_cds_result_is_not_reported_as_complete(stored_provider, monkeypatch):
    provider, settings, calls = stored_provider
    original_read = provider._read_timeseries

    def missing_middle(*args, **kwargs):
        points, grid = original_read(*args, **kwargs)
        return [p for p in points if p.time.hour != 1], grid

    monkeypatch.setattr(provider, "_read_timeseries", missing_middle)
    from backend.app.providers.errors import ClimateDataError
    with pytest.raises(ClimateDataError, match="still incomplete"):
        request(provider)


def test_raw_file_names_are_isolated_between_datasets(stored_provider):
    provider, settings, calls = stored_provider
    kwargs = dict(variable="temperature", latitude=10.8231, longitude=106.6297,
                  start=datetime(2025, 2, 1), end=datetime(2025, 2, 1, 2))
    other = ERA5ClimateProvider(settings.model_copy(update={"era5_dataset": "other-dataset"}))
    assert provider._target_path(**kwargs) != other._target_path(**kwargs)


@pytest.fixture
def hourly_provider(tmp_path):
    """A local CDS substitute that returns exactly the date/hour combinations requested."""
    class HourlyClient:
        def __init__(self):
            self.calls = []
            self.fail_date = None

        def retrieve(self, dataset, selection, target):
            self.calls.append(selection)
            if self.fail_date in selection["date"]:
                raise RuntimeError("temporary CDS failure")
            timestamps = [datetime.fromisoformat(f"{day}T{hour}:00")
                          for day in selection["date"] for hour in selection["time"]]
            values = np.array([290 + stamp.hour / 10 for stamp in timestamps])
            xr.Dataset(
                {"t2m": (("time", "latitude", "longitude"), values.reshape(-1, 1, 1))},
                coords={"time": np.array(timestamps, dtype="datetime64[us]"),
                        "latitude": [10.75], "longitude": [106.75]},
            ).to_netcdf(target, engine="scipy")

    client = HourlyClient()
    settings = Settings(data_dir=tmp_path, cds_url="https://example.test", cds_api_key="test")
    provider = ERA5ClimateProvider(settings, client_factory=lambda _: client,
                                  repository=ClimateRepository(tmp_path / "climate.sqlite3"))
    return provider, client


def test_extending_vietnam_day_requests_exactly_new_24_hours(hourly_provider):
    provider, client = hourly_provider
    start = datetime.fromisoformat("2025-02-01T00:00:00+07:00")
    old_end = datetime.fromisoformat("2025-02-01T23:00:00+07:00")
    new_end = datetime.fromisoformat("2025-02-02T23:00:00+07:00")
    first = request(provider, start=start, end=old_end)
    original_call_count = len(client.calls)
    expanded = request(provider, start=start, end=new_end)
    new_calls = client.calls[original_call_count:]
    requested_hours = [datetime.fromisoformat(f"{day}T{hour}:00")
                       for call in new_calls for day in call["date"] for hour in call["time"]]
    expected_new = set(hourly_timestamps(start, new_end)) - set(hourly_timestamps(start, old_end))
    assert len(requested_hours) == 24
    assert set(requested_hours) == expected_new
    assert len(expanded.data) == 48
    assert expanded.data[:24] == first.data
    assert new_calls[0]["date"] == ["2025-02-01"]
    assert new_calls[0]["time"] == [f"{hour:02d}:00" for hour in range(17, 24)]
    assert new_calls[1]["date"] == ["2025-02-02"]
    assert new_calls[1]["time"] == [f"{hour:02d}:00" for hour in range(17)]
    call_count = len(client.calls)
    assert request(provider, start=start, end=new_end) == expanded
    assert len(client.calls) == call_count


def test_retry_after_second_gap_failure_keeps_first_completed_gap(hourly_provider):
    from backend.app.providers.errors import ClimateProviderError

    provider, client = hourly_provider
    start = datetime.fromisoformat("2025-02-01T00:00:00+07:00")
    old_end = datetime.fromisoformat("2025-02-01T23:00:00+07:00")
    new_end = datetime.fromisoformat("2025-02-02T23:00:00+07:00")
    request(provider, start=start, end=old_end)
    client.fail_date = "2025-02-02"
    with pytest.raises(ClimateProviderError, match="temporary CDS failure"):
        request(provider, start=start, end=new_end)
    completed_calls = len(client.calls)
    client.fail_date = None
    result = request(provider, start=start, end=new_end)
    assert len(result.data) == 48
    assert len(client.calls) == completed_calls + 1
    assert client.calls[-1]["date"] == ["2025-02-02"]
    assert client.calls[-1]["time"] == [f"{hour:02d}:00" for hour in range(17)]
