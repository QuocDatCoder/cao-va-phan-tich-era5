import time

from streamlit.testing.v1 import AppTest

from frontend.api_client import DashboardApiClient


_APP = """
import streamlit as st
from frontend.auth import render_login_gate
from frontend.api_client import DashboardApiClient

render_login_gate(DashboardApiClient())
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
