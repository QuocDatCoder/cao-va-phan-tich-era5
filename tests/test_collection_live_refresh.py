from datetime import UTC, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest
from streamlit.testing.v1 import AppTest

from backend.app.core.config import get_settings
from backend.app.core.user_settings import get_settings_for_user
from backend.app.domain.live_refresh import adaptive_backoff
from backend.app.domain.recent_weather import VIETNAM_TIME
from backend.app.domain.schemas import ClimatePoint, DownloadRequest, Location
from backend.app.domain.sources import OPEN_METEO_BEST_MATCH_DATASET, OPEN_METEO_DATASET
from backend.app.domain.time_utils import hourly_timestamps
from backend.app.main import create_app
from backend.app.providers.errors import ClimateProviderError
from backend.app.providers.open_meteo import OpenMeteoProvider
from backend.app.services import live_refresh_service
from backend.app.services.dependencies import get_auth_service
from backend.app.storage.collection_repository import CollectionRepository, current_collection
from frontend.api_client import DashboardApiClient, DashboardApiError
from frontend.library import _shared_read


VARIABLE = "2m_temperature"
POINT = Location(latitude=21.0, longitude=105.8)
START = datetime(2026, 11, 26, 23, tzinfo=VIETNAM_TIME)
END = START + timedelta(hours=1)
ACCOUNT_PASSWORD = "fixture secure password"
ADMIN_LIST = "/api/v1/auth/admin/live-schedules"


def collection_url(cid, suffix=""):
    return f"/api/v1/collections/{cid}{suffix}"


@pytest.mark.parametrize(("seconds", "requested", "interval", "backed_off", "reason"), [
    (0, 1, 1, False, None),
    (6 * 3600 - 1, 3, 3, False, None),
    (6 * 3600, 1, 6, True, None),
    (86399, 24, 24, False, None),
    (86400, 6, 24, True, None),
    (86400, 24, 24, False, None),
    (7 * 86400 - 1, 1, 24, True, None),
    (7 * 86400, 1, None, False, "inactive_7_days"),
])
def test_adaptive_backoff(seconds, requested, interval, backed_off, reason):
    policy = adaptive_backoff(requested, seconds, True)
    assert (policy.effective_enabled, policy.effective_interval_hours, policy.backed_off,
            policy.disabled_reason) == (interval is not None, interval, backed_off, reason)


def test_disabled_policy_preserves_reason():
    policy = adaptive_backoff(3, 8 * 86400, False, "disabled_by_admin")
    assert not policy.effective_enabled
    assert policy.effective_interval_hours is None
    assert not policy.backed_off
    assert policy.disabled_reason == "disabled_by_admin"


@pytest.fixture
def live_api(tmp_path, monkeypatch):
    auth_db = tmp_path / "auth.sqlite3"
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_DB_PATH", str(auth_db))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ADMIN_USERNAME", "fixture_admin")
    monkeypatch.setenv("ADMIN_PASSWORD", ACCOUNT_PASSWORD)
    monkeypatch.setenv("ERA5_SYNC_ENABLED", "false")
    get_settings.cache_clear()
    latest = Mock(return_value=END)
    monkeypatch.setattr(live_refresh_service, "latest_weather_hour", latest)

    def fetch(location, first, last, _latest, *, dataset):
        assert location == POINT
        assert dataset == OPEN_METEO_DATASET
        yield POINT, {VARIABLE: [
            ClimatePoint(time=hour, value=300.15) for hour in hourly_timestamps(first, last)
        ]}

    provider = Mock(spec=OpenMeteoProvider)
    provider.fetch_refresh.side_effect = fetch
    resolve = Mock(return_value=provider)
    monkeypatch.setattr(live_refresh_service, "get_provider_for_source", resolve)

    try:
        with TestClient(create_app()) as client:
            register = client.post("/api/v1/auth/register", json={"username": "fixture_user", "password": ACCOUNT_PASSWORD})
            assert register.status_code == 201
            headers = {"Authorization": f"Bearer {register.json()['access_token']}"}
            me = client.get("/api/v1/auth/me", headers=headers).json()
            login = client.post("/api/v1/auth/login", json={"username": "fixture_admin", "password": ACCOUNT_PASSWORD})
            assert login.status_code == 200
            admin = {"Authorization": f"Bearer {login.json()['access_token']}"}
            yield SimpleNamespace(client=client, headers=headers, admin=admin, user_id=me["id"],
                                  db=auth_db, latest=latest, provider=provider, resolve=resolve)
    finally:
        get_settings.cache_clear()


def seed_collection(user_id, cid="weather", *, source="open_meteo", place="Hà Nội", hours=(0, 1)):
    repository = CollectionRepository(get_settings_for_user(user_id).climate_data_db_path)
    dataset = OPEN_METEO_DATASET if source == "open_meteo" else "mock"
    request = DownloadRequest(source=source, variable=VARIABLE, location=POINT, selected_place=POINT, frequency="hourly",
                              place_name=place, start_date=START.astimezone(UTC).date(),
                              end_date=END.astimezone(UTC).date(), requested_start=START, requested_end=END,
                              request_timezone="UTC+7")
    job = SimpleNamespace(job_id=cid, source=source, dataset=dataset, retrieval_variables=[VARIABLE],
                          created_at=START)
    repository.create(job, request, VARIABLE)
    repository.running(cid)
    token = current_collection.set((cid, dataset))
    try:
        repository.save(dataset=dataset, variable=VARIABLE, unit="K", requested_location=POINT,
                        grid_location=POINT, points=[
                            ClimatePoint(time=START + timedelta(hours=i), value=299.15) for i in hours
                        ])
    finally:
        current_collection.reset(token)
    repository.finish(cid, [], None)
    return repository


def set_live(api, cid="weather", enabled=True, interval=1):
    return api.client.put(collection_url(cid, "/live"), headers=api.headers,
                          json={"enabled": enabled, "interval_hours": interval})


def test_live_api_scope_limits_and_source(live_api):
    api = live_api
    path = collection_url("weather", "/live")
    for cid in ("weather", "two", "three", "four", "five", "six"):
        seed_collection(api.user_id, cid)
    seed_collection(api.user_id, "mock", source="mock")

    assert api.client.get(path).status_code == 401
    assert api.client.get(path, headers=api.admin).status_code == 404
    assert api.client.get(path, headers=api.headers).json() == {
        "enabled": False, "requested_interval_hours": 1, "effective_interval_hours": None,
        "backed_off": False, "disabled_reason": None, "last_refreshed_at": None,
    }
    assert set_live(api, interval=2).status_code == 422
    assert set_live(api, interval=True).status_code == 422
    assert set_live(api, "mock").status_code == 422
    assert api.client.post(collection_url("mock", "/refresh"), headers=api.headers).status_code == 422
    assert api.client.post(collection_url("weather", "/refresh"), headers=api.admin).status_code == 404
    for cid in ("weather", "two", "three", "four", "five"):
        assert set_live(api, cid, interval=3).status_code == 200
    assert set_live(api, "six").status_code == 409
    first = set_live(api, interval=6).json()
    assert first["enabled"] and first["effective_interval_hours"] == 6
    assert api.client.get(path, headers=api.headers).json() == first
    assert set_live(api, "five", enabled=False).status_code == 200
    assert set_live(api, "six").status_code == 200
    api.resolve.assert_not_called()


def test_api_backoff_activity_and_admin_disable(live_api):
    api = live_api
    seed_collection(api.user_id)
    assert set_live(api).status_code == 200
    path = collection_url("weather", "/live")
    for idle, hours in ((6 * 3600, 6), (86400, 24), (7 * 86400, None)):
        activity = int(time.time()) - idle
        with sqlite3.connect(api.db) as db:
            db.execute("UPDATE users SET last_active_at=? WHERE id=?", (activity, api.user_id))
        state = api.client.get(path, headers=api.headers).json()
        assert state["effective_interval_hours"] == hours
        assert state["backed_off"] == (hours is not None)
        with sqlite3.connect(api.db) as db:
            assert db.execute("SELECT last_active_at FROM users WHERE id=?", (api.user_id,)).fetchone()[0] == activity
    assert state["disabled_reason"] == "inactive_7_days"
    assert not state["enabled"]
    assert api.client.get("/api/v1/auth/me", headers=api.headers).status_code == 200
    assert api.client.get(path, headers=api.headers).json() == state
    assert set_live(api, interval=3).json()["disabled_reason"] is None

    disable = f"{ADMIN_LIST}/{api.user_id}/weather/disable"
    assert api.client.get(ADMIN_LIST, headers=api.headers).status_code == 403
    assert api.client.post(disable, headers=api.headers).status_code == 403
    assert api.client.post(disable + "-unknown", headers=api.admin).status_code == 404
    rows = api.client.get(ADMIN_LIST, headers=api.admin).json()["schedules"]
    assert rows == [{
        **api.client.get(path, headers=api.headers).json(),
        "username": "fixture_user", "user_id": api.user_id, "collection_id": "weather", "place_name": "Hà Nội",
    }]
    assert api.client.post(disable, headers=api.admin).json()["disabled_reason"] == "disabled_by_admin"
    assert api.client.get(path, headers=api.headers).json()["enabled"] is False
    assert set_live(api).json()["disabled_reason"] is None
    assert api.client.delete(collection_url("weather"), headers=api.headers).status_code == 200
    assert api.client.get(ADMIN_LIST, headers=api.admin).json()["schedules"] == []


def test_refresh_noop_and_in_place_tail_upsert(live_api):
    api = live_api
    repository = seed_collection(api.user_id)
    previous = repository.get("weather")
    path = collection_url("weather", "/refresh")
    assert api.client.post(path, headers=api.headers).json() == {
        "status": "up_to_date", "added_hours": 0, "requested_end": END.isoformat(),
    }
    api.resolve.assert_not_called()
    latest = END + timedelta(hours=3)
    api.latest.return_value = latest
    response = api.client.post(path, headers=api.headers)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "updated" and result["added_hours"] == 3
    assert result["requested_end"] == latest.isoformat()
    api.resolve.assert_called_once_with("open_meteo", user_id=api.user_id)
    api.provider.fetch_refresh.assert_called_once_with(
        POINT, (END + timedelta(hours=1)).astimezone(UTC).replace(tzinfo=None),
        latest.astimezone(UTC).replace(tzinfo=None), latest, dataset=OPEN_METEO_DATASET,
    )
    entry = repository.get("weather")
    assert entry["id"] == entry["job_id"] == previous["job_id"]
    assert entry["metadata"]["requested_end"] == latest.isoformat()
    assert entry["metadata"]["end_date"] == latest.astimezone(UTC).date().isoformat()
    assert entry["finished_at"] > previous["finished_at"]
    assert entry["status"] == "complete"
    assert entry["summary"]["available_count"] == entry["summary"]["expected_count"] == 5
    series = api.client.get(collection_url("weather", "/timeseries"), headers=api.headers).json()
    assert [point["value"] for point in series["data"]] == [26, 26, 27, 27, 27]
    assert api.client.get(collection_url("weather", "/live"), headers=api.headers).json()["last_refreshed_at"]
    assert api.client.post(path, headers=api.headers).json()["status"] == "up_to_date"
    assert api.provider.fetch_refresh.call_count == 1
    assert len(repository.list()) == 1


def test_refresh_fills_missing_hours_and_keeps_existing_data_on_error(live_api):
    api = live_api
    repository = seed_collection(api.user_id, hours=(0,))
    assert repository.get("weather")["status"] == "partial"
    path = collection_url("weather", "/refresh")
    fetch = api.provider.fetch_refresh.side_effect
    api.provider.fetch_refresh.side_effect = ClimateProviderError("fixture provider error")
    old = repository.get("weather")
    assert api.client.post(path, headers=api.headers).status_code == 502
    assert repository.get("weather") == old
    api.provider.fetch_refresh.side_effect = fetch
    result = api.client.post(path, headers=api.headers).json()
    assert result["status"] == "updated" and result["added_hours"] == 1
    assert repository.get("weather")["status"] == "complete"


def test_refresh_uses_hours_already_stored_outside_request(live_api):
    api = live_api
    seed_collection(api.user_id, hours=(0, 1, 2))
    api.latest.return_value = END + timedelta(hours=1)
    result = api.client.post(collection_url("weather", "/refresh"), headers=api.headers).json()
    assert result["status"] == "updated" and result["added_hours"] == 1
    api.resolve.assert_not_called()


def test_concurrent_refreshes_extend_a_collection_once(live_api):
    api = live_api
    seed_collection(api.user_id)
    api.latest.return_value = END + timedelta(hours=2)
    path = collection_url("weather", "/refresh")

    def refresh():
        return api.client.post(path, headers=api.headers).json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = executor.submit(refresh), executor.submit(refresh)
        results = [first.result(), second.result()]
    assert sorted((result["status"], result["added_hours"]) for result in results) == [
        ("up_to_date", 0), ("updated", 2),
    ]
    assert api.provider.fetch_refresh.call_count == 1


def test_background_schedule_respects_backoff_and_preserves_activity(live_api):
    api = live_api
    seed_collection(api.user_id)
    assert set_live(api).status_code == 200
    schedules = get_auth_service()._repository
    now = int(time.time())
    with sqlite3.connect(api.db) as db:
        db.execute("UPDATE users SET last_active_at=? WHERE id=?", (now - 86400, api.user_id))
    schedules.record_live_refresh(api.user_id, "weather", "Hà Nội", now - 3600, automatic=True)
    api.latest.return_value = END + timedelta(hours=1)

    live_refresh_service.run_live_schedules(schedules, now=now)
    api.resolve.assert_not_called()
    live_refresh_service.run_live_schedules(schedules, now=now + 23 * 3600)
    assert api.resolve.call_count == 1
    live_refresh_service.run_live_schedules(schedules, now=now + 23 * 3600 + 1)
    assert api.resolve.call_count == 1
    with sqlite3.connect(api.db) as db:
        assert db.execute("SELECT last_active_at FROM users WHERE id=?", (api.user_id,)).fetchone()[0] == now - 86400
    live_refresh_service.run_live_schedules(schedules, now=now + 6 * 86400)
    state = schedules.get_live_schedule(api.user_id, "weather", now + 6 * 86400)
    assert state["disabled_reason"] == "inactive_7_days"
    assert api.resolve.call_count == 1


@pytest.mark.parametrize(("dataset", "model"), [
    (OPEN_METEO_DATASET, "gfs_global"),
    (OPEN_METEO_BEST_MATCH_DATASET, None),
])
def test_provider_splits_missing_dates_between_archive_and_forecast(monkeypatch, dataset, model):
    provider = OpenMeteoProvider(get_settings(), None)
    fetch = Mock(return_value=({}, POINT, {}))
    monkeypatch.setattr(provider, "_fetch", fetch)
    first = datetime(2026, 11, 25, 23)
    last = datetime(2026, 11, 27, 1)
    latest = last.replace(tzinfo=UTC).astimezone(VIETNAM_TIME)
    assert list(provider.fetch_refresh(POINT, first, last, latest, dataset=dataset)) == [(POINT, {}), (POINT, {})]
    archive, recent = fetch.call_args_list
    assert archive.args == (POINT, first.date(), first.date())
    assert archive.kwargs == {"refresh_archive": True, "model": model}
    assert recent.args == (POINT, datetime(2026, 11, 26).date(), last.date())
    assert recent.kwargs["model"] == model
    assert recent.kwargs["live_interval"] == (datetime(2026, 11, 26, tzinfo=UTC), last.replace(tzinfo=UTC))


@pytest.fixture
def chart_app(live_api, monkeypatch):
    api = live_api
    seed_collection(api.user_id)

    def call(method, path, *, track_activity=True, **kwargs):
        headers = dict(api.headers)
        if not track_activity:
            headers["X-Weather-Activity"] = "passive"
        response = api.client.request(method, path, headers=headers, **kwargs)
        if response.is_error:
            raise DashboardApiError(response.json()["detail"], status_code=response.status_code)
        return response.json()

    refresh = Mock(side_effect=lambda cid, automatic=False: call(
        "POST", collection_url(cid, "/refresh"), params={"automatic": str(automatic).lower()},
    ))
    monkeypatch.setattr(DashboardApiClient, "get_collection", lambda self, cid:
                        call("GET", collection_url(cid), track_activity=self.track_activity))
    monkeypatch.setattr(DashboardApiClient, "query_collection", lambda self, cid, op, params:
                        call("GET", collection_url(cid, f"/{op}"), params=params, track_activity=self.track_activity))
    monkeypatch.setattr(DashboardApiClient, "get_collection_live", lambda self, cid:
                        call("GET", collection_url(cid, "/live")))
    monkeypatch.setattr(DashboardApiClient, "update_collection_live", lambda self, cid, enabled, interval:
                        call("PUT", collection_url(cid, "/live"), json={"enabled": enabled, "interval_hours": interval}))
    monkeypatch.setattr(DashboardApiClient, "refresh_collection", refresh)

    def script():
        from frontend.api_client import DashboardApiClient
        from frontend.library import render_saved

        render_saved(lambda source: DashboardApiClient(base_url="http://live-fixture", source=source), charts=True)

    _shared_read.clear()
    app = AppTest.from_function(script, default_timeout=15)
    app.session_state["chart_collection"] = "weather"
    app.session_state["chart_details_visible"] = True
    yield app, refresh
    _shared_read.clear()


def test_chart_controls_toggle_interval_and_manual_refresh(chart_app, live_api):
    app, refresh = chart_app
    app.run()
    assert not app.exception
    toggle = app.toggle(key="chart_live_enabled_weather")
    assert toggle.label == "Tự động cập nhật" and not toggle.value
    select = app.selectbox(key="chart_live_interval_weather")
    assert select.value == 1 and select.options == ["1 giờ", "3 giờ", "6 giờ", "24 giờ"]
    toggle.set_value(True).run()
    assert not app.exception
    assert app.toggle(key="chart_live_enabled_weather").value
    app.selectbox(key="chart_live_interval_weather").set_value(3).run()
    assert live_api.client.get(collection_url("weather", "/live"), headers=live_api.headers).json()["requested_interval_hours"] == 3

    button = app.button(key="chart_refresh_weather")
    assert button.label == "Cập nhật ngay"
    assert button.proto.icon == ":material/sync:"
    new_end = END + timedelta(hours=2)
    live_api.latest.return_value = new_end
    button.click().run()
    assert not app.exception
    refresh.assert_any_call("weather")
    assert app.date_input(key="chart_range_end_weather").value == new_end.date()
    assert app.time_input(key="chart_hour_end_weather").value.hour == new_end.hour
    assert app.session_state["chart_result"][2]["requested_end"] == new_end.isoformat()
    assert app.get("plotly_chart")


def test_chart_auto_refresh_keeps_idle_backoff_and_replots(chart_app, live_api):
    app, refresh = chart_app
    app.run()
    app.toggle(key="chart_live_enabled_weather").set_value(True).run()
    refresh.reset_mock()
    now = int(time.time())
    with sqlite3.connect(live_api.db) as db:
        db.execute("UPDATE users SET last_active_at=? WHERE id=?", (now - 6 * 3600, live_api.user_id))
        db.execute("UPDATE collection_live_schedules SET last_refreshed_at=? WHERE user_id=?",
                   (datetime.fromtimestamp(now - 6 * 3600, UTC).isoformat(), live_api.user_id))
    latest = END + timedelta(hours=1)
    live_api.latest.return_value = latest
    app.run()

    assert not app.exception
    refresh.assert_called_once_with("weather", automatic=True)
    assert app.session_state["chart_result"][2]["requested_end"] == latest.isoformat()
    assert live_api.client.get(collection_url("weather", "/live"), headers=live_api.headers).json()["backed_off"]
    with sqlite3.connect(live_api.db) as db:
        assert db.execute("SELECT last_active_at FROM users WHERE id=?", (live_api.user_id,)).fetchone()[0] == now - 6 * 3600


@pytest.mark.parametrize("status_code", [404, 405])
def test_chart_legacy_api_fails_gracefully(chart_app, monkeypatch, status_code):
    app, refresh = chart_app
    app.run()
    refresh.side_effect = DashboardApiError("legacy", status_code=status_code)
    app.button(key="chart_refresh_weather").click().run()
    assert not app.exception
    assert any("chưa khả dụng" in warning.value for warning in app.warning)
    monkeypatch.setattr(DashboardApiClient, "get_collection_live",
                        Mock(side_effect=DashboardApiError("legacy", status_code=status_code)))
    app.run()
    assert not app.exception
    assert any("chưa khả dụng" in caption.value for caption in app.caption)


def test_admin_live_page_filters_and_force_disable(live_api, monkeypatch):
    api = live_api
    seed_collection(api.user_id)
    seed_collection(api.user_id, "old", place="Đà Nẵng")
    seed_collection(api.user_id, "paused", place="Huế")
    assert set_live(api, interval=24).status_code == 200
    assert set_live(api, "old", interval=1).status_code == 200
    assert set_live(api, "paused", enabled=False).status_code == 200
    with sqlite3.connect(api.db) as db:
        db.execute("UPDATE users SET last_active_at=? WHERE id=?", (int(time.time()) - 86400, api.user_id))

    def disable(user_id, cid):
        response = api.client.post(f"{ADMIN_LIST}/{user_id}/{cid}/disable", headers=api.admin)
        assert response.status_code == 200
        return response.json()

    disable_call = Mock(side_effect=disable)
    monkeypatch.setattr(DashboardApiClient, "current_user", lambda self: {"username": "fixture_admin", "is_admin": True})
    monkeypatch.setattr(DashboardApiClient, "admin_live_schedules",
                        lambda self: api.client.get(ADMIN_LIST, headers=api.admin).json()["schedules"])
    monkeypatch.setattr(DashboardApiClient, "admin_disable_live_schedule", disable_call)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "admin_app.py"), default_timeout=15)
    app.query_params["view"] = "live-schedules"
    app.session_state["admin_access_token"] = "test-admin-token"
    app.session_state["admin_expires_at"] = int(time.time()) + 3600
    app.run()

    assert not app.exception
    assert app.header[0].value == "Tự động cập nhật"
    assert [(metric.label, metric.value) for metric in app.metric] == [
        ("Đang bật", "1"), ("Đang tự giãn chu kỳ", "1"), ("Đã tắt", "1"),
    ]
    for heading in ("Tài khoản", "Bộ dữ liệu", "Chu kỳ gốc", "Chu kỳ thực tế",
                    "Trạng thái", "Cập nhật gần nhất", "Thao tác"):
        assert f"**{heading}**" in [item.value for item in app.markdown]
    assert {"Hà Nội", "Đà Nẵng", "Huế"} <= {item.value for item in app.markdown}
    assert app.text_input(key="admin_live_search").label == "Tìm kiếm"
    assert app.selectbox(key="admin_live_status").options == ["Tất cả", "Đang bật", "Đang tự giãn", "Đã tắt"]
    assert app.selectbox(key="admin_live_interval").options == ["Tất cả", "1 giờ", "3 giờ", "6 giờ", "24 giờ"]
    app.text_input(key="admin_live_search").set_value("đà NẴNG").run()
    app.selectbox(key="admin_live_status").select("Đang tự giãn").run()
    app.selectbox(key="admin_live_interval").select("24 giờ").run()
    assert "Đà Nẵng" in [item.value for item in app.markdown]
    assert "Hà Nội" not in [item.value for item in app.markdown]
    button = app.button(key=f"admin_live_disable_{api.user_id}_old")
    assert button.label == "Tắt"
    button.click().run()
    assert not app.exception
    disable_call.assert_called_once_with(api.user_id, "old")
    assert [(metric.label, metric.value) for metric in app.metric][-2:] == [
        ("Đang tự giãn chu kỳ", "0"), ("Đã tắt", "2"),
    ]
