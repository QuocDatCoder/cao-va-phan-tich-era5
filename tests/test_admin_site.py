import time
from pathlib import Path
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

from frontend import admin_site
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
    monkeypatch.setattr(DashboardApiClient, "admin_overview", lambda api: {
        "total_users": 1, "active_users": 1, "disabled_users": 0, "admin_users": 1, "new_today": 0,
    })
    return payload


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
    monkeypatch.setattr(admin_site, "_BRIDGE", bridge)
    admin_app.run()
    _submit_login(admin_app, "admin_user")

    login.assert_called_once_with("admin_user", "fixture password")
    assert not admin_app.exception
    assert admin_app.header[0].value == "Tổng quan hệ thống"
    assert not admin_app.text_input
    assert admin_app.session_state["admin_access_token"] == admin_session["access_token"]
    assert admin_app.session_state["admin_expires_at"] == admin_session["expires_at"]
    assert admin_app.session_state["admin_username"] == "admin_user"
    data = bridge.call_args.kwargs["data"]
    assert data["mode"] == "write"
    assert data["session"] == {
        "access_token": admin_session["access_token"], "expires_at": admin_session["expires_at"],
    }


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
