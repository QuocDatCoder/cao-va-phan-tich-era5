"""Authentication, navigation, and shared presentation for Weather Hub Admin."""
import os
import time
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from typing import Callable

import streamlit as st

from backend.app.domain.time_utils import to_vietnam_time
from frontend.api_client import DEFAULT_TIMEOUT_SECONDS, DashboardApiClient, DashboardApiError
from frontend.appearance import apply_appearance
from frontend.workspace_sidebar import _SIDEBAR


_TOKEN_KEY = "admin_access_token"
_EXPIRY_KEY = "admin_expires_at"
_ADMIN_ROLE_ERROR = "Tài khoản này không có quyền quản trị."
_ADMIN_NAV = {
    "overview": ("Tổng quan", "dashboard"),
    "accounts": ("Quản lý người dùng", "manage_accounts"),
    "collections": ("Quản lý thu thập", "cloud_download"),
    "live-schedules": ("Quản lý cập nhật", "sync"),
}
_BRIDGE = st.components.v2.component(
    "era5_admin_session_bridge",
    html="<span aria-hidden='true'></span>",
    js="""
export default function({data, parentElement, setStateValue}) {
  if (data.mode === 'read' && !parentElement.adminSessionRead) {
    parentElement.adminSessionRead = true;
    let session = null;
    try { session = JSON.parse(sessionStorage.getItem(data.key)); } catch (_) {}
    setStateValue('restored', {session});
  } else if (data.mode === 'write' || data.mode === 'clear') {
    try {
      if (data.mode === 'write') sessionStorage.setItem(data.key, JSON.stringify(data.session));
      else sessionStorage.removeItem(data.key);
    } catch (_) {}
  }
}
""",
)


def _client() -> DashboardApiClient:
    return DashboardApiClient(
        base_url=os.getenv("API_BASE_URL", "http://127.0.0.1:8000"),
        timeout_seconds=float(os.getenv("API_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))),
        access_token=st.session_state.get(_TOKEN_KEY),
    )


def _store_admin_session(payload: dict) -> None:
    st.session_state.update({
        _TOKEN_KEY: payload["access_token"],
        _EXPIRY_KEY: payload["expires_at"],
        "admin_username": payload["username"],
        "admin_restore_ready": True,
    })


def _render_admin_login() -> None:
    with st.container(horizontal_alignment="center"):
        with st.container(width=440):
            with st.form("admin_login_form", clear_on_submit=True):
                theme = "dark" if st.context.theme.type == "dark" else "light"
                st.html(f'<strong class="admin-wordmark" data-theme="{theme}">Weather <span>Hub</span></strong>')
                st.header("Đăng nhập Quản trị", anchor=False)
                st.caption("Weather Hub Admin")
                st.text_input("Tên đăng nhập", key="admin_login_username", autocomplete="username")
                st.text_input(
                    "Mật khẩu", type="password", key="admin_login_password", autocomplete="current-password"
                )
                st.form_submit_button(
                    "Đăng nhập quản trị", key="admin_login_submit", type="primary", width="stretch",
                    on_click=_submit_admin_login,
                )
                if message := st.session_state.pop("admin_login_error", None):
                    st.error(message)


def _submit_admin_login() -> None:
    api = replace(_client(), access_token=None)
    username = st.session_state["admin_login_username"]
    password = st.session_state["admin_login_password"]
    try:
        payload = api.login(username, password)
    except DashboardApiError as exc:
        st.session_state["admin_login_error"] = str(exc)
        return
    is_admin = payload.get("is_admin")
    if is_admin is None and payload.get("access_token"):
        try:
            me = replace(api, access_token=payload["access_token"]).current_user()
            is_admin = bool(me.get("is_admin"))
        except DashboardApiError:
            is_admin = False
    if is_admin is not True:
        try:
            replace(api, access_token=payload["access_token"]).logout()
        except DashboardApiError:
            pass
        st.session_state["admin_login_error"] = _ADMIN_ROLE_ERROR
        return
    _store_admin_session(payload)


def _navigate_admin() -> None:
    event = st.session_state.get("era5_admin_sidebar")
    section = getattr(event, "navigate", None)
    if section in _ADMIN_NAV:
        st.session_state["admin_section"] = section
        st.query_params["view"] = section


def navigate_admin(section: str, **preset_state) -> None:
    """Open an admin section and initialize its filters before rendering widgets."""
    if section not in _ADMIN_NAV:
        raise ValueError(f"Unknown admin section: {section}")
    st.session_state.update(preset_state)
    st.session_state["admin_section"] = section
    st.query_params["view"] = section
    st.rerun()


def vietnam_datetime(value: int | float | str | None) -> datetime | None:
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            instant = datetime.fromtimestamp(value, UTC)
        elif isinstance(value, str):
            instant = datetime.fromisoformat(value)
        else:
            return None
        return to_vietnam_time(instant)
    except (ValueError, OSError, OverflowError):
        return None


def format_vn_time(value: int | float | str | None) -> str:
    instant = vietnam_datetime(value)
    return instant.strftime("%H:%M · %d/%m/%Y") if instant is not None else "—"


def format_bytes(num_bytes: int | None) -> str:
    byte_count = max(0, num_bytes or 0)
    if byte_count < 1024:
        return f"{byte_count} B"
    size = float(byte_count)
    for unit in ("KB", "MB", "GB"):
        size /= 1024
        if size < 1024:
            return f"{size:.1f} {unit}"
    return f"{size / 1024:.1f} TB"


def format_idle_duration(idle_seconds: int | None) -> str:
    if idle_seconds is None:
        return "—"
    for unit, seconds in (("ngày", 86400), ("giờ", 3600), ("phút", 60)):
        if idle_seconds >= seconds:
            return f"{idle_seconds // seconds} {unit} trước"
    return "Vừa xong"


def render_page_header(
    title: str,
    subtitle: str,
    *,
    on_refresh: Callable[[], None] | None = None,
    action_render: Callable[[], None] | None = None,
) -> None:
    with st.container(key="admin_page_header"):
        intro, toolbar = st.columns([3, 2], vertical_alignment="center")
        intro.header(title, anchor=False)
        intro.caption(subtitle)
        with toolbar.container(horizontal=True, horizontal_alignment="right", vertical_alignment="center"):
            st.caption(f"{format_vn_time(time.time())} (UTC+7)")
            st.button("Làm mới", key="admin_page_refresh", icon=":material/refresh:", on_click=on_refresh)
            if action_render is not None:
                action_render()


def _logout_admin(api: DashboardApiClient) -> None:
    try:
        api.logout()
    except DashboardApiError:
        pass
    st.session_state.clear()
    st.session_state["admin_restore_ready"] = True
    st.query_params.clear()


def _current_admin(api: DashboardApiClient) -> dict:
    try:
        user = api.current_user()
    except DashboardApiError as exc:
        st.session_state.pop(_TOKEN_KEY, None)
        st.session_state.pop(_EXPIRY_KEY, None)
        st.session_state.pop("admin_username", None)
        st.session_state["admin_restore_ready"] = True
        st.session_state["admin_login_error"] = (
            "Phiên quản trị không còn hợp lệ. Vui lòng đăng nhập lại."
            if exc.status_code in {401, 403} else str(exc)
        )
        st.rerun()
    if not user.get("is_admin"):
        st.session_state.clear()
        st.session_state["admin_restore_ready"] = True
        st.session_state["admin_login_error"] = _ADMIN_ROLE_ERROR
        st.rerun()
    st.session_state["admin_username"] = user["username"]
    return user


def _render_admin_sidebar(api: DashboardApiClient, username: str) -> None:
    section = st.session_state.get("admin_section")
    if section not in _ADMIN_NAV:
        requested = st.query_params.get("view")
        section = requested if requested in _ADMIN_NAV else "overview"
        st.session_state["admin_section"] = section
    _SIDEBAR(
        key="era5_admin_sidebar",
        height=0,
        data={
            "navigation": [
                {"id": key, "label": label, "icon": icon}
                for key, (label, icon) in _ADMIN_NAV.items()
            ],
            "active": section,
            "username": username,
        },
        on_navigate_change=_navigate_admin,
        on_logout_change=lambda: _logout_admin(api),
    )


def main() -> None:
    from .collections import _render_collections
    from .live_schedules import _render_live_schedules
    from .overview import _render_overview
    from .users import _render_accounts

    apply_appearance()
    st.html("""<style>
        .st-key-era5_admin_sidebar, .st-key-era5_admin_auth_bridge,
        [data-testid="stElementContainer"]:has(> .st-key-era5_admin_sidebar) {
            position: absolute;
            height: 0;
            min-height: 0;
            inset-block-start: 0;
        }
        .block-container [data-testid="stVerticalBlock"]:has(> div > .st-key-admin_workspace),
        .st-key-admin_workspace {
            gap: 0 !important;
        }
        body {
            --admin-accent:#0f766e;
            --admin-primary:#0f766e;
            --admin-primary-hover:#115e59;
            --admin-primary-ink:#fff;
            --admin-surface:#fff;
            --admin-border:#d5e2df;
            --admin-muted:#50645f;
            --admin-header:#edf5f3;
            --admin-hover:#f2f8f6;
            --admin-active-ink:#176044;
            --admin-active-bg:#e3f5ea;
            --admin-warning-ink:#794408;
            --admin-warning-bg:#fff1d7;
            --admin-wordmark-ink:#17221c;
            --admin-hub-bg:#237845;
            --admin-hub-ink:#fff;
        }
        body:has(.era5-shell[data-theme="dark"], .admin-wordmark[data-theme="dark"]) {
            --admin-accent:#60a5fa;
            --admin-primary:#60a5fa;
            --admin-primary-hover:#93c5fd;
            --admin-primary-ink:#0b1220;
            --admin-surface:#202124;
            --admin-border:#3c414a;
            --admin-muted:#b6bfce;
            --admin-header:#232d3d;
            --admin-hover:#242b38;
            --admin-active-ink:#91e2b4;
            --admin-active-bg:#19382d;
            --admin-warning-ink:#ffcf86;
            --admin-warning-bg:#44311c;
            --admin-wordmark-ink:#fff;
            --admin-hub-bg:#ff9d24;
            --admin-hub-ink:#101010;
        }
        .admin-wordmark {
            display:inline-flex; align-items:center; gap:.11em; color:var(--admin-wordmark-ink);
            font-size:1.55rem; font-weight:850; letter-spacing:-.065em; line-height:1; margin-bottom:.35rem;
        }
        .admin-wordmark span {
            padding:.14em .23em .17em; border-radius:.27em;
            background:var(--admin-hub-bg); color:var(--admin-hub-ink); font-weight:650;
        }
        .st-key-admin_login_view [data-testid="stForm"] {
            background:var(--admin-surface); border-color:var(--admin-border);
        }
        :is(.st-key-admin_login_view, .st-key-admin_page_header, [data-testid="stDialog"])
        :is([data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"]):enabled,
        .st-key-admin_handoff_view [data-testid="stBaseLinkButton-primary"] {
            background:var(--admin-primary); border-color:var(--admin-primary); color:var(--admin-primary-ink);
        }
        :is(.st-key-admin_login_view, .st-key-admin_page_header, [data-testid="stDialog"], .st-key-admin_handoff_view)
        :is([data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"],
            [data-testid="stBaseLinkButton-primary"]):hover {
            background:var(--admin-primary-hover); border-color:var(--admin-primary-hover);
            color:var(--admin-primary-ink);
        }
        .st-key-admin_workspace :is(h1, h2) {
            font-size:clamp(1.55rem, 2.5vw, 1.9rem) !important;
            letter-spacing:-.025em;
            line-height:1.2;
        }
        .st-key-admin_workspace [data-testid="stMetric"] {
            background:var(--admin-surface);
            border-color:var(--admin-border);
            border-radius:.75rem;
        }
        .st-key-admin_workspace [data-testid="stMetricLabel"] {
            color:var(--admin-muted); font-weight:600;
        }
        .st-key-admin_workspace [data-testid="stMetricLabel"] p {white-space:normal}
        .st-key-admin_workspace [data-testid="stMetricValue"] {
            color:var(--admin-accent);
            font-weight:700;
            font-variant-numeric:tabular-nums;
        }
        .st-key-admin_page_header {
            padding-block:.25rem .85rem;
            border-bottom:1px solid var(--admin-border);
        }
        .st-key-admin_page_header p {color:var(--admin-muted)}
        .st-key-admin_workspace :is(.st-key-admin_health, [class*="st-key-admin_panel_"]) {
            background:var(--admin-surface); border-color:var(--admin-border); border-radius:.75rem;
        }
        .st-key-admin_health {margin-top:.15rem}
        .st-key-admin_health [data-testid="stCaptionContainer"] p {color:var(--admin-muted)}
        .st-key-admin_panel_audit [data-testid="stText"] span {white-space:pre-wrap; overflow-wrap:anywhere}
        :is(.st-key-admin_accounts_view, .st-key-admin_history) [data-testid="stDataFrame"] {
            outline:1px solid var(--admin-border); outline-offset:-1px; border-radius:.65rem;
        }
        @media (min-width: 761px) {
            body:has(.era5-shell) .block-container:has(.st-key-admin_workspace) {
                padding-top: 3rem !important;
            }
        }
    </style>""")
    bridge_key = "era5.admin.v1." + sha256(os.getenv("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/").encode()).hexdigest()[:20]
    token = st.session_state.get(_TOKEN_KEY)
    expires_at = st.session_state.get(_EXPIRY_KEY)
    valid_local = bool(token and type(expires_at) is int and expires_at > int(time.time()))
    with st.container(key="era5_admin_auth_bridge"):
        bridge = _BRIDGE(key="admin_browser_auth_bridge", data={
            "key": bridge_key,
            "mode": "write" if valid_local else "clear" if st.session_state.get("admin_restore_ready") else "read",
            "session": {"access_token": token, "expires_at": expires_at} if valid_local else None,
        }, height=0, on_restored_change=lambda: None)
    ticket = st.query_params.get("handoff")
    redeemed_ticket_key = sha256(ticket.encode()).hexdigest() if ticket else None
    already_redeemed = bool(
        ticket
        and st.session_state.get("admin_handoff_ticket_key") == redeemed_ticket_key
        and valid_local
    )
    api = _client()
    user = _current_admin(api) if valid_local and (not ticket or already_redeemed) else None
    stage = st.empty()
    if not valid_local and not st.session_state.get("admin_restore_ready") and not ticket:
        restored = getattr(bridge, "restored", None)
        if isinstance(restored, dict):
            st.session_state["admin_restore_ready"] = True
            saved = restored.get("session")
            if isinstance(saved, dict):
                saved_token, saved_expiry = saved.get("access_token"), saved.get("expires_at")
                if isinstance(saved_token, str) and saved_token and type(saved_expiry) is int and saved_expiry > int(time.time()):
                    st.session_state[_TOKEN_KEY] = saved_token
                    st.session_state[_EXPIRY_KEY] = saved_expiry
                    st.rerun()
        elif st.session_state.get("admin_restore_ready") is False or st.context.headers.get("Sec-WebSocket-Key"):
            # A browser acknowledges the read even when it has no saved session.
            # AppTest and the HTTP agent can render the form without executing JavaScript.
            stage.caption("Đang khôi phục phiên quản trị…")
            return
    if ticket and not already_redeemed:
        try:
            session = api.redeem_admin_handoff(ticket)
        except DashboardApiError as exc:
            st.query_params.clear()
            with stage.container(key="admin_handoff_view"):
                if exc.status_code == 401:
                    st.error("Mã chuyển trang đã hết hạn hoặc đã được dùng. Hãy tạo mã mới từ trang chính.")
                elif exc.status_code == 404:
                    st.error("Backend chưa có API chuyển trang admin. Hãy tắt các cửa sổ cũ và khởi động lại toàn bộ bằng run_all.bat.")
                else:
                    st.error(f"Không thể xác nhận mã chuyển trang (HTTP {exc.status_code or 'lỗi kết nối'}): {exc}")
                retry_url = os.getenv("MAIN_APP_URL", "http://localhost:8501").rstrip("/") + "/?retry_admin=1"
                st.link_button("Tạo lại liên kết từ trang chính", retry_url, type="primary")
            return
        _store_admin_session(session)
        st.session_state["admin_handoff_ticket_key"] = redeemed_ticket_key
        st.query_params.clear()
        st.rerun()
    elif already_redeemed:
        # Streamlit can rerun before the browser applies query-param removal.
        # The exchange is one-time, so continue with the session already issued.
        st.query_params.clear()
    if user is None:
        with stage.container(key="admin_login_view"):
            _render_admin_login()
        return
    with stage.container(key="admin_workspace"):
        _render_admin_sidebar(api, user["username"])
        section = st.session_state.get("admin_section")
        if section == "accounts":
            with st.container(key="admin_accounts_view"):
                _render_accounts(api, user)
        elif section == "collections":
            with st.container(key="admin_collections_view"):
                _render_collections(api)
        elif section == "live-schedules":
            with st.container(key="admin_live_view"):
                _render_live_schedules(api)
        else:
            with st.container(key="admin_overview_view"):
                _render_overview(api)

