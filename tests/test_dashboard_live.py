from datetime import date

from fastapi.testclient import TestClient
import pytest
from streamlit.testing.v1 import AppTest

from backend.app.core.config import Settings, get_settings
from backend.app.domain.schemas import DownloadArea, SynchronizationRequest
from backend.app.main import app
from backend.app.providers.mock_provider import MockClimateProvider
from backend.app.services.climate_service import ClimateService
from backend.app.services.climate_analysis_service import ClimateAnalysisService
from backend.app.services.dependencies import (
    get_climate_service, get_climate_analysis_service, get_synchronization_service,
)
from backend.app.services.download_job_service import DownloadJobService
from backend.app.services.synchronization_service import LatestAvailableDateProvider, SynchronizationService
from backend.app.storage.job_repository import JobRepository
from backend.app.storage.synchronization_repository import SynchronizationRepository
from frontend.api_client import DashboardApiClient, DashboardApiError
from frontend.dashboard import _cached_get


@pytest.fixture
def live_app(tmp_path, monkeypatch):
    provider = MockClimateProvider(tmp_path)
    climate = ClimateService(provider)
    sync = SynchronizationService(
        DownloadJobService(provider, JobRepository(tmp_path / "jobs.sqlite3")),
        SynchronizationRepository(tmp_path / "jobs.sqlite3"), LatestAvailableDateProvider(5),
    )
    settings = Settings(_env_file=None, climate_provider="mock", era5_sync_enabled=False)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_climate_service] = lambda: climate
    app.dependency_overrides[get_climate_analysis_service] = lambda: ClimateAnalysisService(climate)
    app.dependency_overrides[get_synchronization_service] = lambda: sync
    api = TestClient(app)
    calls = []

    def get(self, path, params=None):
        calls.append((path, params))
        result = api.get(path, params=params)
        if not result.is_success:
            raise DashboardApiError(str(result.json()))
        return result.json()

    def post(self, path, payload):
        calls.append((path, payload))
        result = api.post(path, json=payload)
        if not result.is_success:
            raise DashboardApiError(str(result.json()))
        return result.json()

    monkeypatch.setenv("API_BASE_URL", "http://test/" + tmp_path.name)
    monkeypatch.setattr(DashboardApiClient, "_get", get)
    monkeypatch.setattr(DashboardApiClient, "_post", post)
    _cached_get.clear()
    try:
        yield AppTest.from_file("../app.py"), sync, calls
    finally:
        app.dependency_overrides.clear()
        _cached_get.clear()
        api.close()


def finish_sync(service, end_date):
    queued = service.synchronize(SynchronizationRequest(
        variable="2m_temperature", start_date=date(2025, 1, 30),
        latest_available_date=end_date,
        area=DownloadArea(north=11, south=10, west=106, east=107),
    ))
    service.run_synchronization(queued.sync_id)


def timeseries_calls(calls):
    return [params for path, params in calls if path.endswith("/climate/timeseries")]


def test_dashboard_refetches_after_external_sync_without_moving_historical_dates(live_app):
    ui, sync, calls = live_app
    ui.run(timeout=30)
    assert not ui.exception
    first = timeseries_calls(calls)[-1]
    count = len(timeseries_calls(calls))
    finish_sync(sync, date(2025, 2, 1))
    ui.run(timeout=30)
    assert not ui.exception
    assert len(timeseries_calls(calls)) > count
    assert timeseries_calls(calls)[-1] == first


def test_follow_mode_moves_window_only_after_completed_sync(live_app):
    ui, sync, calls = live_app
    finish_sync(sync, date(2025, 2, 1))
    ui.run(timeout=30)
    ui.toggle[0].set_value(True)
    ui.run(timeout=30)
    assert not ui.exception
    assert timeseries_calls(calls)[-1]["start"] == "2025-01-31T00:00:00+07:00"
    assert timeseries_calls(calls)[-1]["end"] == "2025-02-01T23:00:00+07:00"
    finish_sync(sync, date(2025, 2, 2))
    ui.run(timeout=30)
    assert not ui.exception
    assert timeseries_calls(calls)[-1]["start"] == "2025-02-01T00:00:00+07:00"
    assert timeseries_calls(calls)[-1]["end"] == "2025-02-02T23:00:00+07:00"


def test_auto_follow_starts_sync_once_and_then_renders_completed_window(live_app):
    ui, sync, calls = live_app
    ui.run(timeout=30)
    ui.toggle[0].set_value(True).run(timeout=30)
    assert not ui.exception
    assert not [path for path, _ in calls if path.endswith("/synchronization/run")]
    ui.checkbox[0].set_value(True).run(timeout=30)
    assert not ui.exception
    ui.run(timeout=30)
    assert not ui.exception
    assert len([path for path, _ in calls if path.endswith("/synchronization/run")]) == 1
    assert ui.session_state["live_states"][0]["status"] == "completed"
    synced = ui.session_state["live_states"][0]["last_synced_date"]
    assert timeseries_calls(calls)[-1]["end"] == synced + "T23:00:00+07:00"


@pytest.mark.parametrize("label,variable", [
    ("Điểm sương 2m", "dewpoint"), ("Tốc độ gió 10m", "wind_speed"),
    ("Hướng gió 10m", "wind_direction"), ("Bức xạ mặt trời", "solar_radiation"),
])
def test_weather_variable_renders_in_dashboard(live_app, label, variable):
    ui, sync, calls = live_app
    ui.run(timeout=30)
    ui.selectbox[0].select(label).run(timeout=30)
    assert not ui.exception
    assert not ui.error
    assert timeseries_calls(calls)[-1]["variable"] == variable


@pytest.mark.parametrize("label", ["Nhiệt độ", "Lượng mưa", "Hướng gió 10m", "Bức xạ mặt trời"])
def test_daily_aggregation_renders_in_dashboard(live_app, label):
    ui, sync, calls = live_app
    ui.run(timeout=30)
    ui.selectbox[0].select(label).run(timeout=30)
    ui.selectbox[1].select("Theo ngày").run(timeout=30)
    assert not ui.exception
    assert not ui.error
    aggregate_calls = [params for path, params in calls if path.endswith("/climate/aggregate")]
    assert aggregate_calls[-1]["frequency"] == "daily"
