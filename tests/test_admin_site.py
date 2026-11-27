import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

from frontend import admin_site
from frontend.admin import shell
from frontend.admin.collections import RECENT_FAILURES, STALE_JOBS
from frontend.admin.live_schedules import LIVE_AUTO_DISABLED
from frontend.api_client import DashboardApiClient, DashboardApiError


@pytest.fixture
def admin_app():
    entrypoint = Path(__file__).resolve().parents[1] / "admin_app.py"
    return AppTest.from_file(str(entrypoint), default_timeout=15)


@pytest.fixture
def admin_session(monkeypatch):
    payload = {
        "access_token": "fixture-admin-token",
        "expires_at": int(time.time()) + 3600,
        "username": "admin_user",
        "is_admin": True,
    }

    def current_user(api):
        assert api.access_token == payload["access_token"]
        return {"id": 1, "username": payload["username"], "is_admin": True}

    monkeypatch.setattr(DashboardApiClient, "current_user", current_user)
    monkeypatch.setattr(DashboardApiClient, "list_users", lambda api: [])
    monkeypatch.setattr(DashboardApiClient, "admin_collection_history", lambda api: [])
    monkeypatch.setattr(DashboardApiClient, "admin_live_schedules", lambda api: [])
    monkeypatch.setattr(DashboardApiClient, "admin_overview", lambda api: {
        "total_users": 1, "active_users": 1, "disabled_users": 0, "admin_users": 1, "new_today": 0,
    })
    return payload


@pytest.fixture
def collection_history(monkeypatch):
    completed = {
        "username": "an", "user_id": 2, "job_id": "9ba760c9-2345-6789",
        "variable": "2m_temperature", "start_date": "2026-11-01", "end_date": "2026-11-03",
        "frequency": "daily", "status": "completed", "progress": 100,
        "created_at": "2026-11-27T19:05:06.123456", "completed_at": "2026-11-28T03:30:20+07:00",
    }
    history = [
        completed,
        {**completed, "job_id": "98765432-1111", "frequency": "hourly",
         "status": "running", "progress": 25, "completed_at": None},
        {**completed, "username": "binh", "user_id": 3, "job_id": "12345678-2222", "frequency": "monthly"},
        {**completed, "username": "binh", "user_id": 3, "job_id": "45678901-3333",
         "frequency": "yearly", "status": "cancelled"},
    ]
    monkeypatch.setattr(DashboardApiClient, "admin_collection_history", lambda api: history)


def _open_admin_view(app, session, view):
    app.session_state["admin_restore_ready"] = True
    app.session_state["admin_access_token"] = session["access_token"]
    app.session_state["admin_expires_at"] = session["expires_at"]
    app.query_params["view"] = view
    app.run()
    assert not app.exception


def _submit_login(app, username):
    app.text_input(key="admin_login_username").set_value(username)
    app.text_input(key="admin_login_password").set_value("fixture password")
    app.button(key="admin_login_submit").click().run()


def test_unauthenticated_admin_site_shows_login_form(admin_app, monkeypatch):
    monkeypatch.delenv("MAIN_APP_URL", raising=False)
    admin_app.run()

    assert not admin_app.exception
    assert admin_app.header[0].value == "Đăng nhập Quản trị"
    assert admin_app.caption[0].value == "Weather Hub Admin"
    assert admin_app.text_input(key="admin_login_username").label == "Tên đăng nhập"
    password = admin_app.text_input(key="admin_login_password")
    assert password.label == "Mật khẩu"
    assert password.proto.type == password.proto.PASSWORD
    submit = admin_app.button(key="admin_login_submit")
    assert submit.label == "Đăng nhập quản trị"
    assert submit.proto.type == "primary"
    assert not admin_app.get("link_button")


def test_admin_login_opens_dashboard_and_writes_browser_session(admin_app, admin_session, monkeypatch):
    login = Mock(return_value=admin_session)
    bridge = Mock(wraps=admin_site._BRIDGE)
    monkeypatch.setattr(DashboardApiClient, "login", login)
    monkeypatch.setattr(shell, "_BRIDGE", bridge)
    admin_app.run()
    _submit_login(admin_app, "admin_user")

    login.assert_called_once_with("admin_user", "fixture password")
    assert not admin_app.exception
    assert admin_app.header[0].value == "Tổng quan hệ thống"
    assert not admin_app.text_input
    assert [info.value for info in admin_app.info] == ["Chưa có lịch sử thu thập."]
    assert len(admin_app.get("echarts_chart")) == 1
    assert admin_app.success[0].value == "Hệ thống vận hành ổn định, chưa có cảnh báo cần xử lý."
    assert admin_app.session_state["admin_access_token"] == admin_session["access_token"]
    assert admin_app.session_state["admin_expires_at"] == admin_session["expires_at"]
    assert admin_app.session_state["admin_username"] == "admin_user"
    data = bridge.call_args.kwargs["data"]
    assert data["mode"] == "write"
    assert data["session"] == {
        "access_token": admin_session["access_token"], "expires_at": admin_session["expires_at"],
    }


@pytest.mark.parametrize("has_saved_session", [False, True])
def test_admin_waits_for_browser_before_showing_login(admin_app, admin_session, monkeypatch, has_saved_session):
    bridge = SimpleNamespace(restored=None)
    monkeypatch.setattr(shell, "_BRIDGE", lambda **kwargs: bridge)
    admin_app.session_state["admin_restore_ready"] = False
    admin_app.run()

    assert not admin_app.exception
    assert not admin_app.text_input
    assert admin_app.caption[0].value == "Đang khôi phục phiên quản trị…"

    bridge.restored = {"session": admin_session if has_saved_session else None}
    admin_app.run()

    assert not admin_app.exception
    assert admin_app.session_state["admin_restore_ready"] is True
    assert admin_app.header[0].value == ("Tổng quan hệ thống" if has_saved_session else "Đăng nhập Quản trị")


@pytest.mark.parametrize("revocation_fails", [False, True])
def test_admin_login_rejects_regular_user(admin_app, monkeypatch, revocation_fails):
    token = "fixture-user-token"
    logged_out = []
    monkeypatch.setattr(DashboardApiClient, "login", Mock(return_value={"access_token": token, "is_admin": False}))

    def logout(api):
        logged_out.append(api.access_token)
        if revocation_fails:
            raise DashboardApiError("Unavailable", status_code=503)

    monkeypatch.setattr(DashboardApiClient, "logout", logout)
    admin_app.run()
    _submit_login(admin_app, "regular_user")

    assert logged_out == [token]
    assert not admin_app.exception
    assert [error.value for error in admin_app.error] == ["Tài khoản này không có quyền quản trị."]
    assert admin_app.header[0].value == "Đăng nhập Quản trị"
    assert "admin_access_token" not in admin_app.session_state
    assert "admin_expires_at" not in admin_app.session_state
    assert "admin_username" not in admin_app.session_state


def test_admin_handoff_still_opens_dashboard(admin_app, admin_session, monkeypatch):
    redeem = Mock(return_value=admin_session)
    monkeypatch.setattr(DashboardApiClient, "redeem_admin_handoff", redeem)
    admin_app.query_params["handoff"] = "fixture-handoff-ticket"
    admin_app.run()

    redeem.assert_called_once_with("fixture-handoff-ticket")
    assert not admin_app.exception
    assert "handoff" not in admin_app.query_params
    assert admin_app.header[0].value == "Tổng quan hệ thống"
    assert admin_app.session_state["admin_access_token"] == admin_session["access_token"]


@pytest.fixture
def operational_data(monkeypatch):
    now = datetime.now(UTC)
    account = {"username": "an", "user_id": 2, "place_name": "Hà Nội"}
    history = [
        {**account, "job_id": "done", "status": "completed", "created_at": now.isoformat()},
        {**account, "job_id": "old-job", "status": "running",
         "created_at": (now - timedelta(days=9)).isoformat(), "updated_at": now.isoformat()},
        {**account, "job_id": "stuck", "status": "queued",
         "created_at": (now - timedelta(hours=2)).isoformat()},
        {**account, "job_id": "failed", "status": "failed", "collection_status": "interrupted",
         "created_at": (now - timedelta(hours=5)).isoformat(), "completed_at": now.isoformat()},
        {**account, "job_id": "cancelled", "status": "cancelled", "place_name": "Đà Nẵng",
         "created_at": (now - timedelta(days=1)).isoformat()},
    ]
    schedules = [
        {**account, "collection_id": f"live-{i}", "enabled": True, "backed_off": i == 0,
         "requested_interval_hours": 1, "effective_interval_hours": 6 if i == 0 else 1}
        for i in range(4)
    ] + [
        {**account, "collection_id": "auto-off", "enabled": False, "disabled_reason": "inactive_7_days"},
        {**account, "collection_id": "admin-off", "enabled": False, "disabled_reason": "disabled_by_admin"},
        {**account, "collection_id": "manual", "enabled": False},
    ]
    logs = [
        {"id": i, "action": "create_user", "actor_username": f"operator-{i}",
         "target_type": "user", "target_id": str(i), "detail": f"chi tiết {i}", "created_at": now.timestamp() + i}
        for i in range(10)
    ]
    overview = Mock(return_value={
        "total_users": 8, "active_users": 7, "active_7d_users": 6, "active_sessions": 4,
        "disabled_users": 1, "new_today": 2, "total_collections": 17,
        "total_observations": 12345, "total_storage_bytes": 14889779,
        "scheduler_status": {
            "running": True, "next_run_at": "2026-11-28T02:00:00Z", "last_tick_at": "2026-11-28T01:59:00Z",
        },
        "recent_audit_logs": logs,
    })
    monkeypatch.setattr(DashboardApiClient, "admin_overview", overview)
    monkeypatch.setattr(DashboardApiClient, "admin_collection_history", lambda api: history)
    monkeypatch.setattr(DashboardApiClient, "admin_live_schedules", lambda api: schedules)
    return overview, logs


@pytest.mark.parametrize(("theme", "accent"), [("light", "#0f766e"), ("dark", "#60a5fa")])
def test_admin_overview_health_kpis_and_operational_charts(
    admin_app, admin_session, operational_data, monkeypatch, theme, accent,
):
    overview, _ = operational_data
    echarts = Mock(wraps=shell.st.echarts_chart)
    monkeypatch.setattr(shell.st, "echarts_chart", echarts)
    admin_app.context["theme"] = theme
    _open_admin_view(admin_app, admin_session, "overview")

    assert {"Backend API", "Live Scheduler", "Nguồn mặc định", "Lưu trữ"} <= {
        caption.value for caption in admin_app.caption
    }
    assert {":green-badge[:material/check_circle: Trực tuyến]", ":green-badge[:material/sync: Đang chạy · quét 1 phút]"} <= {
        badge.value for badge in admin_app.markdown
    }
    assert "09:00 · 28/11/2026" in " ".join(caption.value for caption in admin_app.caption)
    assert "08:59 · 28/11/2026" in " ".join(caption.value for caption in admin_app.caption)
    assert {"**Open-Meteo GFS Global**", "**14.2 MB · 12,345 điểm quan trắc**"} <= {
        text.value for text in admin_app.markdown
    }
    assert [(card.label, card.value) for card in admin_app.metric] == [
        ("Người dùng hoạt động (7 ngày)", "6/8"), ("Kho dữ liệu & Dung lượng", "17 bộ"),
        ("Thu thập (7 ngày)", "4 tác vụ"), ("Cập nhật tự động (Live)", "4 đang bật"),
    ]
    assert [card.delta for card in admin_app.metric] == [
        "+2 hôm nay", "14.2 MB", "25% thành công", "~76 lượt/ngày (-21%)",
    ]
    assert admin_app.subheader[0].value == "Xu hướng thu thập theo ngày"
    assert len(admin_app.get("echarts_chart")) == 2
    trend, donut = echarts.call_args_list
    spec, = trend.args
    assert spec["color"][0] == accent
    assert list(spec["dataset"]["source"].sum(numeric_only=True)) == [1, 1, 2]
    assert {series["stack"] for series in spec["series"]} == {"jobs"}
    assert trend.kwargs["alt"]
    pie, = donut.args[0]["series"]
    assert pie["type"] == "pie"
    assert {row["name"]: row["value"] for row in pie["data"]} == {
        "Đang bật": 3, "Đang tự giãn": 1, "Tự tắt (7 ngày)": 1, "Admin đã tắt": 1,
    }
    assert donut.kwargs["alt"]

    admin_app.segmented_control(key="admin_trend_period").set_value(14)
    admin_app.segmented_control(key="admin_chart_view").set_value("Top địa điểm").run()
    assert not admin_app.exception
    trend, places = echarts.call_args_list[-2:]
    assert len(trend.args[0]["dataset"]["source"]) == 14
    assert places.args[0]["dataset"]["source"]["Địa điểm"].tolist() == ["Hà Nội", "Đà Nẵng"]
    assert places.args[0]["yAxis"]["type"] == "category"
    assert places.kwargs["alt"]
    calls = overview.call_count
    admin_app.button(key="admin_page_refresh").click().run()
    assert overview.call_count == calls + 1


@pytest.mark.parametrize(("button", "destination", "filter_key", "status"), [
    ("admin_attention_0", "collections", "admin_history_status", RECENT_FAILURES),
    ("admin_attention_1", "collections", "admin_history_status", STALE_JOBS),
    ("admin_attention_3", "live-schedules", "admin_live_view_mode", "Hệ thống / Admin tắt"),
    ("admin_attention_limit_an", "live-schedules", "admin_live_search", "an"),
])
def test_admin_overview_attention_opens_filtered_view(
    admin_app, admin_session, operational_data, button, destination, filter_key, status,
):
    _open_admin_view(admin_app, admin_session, "overview")
    assert "Cần chú ý ngay" in {header.value for header in admin_app.subheader}
    assert {f"1 tác vụ · {RECENT_FAILURES}", f"1 tác vụ · {STALE_JOBS}", "an · 4/5 lịch Live đang bật"} <= {
        text.value for text in admin_app.text
    }
    admin_app.button(key=button).click().run()
    assert not admin_app.exception
    assert admin_app.query_params["view"] == destination
    assert admin_app.session_state[filter_key] == status
    if destination == "collections":
        frame = admin_app.dataframe[0].value
        assert len(frame) == 1
    elif status == "Hệ thống / Admin tắt":
        frame = admin_app.dataframe[0].value
        assert "auto-off" in frame["Mã bộ"].tolist()


def test_admin_overview_opens_full_audit_log(admin_app, admin_session, operational_data, monkeypatch):
    _, logs = operational_data
    audit = Mock(return_value=logs)
    monkeypatch.setattr(DashboardApiClient, "admin_audit_logs", audit)
    _open_admin_view(admin_app, admin_session, "overview")
    audit.assert_not_called()
    assert [entry.value for entry in admin_app.text if entry.value.startswith("operator-")] == [
        f"operator-{i} → Tài khoản · {i}" for i in range(9, 1, -1)
    ]
    assert [entry.value for entry in admin_app.text].count("Tạo tài khoản") == 8
    assert "chi tiết 9" in [entry.value for entry in admin_app.text]

    admin_app.button(key="admin_open_audit").click().run()
    assert not admin_app.exception
    audit.assert_called_once_with(limit=100)
    assert admin_app.dataframe[0].value["Người thực hiện"].tolist() == [f"operator-{i}" for i in range(9, -1, -1)]


def test_admin_collections_filters_formats_and_exports_history(admin_app, admin_session, collection_history, monkeypatch):
    monkeypatch.setattr(DashboardApiClient, "list_users", lambda api: [
        {"username": "binh"}, {"username": "an"}, {"username": "chi"},
    ])
    download = Mock(wraps=admin_site.st.download_button)
    monkeypatch.setattr(admin_site.st, "download_button", download)
    sidebar = Mock(wraps=admin_site._SIDEBAR)
    monkeypatch.setattr(shell, "_SIDEBAR", sidebar)
    _open_admin_view(admin_app, admin_session, "collections")

    assert admin_app.header[0].value == "Quản lý thu thập"
    assert admin_app.caption[0].value == "Giám sát tác vụ thu thập, độ phủ dữ liệu và chất lượng kho khí tượng (UTC+7)."
    navigation = sidebar.call_args.kwargs["data"]["navigation"]
    assert [(item["id"], item["label"], item["icon"]) for item in navigation] == [
        ("overview", "Tổng quan", "dashboard"), ("accounts", "Quản lý người dùng", "manage_accounts"),
        ("collections", "Quản lý thu thập", "cloud_download"), ("live-schedules", "Quản lý cập nhật", "sync"),
    ]
    user = admin_app.selectbox(key="admin_history_user")
    assert user.label == "Người dùng"
    assert user.options == ["Tất cả người dùng", "an", "binh", "chi"]
    frame = admin_app.dataframe[0].value
    assert list(frame.columns) == [
        "Tài khoản", "Địa điểm", "Khoảng dữ liệu", "Biến & Nguồn", "Độ phủ (%)",
        "Điểm dữ liệu", "Trạng thái", "Thời lượng", "Tạo lúc (UTC+7)", "Live",
    ]

    user.set_value("an")
    admin_app.segmented_control(key="admin_history_status").set_value("Hoàn tất").run()
    assert not admin_app.exception
    frame = admin_app.dataframe[0].value
    assert len(frame) == 1
    assert frame.loc[0, "Tài khoản"] == "an"
    assert frame.loc[0, "Trạng thái"] == "Hoàn tất"
    assert frame.loc[0, "Độ phủ (%)"] == 100
    button, = admin_app.get("download_button")
    assert button.proto.label == "Xuất CSV"
    assert download.call_args.kwargs["icon"] == ":material/download:"
    assert download.call_args.kwargs["on_click"] == "ignore"
    assert download.call_args.args[1].decode("utf-8-sig").splitlines()[0] == ",".join(frame.columns)

    admin_app.segmented_control(key="admin_history_status").set_value("Thất bại").run()
    assert not admin_app.dataframe
    assert admin_app.info[0].value == "Chưa có lịch sử thu thập phù hợp."


@pytest.fixture
def user_directory(monkeypatch):
    now = int(time.time())
    accounts = [
        {"id": 1, "username": "admin_user", "is_admin": True, "is_active": True,
         "last_active_at": now, "active_sessions": 1},
        {"id": 2, "username": "an", "is_admin": False, "is_active": True,
         "created_at": now - 1000, "last_login_at": now - 500, "last_active_at": now - 300,
         "idle_seconds": 300, "active_sessions": 2, "collections_count": 3, "observations_count": 321,
         "storage_bytes": 12698, "live_active_count": 2, "live_schedules_count": 4},
        {"id": 3, "username": "han", "is_admin": False, "is_active": False,
         "idle_seconds": 9 * 86400},
        {"id": 4, "username": "reserved", "is_admin": True, "is_active": True, "is_fixed_admin": True},
    ]
    load = Mock(return_value=accounts)
    monkeypatch.setattr(DashboardApiClient, "list_users", load)
    return load


def _select_user(app, row):
    app.session_state["admin_users_table"] = {"selection": {"rows": [row], "columns": [], "cells": []}}
    app.run()
    assert not app.exception


def test_admin_users_kpis_filters_sort_and_inspector(admin_app, admin_session, user_directory):
    _open_admin_view(admin_app, admin_session, "accounts")
    assert admin_app.header[0].value == "Quản lý người dùng"
    assert [(card.label, card.value) for card in admin_app.metric] == [
        ("Tổng tài khoản", "4"), ("Hoạt động (7 ngày)", "2"),
        ("Phiên đang mở", "3"), ("Đã vô hiệu hóa", "1"),
    ]
    table, = admin_app.dataframe
    assert list(table.value.columns) == [
        "Tài khoản", "ID", "Vai trò", "Trạng thái", "Hoạt động gần nhất",
        "Phiên mở", "Kho dữ liệu", "Lịch Live", "Dung lượng",
    ]
    assert table.proto.alt == "Danh sách người dùng"
    assert list(table.proto.selection_mode) == [table.proto.SINGLE_ROW]
    assert table.value["Tài khoản"].tolist() == ["admin_user", "an", "han", "reserved"]
    assert any(caption.value.startswith("Chọn một tài khoản trong bảng") for caption in admin_app.caption)

    _select_user(admin_app, 1)
    text = [entry.value for entry in admin_app.text]
    assert {"an · #2", "3 bộ", "321", "2/5", "12.4 KB"} <= set(text)
    assert any(value.endswith(" · 5 phút trước") for value in text)
    assert admin_app.button(key="admin_user_role_2").label == "Cấp quyền quản trị"
    assert admin_app.button(key="admin_user_status_2").label == "Vô hiệu hóa"

    admin_app.selectbox(key="admin_user_sort").set_value("Dung lượng giảm dần").run()
    assert not admin_app.exception
    assert admin_app.dataframe[0].value["Tài khoản"].iloc[0] == "an"
    assert "Hồ sơ tài khoản" not in [item.value for item in admin_app.subheader]

    admin_app.text_input(key="admin_user_search").set_value("#3")
    admin_app.segmented_control(key="admin_user_status").set_value("Đã khóa")
    admin_app.selectbox(key="admin_user_role").set_value("Người dùng").run()
    assert not admin_app.exception
    assert admin_app.dataframe[0].value["Tài khoản"].tolist() == ["han"]
    _select_user(admin_app, 0)
    assert admin_app.button(key="admin_user_status_3").label == "Kích hoạt lại"

    admin_app.text_input(key="admin_user_search").set_value("#999").run()
    assert not admin_app.exception
    assert admin_app.dataframe[0].value.empty
    assert any(caption.value.startswith("Chọn một tài khoản trong bảng") for caption in admin_app.caption)


@pytest.mark.parametrize(("row", "account_id"), [(0, 1), (3, 4)])
def test_admin_users_protects_self_and_fixed_admin(admin_app, admin_session, user_directory, row, account_id):
    _open_admin_view(admin_app, admin_session, "accounts")
    _select_user(admin_app, row)
    assert ":gray-badge[:material/lock: Tài khoản được bảo vệ]" in [item.value for item in admin_app.markdown]
    keys = {button.key for button in admin_app.button}
    assert f"admin_user_live_{account_id}" in keys
    assert not {f"admin_user_{action}_{account_id}" for action in ("role", "status", "password", "revoke", "delete")} & keys


@pytest.mark.parametrize(("button", "destination", "filter_key"), [
    ("admin_user_collections_2", "collections", "admin_history_user"),
    ("admin_user_live_2", "live-schedules", "admin_live_search"),
])
def test_admin_users_quick_jump_is_scoped_to_the_selected_username(
    admin_app, admin_session, user_directory, collection_history, monkeypatch, button, destination, filter_key,
):
    monkeypatch.setattr(DashboardApiClient, "admin_live_schedules", lambda api: [
        {"username": "an", "user_id": 2, "collection_id": "an-live", "place_name": "Hà Nội", "enabled": True},
        {"username": "han", "user_id": 3, "collection_id": "han-live", "place_name": "an", "enabled": True},
    ])
    _open_admin_view(admin_app, admin_session, "accounts")
    _select_user(admin_app, 1)
    admin_app.button(key=button).click().run()
    assert not admin_app.exception
    assert admin_app.query_params["view"] == destination
    assert admin_app.session_state[filter_key] == "an"
    if destination == "collections":
        assert admin_app.dataframe[0].value["Tài khoản"].tolist() == ["an", "an"]
    else:
        codes = admin_app.dataframe[0].value["Mã bộ"].tolist()
        assert "an-live" in codes and "han-live" not in codes


def _click_dialog_button(app, opener, submit):
    # AppTest reruns the full script for dialog widgets. Reopen the dialog on
    # that same run so its body receives the widget event.
    app.button(key=opener).click()
    app.button(key=submit).click().run()
    assert not app.exception


def test_admin_users_creates_account_with_validation(admin_app, admin_session, user_directory, monkeypatch):
    create = Mock()
    monkeypatch.setattr(DashboardApiClient, "admin_create_user", create)
    _open_admin_view(admin_app, admin_session, "accounts")
    opener = "admin_create_user"
    assert admin_app.button(key=opener).proto.type == "primary"
    admin_app.button(key=opener).click().run()
    assert admin_app.text_input(key="admin_new_password").proto.type == admin_app.text_input(key="admin_new_password").proto.PASSWORD

    admin_app.text_input(key="admin_new_username").set_value("new_admin")
    admin_app.text_input(key="admin_new_password").set_value("short")
    admin_app.selectbox(key="admin_new_role").set_value("Quản trị viên")
    _click_dialog_button(admin_app, opener, "admin_submit_create")
    assert admin_app.error[0].value == "Mật khẩu cần ít nhất 8 ký tự."
    create.assert_not_called()

    admin_app.text_input(key="admin_new_password").set_value("valid password")
    _click_dialog_button(admin_app, opener, "admin_submit_create")
    create.assert_called_once_with("new_admin", "valid password", is_admin=True)
    assert admin_app.success[0].value == "Đã tạo tài khoản new_admin."


@pytest.mark.parametrize(("opener", "method", "kwargs"), [
    ("admin_user_role_2", "update_user", {"is_admin": True}),
    ("admin_user_status_2", "update_user", {"is_active": False}),
    ("admin_user_revoke_2", "admin_revoke_sessions", {}),
])
def test_admin_users_confirms_access_and_session_actions(
    admin_app, admin_session, user_directory, monkeypatch, opener, method, kwargs,
):
    operation = Mock()
    monkeypatch.setattr(DashboardApiClient, method, operation)
    _open_admin_view(admin_app, admin_session, "accounts")
    _select_user(admin_app, 1)
    admin_app.button(key=opener).click().run()
    operation.assert_not_called()
    _click_dialog_button(admin_app, opener, "admin_confirm_user_action")
    operation.assert_called_once_with(2, **kwargs)


def test_admin_users_resets_password_with_optional_revocation(admin_app, admin_session, user_directory, monkeypatch):
    reset_password = Mock()
    monkeypatch.setattr(DashboardApiClient, "admin_reset_password", reset_password)
    _open_admin_view(admin_app, admin_session, "accounts")
    _select_user(admin_app, 1)
    opener = "admin_user_password_2"
    admin_app.button(key=opener).click().run()
    assert admin_app.checkbox(key="admin_reset_revoke_sessions").value
    admin_app.checkbox(key="admin_reset_revoke_sessions").set_value(False)
    admin_app.text_input(key="admin_reset_password").set_value("new password")
    _click_dialog_button(admin_app, opener, "admin_submit_password")
    reset_password.assert_called_once_with(2, "new password", revoke_sessions=False)


def test_admin_users_delete_requires_exact_username_and_shows_impact(
    admin_app, admin_session, user_directory, monkeypatch,
):
    delete = Mock()
    monkeypatch.setattr(DashboardApiClient, "delete_user", delete)
    _open_admin_view(admin_app, admin_session, "accounts")
    _select_user(admin_app, 1)
    opener = "admin_user_delete_2"
    submit = "admin_submit_delete"
    admin_app.button(key=opener).click().run()
    assert "3 bộ dữ liệu, 12.4 KB dung lượng lưu trữ và 4 lịch Live" in admin_app.warning[0].value

    admin_app.text_input(key="admin_delete_username").set_value("An")
    admin_app.button(key=opener).click().run()
    assert admin_app.button(key=submit).proto.disabled
    delete.assert_not_called()

    admin_app.text_input(key="admin_delete_username").set_value("an")
    admin_app.button(key=opener).click().run()
    assert not admin_app.button(key=submit).proto.disabled
    _click_dialog_button(admin_app, opener, submit)
    delete.assert_called_once_with(2)


def test_admin_facade_exports_shell_entrypoint_and_components():
    for name in ("main", "_BRIDGE", "_SIDEBAR", "_ADMIN_NAV", "st"):
        assert getattr(admin_site, name) is getattr(shell, name)


def test_admin_shared_formatters_handle_legacy_and_missing_values():
    assert shell.format_vn_time(0) == shell.format_vn_time(0.0) == "07:00 · 01/01/1970"
    assert shell.format_vn_time("2026-11-27T19:05:06.123456") == "02:05 · 28/11/2026"
    assert shell.format_vn_time("invalid") == shell.format_vn_time(None) == "—"
    assert [shell.format_bytes(size) for size in (None, 12698, 14889779, 1181116006)] == [
        "0 B", "12.4 KB", "14.2 MB", "1.1 GB",
    ]
    assert [shell.format_idle_duration(seconds) for seconds in (None, 0, 300, 28800, 777600)] == [
        "—", "Vừa xong", "5 phút trước", "8 giờ trước", "9 ngày trước",
    ]
