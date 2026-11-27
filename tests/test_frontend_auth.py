import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from backend.app.core.config import get_settings
from frontend import ai_chat, home, workflow, workspace_sidebar
from frontend.api_client import DashboardApiClient


@pytest.fixture(autouse=True)
def mock_current_user(monkeypatch):
    def current_user(api):
        assert api.access_token == "fixture-token"
        return {"username": "test_user", "is_admin": False}

    monkeypatch.setattr(DashboardApiClient, "current_user", current_user)


_APP = """
import streamlit as st
from frontend.auth import logout_account, render_login_gate, restore_account
from frontend.api_client import DashboardApiClient

st.session_state.setdefault("auth_restore_ready", True)
api = DashboardApiClient(access_token=st.session_state.get("auth_access_token"))
if restore_account(api):
    st.button("Đăng xuất", key="auth_logout", on_click=logout_account, args=(api,))
else:
    render_login_gate(api, on_back=lambda: None)
"""


def _session_payload():
    return {
        "access_token": "fixture-token",
        "username": "test_user",
        "expires_at": int(time.time()) + 3600,
    }


def test_login_form_calls_api_and_stores_session(monkeypatch):
    calls = []

    def login(_, username, password):
        calls.append((username, password))
        return _session_payload()

    monkeypatch.setattr(DashboardApiClient, "login", login)
    app = AppTest.from_string(_APP).run()
    app.text_input(key="auth_login_username").set_value("test_user")
    app.text_input(key="auth_login_password").set_value("pass phrase 123")
    app.button(key="auth_login_submit").click().run()

    assert calls == [("test_user", "pass phrase 123")]
    assert app.session_state["auth_access_token"] == "fixture-token"
    assert app.button(key="auth_logout")


def test_registration_form_calls_api_and_stores_session(monkeypatch):
    calls = []

    def register(_, username, password):
        calls.append((username, password))
        return _session_payload()

    monkeypatch.setattr(DashboardApiClient, "register", register)
    app = AppTest.from_string(_APP).run()
    app.text_input(key="auth_register_username").set_value("new_user")
    app.text_input(key="auth_register_password").set_value("pass phrase 123")
    app.button(key="auth_register_submit").click().run()

    assert calls == [("new_user", "pass phrase 123")]
    assert app.session_state["auth_access_token"] == "fixture-token"
    assert app.button(key="auth_logout")


def test_logout_revokes_api_session_and_clears_local_token(monkeypatch):
    calls = []
    monkeypatch.setattr(
        DashboardApiClient, "logout", lambda _: calls.append("logout") or {"status": "ok"}
    )
    app = AppTest.from_string(_APP).run()
    app.session_state["auth_access_token"] = "fixture-token"
    app.session_state["auth_expires_at"] = int(time.time()) + 3600
    app.session_state["auth_username"] = "test_user"
    app.run()
    app.button(key="auth_logout").click().run()

    assert calls == ["logout"]
    assert "auth_access_token" not in app.session_state


@pytest.fixture
def guest_app(monkeypatch):
    import streamlit as st

    monkeypatch.setenv("AUTH_ENABLED", "true")
    get_settings.cache_clear()

    # AppTest does not run component JavaScript. Drive the same Python callbacks
    # with native buttons and the same event payload furnished by the component.
    def sidebar(*, key, data, on_navigate_change, on_login_change, **kwargs):
        def navigate(section):
            st.session_state[key] = SimpleNamespace(navigate=section)
            on_navigate_change()

        with st.sidebar:
            for item in data["navigation"]:
                st.button(item["label"], key=f"sidebar_{item['id']}", on_click=navigate, args=(item["id"],))
            if data["guest"]:
                st.button("Đăng nhập", key="sidebar_login", on_click=on_login_change)

    def unexpected_api(*args, **kwargs):
        pytest.fail("Guest made an API call")

    monkeypatch.setattr(workspace_sidebar, "_SIDEBAR", sidebar)
    monkeypatch.setattr(DashboardApiClient, "_get", unexpected_api)
    monkeypatch.setattr(DashboardApiClient, "_post", unexpected_api)
    monkeypatch.setattr(home, "cached_read", unexpected_api)
    try:
        yield AppTest.from_file(str(Path("app.py").resolve()), default_timeout=15)
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize(("key", "section"), [
    ("sidebar_Thu thập", "Thu thập"),
    ("sidebar_Dữ liệu đã lưu", "Dữ liệu đã lưu"),
    ("sidebar_Biểu đồ", "Biểu đồ"),
    ("sidebar_login", "Trang chủ"),
    ("home_open_cloud_download", "Thu thập"),
    ("home_open_folder_open", "Dữ liệu đã lưu"),
    ("home_open_monitoring", "Biểu đồ"),
    ("home_recent_library", "Dữ liệu đã lưu"),
    ("home_guest_login", "Thu thập"),
    ("home_footer_Biểu đồ", "Biểu đồ"),
])
def test_guest_navigation_opens_login_and_can_return_home(guest_app, key, section):
    app = guest_app.run()
    app.button(key=key).click().run()

    assert not app.exception
    assert app.session_state["workflow_section"] == section
    assert [tab.label for tab in app.tabs] == ["Đăng nhập", "Đăng ký"]
    assert app.sidebar.button(key="sidebar_login")
    assert not app.get("plotly_chart")
    assert app.button(key="auth_back_home").label == "Quay lại Trang chủ"

    app.button(key="auth_back_home").click().run()
    assert not app.exception
    assert app.session_state["workflow_section"] == "Trang chủ"
    assert app.get("plotly_chart")
    assert not app.tabs


@pytest.mark.parametrize(("key", "section", "renderer"), [
    ("sidebar_Thu thập", "Thu thập", "render_collection"),
    ("sidebar_Dữ liệu đã lưu", "Dữ liệu đã lưu", "render_stored"),
    ("home_open_monitoring", "Biểu đồ", "render_charts"),
    ("sidebar_login", "Trang chủ", None),
])
def test_successful_login_opens_requested_section(guest_app, monkeypatch, key, section, renderer):
    import streamlit as st

    def login(api, username, password):
        assert api.access_token is None
        assert (username, password) == ("test_user", "pass phrase 123")
        return _session_payload()

    def render_authenticated_home(api, *, guest=False):
        if guest:
            render_home(api, guest=True)
        else:
            assert api.access_token == "fixture-token"
            st.text(section)

    render_home = workflow.render_home
    monkeypatch.setattr(DashboardApiClient, "login", login)
    monkeypatch.setattr(ai_chat, "render_ai_chat", lambda api: st.text(f"ERA · {api.access_token}"))
    if renderer:
        monkeypatch.setattr(workflow, renderer, lambda: st.text(section))
    else:
        monkeypatch.setattr(workflow, "render_home", render_authenticated_home)

    app = guest_app.run()
    app.button(key=key).click().run()
    app.text_input(key="auth_login_username").set_value("test_user")
    app.text_input(key="auth_login_password").set_value("pass phrase 123")
    app.button(key="auth_login_submit").click().run()

    assert not app.exception
    assert not app.tabs
    assert app.query_params["view"] == section
    assert app.session_state["workflow_section"] == section
    assert [item.value for item in app.text] == ["ERA · fixture-token", section]
    assert not any(button.key == "sidebar_login" for button in app.sidebar.button)
