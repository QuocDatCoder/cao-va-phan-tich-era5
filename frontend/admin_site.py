"""Dedicated admin dashboard and account management interface."""
import os
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from html import escape

import pandas as pd
import streamlit as st

from frontend.api_client import DashboardApiClient, DashboardApiError, DEFAULT_TIMEOUT_SECONDS
from frontend.workspace_sidebar import _SIDEBAR
from frontend.appearance import apply_appearance
from frontend.notifications import notify_admin_error, notify_success
from frontend.dashboard import _translate_status
from backend.app.domain.live_refresh import LIVE_INTERVAL_HOURS
from backend.app.domain.time_utils import to_vietnam_time


_TOKEN_KEY = "admin_access_token"
_EXPIRY_KEY = "admin_expires_at"
_ADMIN_ROLE_ERROR = "Tài khoản này không có quyền quản trị."
_ADMIN_NAV = {
    "overview": ("Tổng quan", "dashboard"),
    "accounts": ("Quản lý tài khoản", "manage_accounts"),
    "live-schedules": ("Tự động cập nhật", "sync"),
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


def _render_admin_login(api: DashboardApiClient) -> None:
    with st.container(horizontal_alignment="center"):
        with st.container(width=440):
            with st.form("admin_login_form", clear_on_submit=True):
                is_dark = getattr(getattr(st.context, "theme", None), "type", None) == "dark"
                ink = "#fff" if is_dark else "#17221c"
                hub_bg = "#ff9d24" if is_dark else "#237845"
                hub_fg = "#101010" if is_dark else "#fff"
                st.html(
                    f'<strong style="display:inline-flex;align-items:center;gap:.11em;'
                    f'color:{ink};font-size:1.55rem;font-weight:850;letter-spacing:-.065em;'
                    f'line-height:1;margin-bottom:.35rem;">Weather '
                    f'<span style="display:inline-block;padding:.14em .23em .17em;'
                    f'border-radius:.27em;background:{hub_bg};color:{hub_fg};font-weight:650;">Hub</span></strong>'
                )
                st.header("Đăng nhập Quản trị", anchor=False)
                st.caption("Weather Hub Admin")
                username = st.text_input("Tên đăng nhập", key="admin_login_username", autocomplete="username")
                password = st.text_input(
                    "Mật khẩu", type="password", key="admin_login_password", autocomplete="current-password"
                )
                submitted = st.form_submit_button(
                    "Đăng nhập quản trị", key="admin_login_submit", type="primary", width="stretch"
                )
                error = st.empty()

    if message := st.session_state.pop("admin_login_error", None):
        error.error(message)
    if not submitted:
        return
    try:
        payload = api.login(username, password)
    except DashboardApiError as exc:
        error.error(str(exc))
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
        error.error(_ADMIN_ROLE_ERROR)
        return
    _store_admin_session(payload)
    st.rerun()


def _navigate_admin() -> None:
    event = st.session_state.get("era5_admin_sidebar")
    section = getattr(event, "navigate", None)
    if section in _ADMIN_NAV:
        if section != st.session_state.get("admin_section"):
            st.session_state.pop("admin_account_notice", None)
        st.session_state["admin_section"] = section
        st.query_params["view"] = section


def _logout_admin(api: DashboardApiClient) -> None:
    try:
        api.logout()
    except DashboardApiError:
        pass
    st.session_state.clear()
    st.session_state["admin_restore_ready"] = True
    st.query_params.clear()


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
            "dismissAdminNoticeOnNavigate": True,
        },
        on_navigate_change=_navigate_admin,
        on_logout_change=lambda: _logout_admin(api),
    )


def main() -> None:
    st.set_page_config(page_title="Weather Hub Admin", page_icon="🛡️", layout="wide")
    apply_appearance()
    st.html("""
        <style>
        body > .admin-notice {
            position:fixed; inset:1rem 1rem auto auto; margin:0; z-index:100000;
            display:flex; align-items:flex-start; gap:.75rem; padding:.85rem 1rem;
            width:max-content; max-width:min(28rem, calc(100vw - 2rem));
            background:#f7fbf8; color:#18332f; border:1px solid #d5e2df;
            border-inline-start:.35rem solid var(--admin-notice-accent);
            border-radius:.7rem; box-shadow:0 .65rem 1.65rem #0003;
            box-sizing:border-box;
        }
        body:not(:has(.st-key-admin_accounts_view)) > .admin-notice {visibility:hidden}
        .admin-notice.era-notice-success {--admin-notice-accent:#16804a}
        .admin-notice.era-notice-error {--admin-notice-accent:#bf3333; background:#fff7f6}
        body:has(.era5-shell[data-theme="dark"]) > .admin-notice {
            background:#242a27; color:#f0f4f2;
            border-color:#414a45; border-inline-start-color:var(--admin-notice-accent);
        }
        body:has(.era5-shell[data-theme="dark"]) > .admin-notice.era-notice-success {--admin-notice-accent:#68d8a0}
        body:has(.era5-shell[data-theme="dark"]) > .admin-notice.era-notice-error {--admin-notice-accent:#ff9393}
        .admin-notice > :first-child {color:var(--admin-notice-accent); font-weight:700}
        .admin-notice .era-notice-text {overflow-wrap:anywhere}
        .admin-notice button {background:none; border:0; color:inherit; cursor:pointer; font-size:1.1rem}
        @media (max-width:640px) {
            body > .admin-notice {top:3.75rem; right:.5rem; max-width:calc(100vw - 1rem)}
        }
        </style>
    """)
    st.html("""<style>
        .st-key-era5_admin_sidebar, .st-key-era5_admin_auth_bridge,
        [data-testid="stElementContainer"]:has(> .st-key-era5_admin_sidebar) {
            position: absolute;
            height: 0;
            min-height: 0;
            inset-block-start: 0;
        }
        .block-container [data-testid="stVerticalBlock"]:has(> div > .st-key-admin_workspace) {
            gap: 0 !important;
        }
        :is(.st-key-admin_workspace, [data-testid="stPopoverBody"]:has([class*="st-key-admin_account_actions_"])) {
            --admin-accent:#0f766e;
            --admin-surface:#fff;
            --admin-border:#d5e2df;
            --admin-muted:#50645f;
            --admin-header:#edf5f3;
            --admin-hover:#f2f8f6;
            --admin-active-ink:#176044;
            --admin-active-bg:#e3f5ea;
            --admin-locked-ink:#922f34;
            --admin-locked-bg:#fcebec;
        }
        body:has(.era5-shell[data-theme="dark"])
        :is(.st-key-admin_workspace, [data-testid="stPopoverBody"]:has([class*="st-key-admin_account_actions_"])) {
            --admin-accent:#60a5fa;
            --admin-surface:#202124;
            --admin-border:#3c414a;
            --admin-muted:#b6bfce;
            --admin-header:#232d3d;
            --admin-hover:#242b38;
            --admin-active-ink:#91e2b4;
            --admin-active-bg:#19382d;
            --admin-locked-ink:#ffb4b8;
            --admin-locked-bg:#43282e;
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
        .st-key-admin_workspace [data-testid="stMetricLabel"] {color:var(--admin-muted)}
        .st-key-admin_workspace [data-testid="stMetricValue"] {
            font-weight:700;
            font-variant-numeric:tabular-nums;
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
    redeemed_ticket_key = sha256(ticket.encode()).hexdigest() if ticket else None
    already_redeemed = bool(
        ticket
        and st.session_state.get("admin_handoff_ticket_key") == redeemed_ticket_key
        and valid_local
    )
    if ticket and not already_redeemed:
        try:
            session = _client().redeem_admin_handoff(ticket)
        except DashboardApiError as exc:
            st.query_params.clear()
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
    if not valid_local:
        _render_admin_login(replace(_client(), access_token=None))
        return
    api = _client()
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
    _render_admin_sidebar(api, user["username"])
    section = st.session_state.get("admin_section")
    with st.container(key="admin_workspace"):
        if section == "accounts":
            with st.container(key="admin_accounts_view"):
                _render_accounts(api, user)
        elif section == "live-schedules":
            _render_live_schedules(api)
        else:
            st.header("Tổng quan hệ thống", anchor=False)
            _render_overview(api)


def _live_status(row):
    if not row["enabled"]:
        return "Đã tắt", "gray"
    return ("Đang tự giãn", "orange") if row["backed_off"] else ("Đang bật", "green")


def _render_live_schedules(api: DashboardApiClient) -> None:
    st.header("Tự động cập nhật", anchor=False)
    try:
        schedules = api.admin_live_schedules()
    except DashboardApiError as exc:
        st.error(str(exc))
        return

    statuses = ("Đang bật", "Đang tự giãn", "Đã tắt")
    cards = st.columns(3)
    for card, status, title in zip(cards, statuses, ("Đang bật", "Đang tự giãn chu kỳ", "Đã tắt")):
        card.metric(title, sum(_live_status(row)[0] == status for row in schedules), border=True)

    search, status_filter, interval_filter = st.columns((2, 1, 1))
    term = search.text_input("Tìm kiếm", placeholder="Tài khoản hoặc địa điểm", key="admin_live_search").strip().casefold()
    status = status_filter.selectbox("Trạng thái", ("Tất cả", *statuses), key="admin_live_status")
    intervals = ("Tất cả", *(f"{hours} giờ" for hours in LIVE_INTERVAL_HOURS))
    interval = interval_filter.selectbox("Chu kỳ thực tế", intervals, key="admin_live_interval")
    rows = [
        row for row in schedules
        if (term in row["username"].casefold() or term in row["place_name"].casefold())
        and (status == "Tất cả" or _live_status(row)[0] == status)
        and (interval == "Tất cả" or f"{row['effective_interval_hours']} giờ" == interval)
    ]

    widths = (1.2, 1.7, 1, 1, 1.2, 1.4, .75)
    with st.container(border=True, key="admin_live_table", gap="small"):
        columns = ("Tài khoản", "Bộ dữ liệu", "Chu kỳ gốc", "Chu kỳ thực tế",
                   "Trạng thái", "Cập nhật gần nhất", "Thao tác")
        for cell, title in zip(st.columns(widths, gap="small"), columns):
            cell.markdown(f"**{title}**")
        if not rows:
            st.caption("Không có lịch phù hợp.")
        for row in rows:
            account, place, original, effective, state, refreshed, action = st.columns(
                widths, gap="small", vertical_alignment="center",
            )
            account.text(row["username"])
            place.markdown(row["place_name"].replace("$", r"\$"))
            place.caption(row["collection_id"][:8])
            original.text(f"{row['requested_interval_hours']} giờ")
            hours = row["effective_interval_hours"]
            effective.text(f"{hours} giờ" if hours is not None else "—")
            label, color = _live_status(row)
            state.badge(label, color=color)
            last = row["last_refreshed_at"]
            refreshed.text(to_vietnam_time(datetime.fromisoformat(last)).strftime("%d/%m/%Y %H:%M") if last else "—")
            user_id, collection_id = row["user_id"], row["collection_id"]
            if row["enabled"] and action.button("Tắt", key=f"admin_live_disable_{user_id}_{collection_id}"):
                try:
                    api.admin_disable_live_schedule(user_id, collection_id)
                except DashboardApiError as exc:
                    st.error(str(exc))
                else:
                    st.rerun()


def _render_overview(api: DashboardApiClient) -> None:
    try:
        overview = api.admin_overview()
        users = api.list_users()
        history = api.admin_collection_history()
    except DashboardApiError as exc:
        st.error(str(exc))
        return
    cards = st.grid("auto", min_column_width=145, gap="small")
    for label, key in zip(("Tổng tài khoản", "Đang hoạt động", "Đã vô hiệu hóa", "Quản trị viên", "Đăng ký hôm nay"),
                          ("total_users", "active_users", "disabled_users", "admin_users", "new_today")):
        cards.metric(label, f"{overview[key]:,}", border=True, height="stretch")
    st.subheader("Lịch sử thu thập của người dùng", anchor=False)
    st.caption("Lịch sử tác vụ được tổng hợp từ kho riêng của từng tài khoản.")
    choices = {"Tất cả người dùng": None}
    choices.update({f"{account['username']} (ID {account['id']})": account["id"] for account in users})
    selected = st.selectbox("Lọc theo người dùng", list(choices), key="admin_history_user")
    selected_id = choices[selected]
    if selected_id is not None:
        history = [job for job in history if job["user_id"] == selected_id]
    if history:
        columns = {
            "username": "Tài khoản", "job_id": "Mã tác vụ",
            "variable": "Biến khí hậu", "start_date": "Từ ngày", "end_date": "Đến ngày",
            "frequency": "Tần suất", "status": "Trạng thái", "progress": "Tiến độ %",
            "created_at": "Bắt đầu", "completed_at": "Kết thúc",
        }
        frame = pd.DataFrame(history)[list(columns)]
        frame["status"] = frame["status"].map(_translate_status)
        st.dataframe(frame.rename(columns=columns), hide_index=True, width="stretch")
    else:
        st.info("Chưa có lịch sử thu thập phù hợp.")


@st.dialog("Xác nhận thao tác")
def _confirm_account_action(api, account, action, label):
    st.write(f"{label}: **{account['username']}**?")
    if action == "delete":
        st.warning("Tài khoản sẽ bị xóa vĩnh viễn.")
    with st.container(horizontal=True, gap="small"):
        confirm = st.button("Xóa vĩnh viễn" if action == "delete" else "Xác nhận", type="primary")
        cancel = st.button("Hủy")
    if cancel:
        st.rerun()
    if confirm:
        try:
            if action == "delete":
                api.delete_user(account["id"])
            elif action == "role":
                api.update_user(account["id"], is_admin=not account["is_admin"])
            else:
                api.update_user(account["id"], is_active=not account["is_active"])
        except DashboardApiError as exc:
            notify_admin_error(str(exc))
        else:
            st.session_state["admin_account_notice"] = f"Đã thực hiện: {label.lower()} ({account['username']})."
            st.rerun()


def _render_accounts(api: DashboardApiClient, current_user: dict) -> None:
    st.html("""
        <style>
        .st-key-admin_accounts_view {container-type:inline-size}
        :is(.st-key-admin_accounts_table, .st-key-admin_account_filters) [data-testid="stHorizontalBlock"] {
            display:grid; gap:.65rem;
        }
        .st-key-admin_account_filters [data-testid="stHorizontalBlock"] {
            grid-template-columns:minmax(0, 1.5fr) repeat(2, minmax(0, 1fr)) minmax(0, 1.25fr);
            align-items:end;
        }
        .st-key-admin_accounts_table [data-testid="stHorizontalBlock"] {
            grid-template-columns:minmax(0, 1.5fr) 3rem minmax(0, 1fr) minmax(0, 1.2fr) minmax(0, 1.5fr) 4.5rem;
            align-items:center;
        }
        :is(.st-key-admin_accounts_table, .st-key-admin_account_filters) [data-testid="stColumn"] {
            width:100%; min-width:0;
        }
        .st-key-admin_account_filters [data-testid="stWidgetLabel"] p {
            color:var(--admin-muted); font-weight:600;
        }
        .st-key-admin_accounts_table {
            --account-inset:1rem;
            gap:0; padding:0; border-color:var(--admin-border);
            background:var(--admin-surface); border-radius:.75rem;
        }
        .st-key-admin_accounts_header {
            background:var(--admin-header);
            border-radius:.75rem .75rem 0 0; padding:.65rem var(--account-inset);
            border-bottom:1px solid var(--admin-border);
        }
        .era5-account-heading {color:var(--admin-muted); font-size:.82rem; font-weight:700}
        .st-key-admin_accounts_table [data-testid="stColumn"] > [data-testid="stVerticalBlock"] {
            align-items:center; text-align:center;
        }
        .st-key-admin_accounts_table [data-testid="stColumn"]:first-child > [data-testid="stVerticalBlock"] {
            align-items:flex-start; text-align:left;
        }
        .era5-account-id, .era5-account-empty, .era5-account-login {
            color:var(--admin-muted); font-size:.85rem; font-variant-numeric:tabular-nums;
        }
        .era5-account-name {font-weight:650; overflow-wrap:anywhere}
        .era5-account-field-label {display:none}
        .era5-account-role-admin, .era5-account-status {
            display:inline-flex; align-items:center; gap:.35rem;
            border-radius:.4rem; padding:.18rem .45rem; font-size:.8rem; font-weight:650;
        }
        .era5-account-role-admin {
            background:var(--admin-header); color:var(--admin-accent);
        }
        .era5-account-status {background:var(--admin-active-bg); color:var(--admin-active-ink)}
        .era5-account-status.disabled {background:var(--admin-locked-bg); color:var(--admin-locked-ink)}
        .era5-account-status::before {
            content:""; flex:none; width:.38rem; height:.38rem;
            border-radius:50%; background:currentColor;
        }
        .st-key-admin_accounts_table [class*="st-key-admin_account_row_"] {
            padding:.55rem var(--account-inset);
        }
        .st-key-admin_accounts_table > :not(:last-child) > [class*="st-key-admin_account_row_"] {
            border-bottom:1px solid var(--admin-border);
        }
        .st-key-admin_accounts_table > :last-child > [class*="st-key-admin_account_row_"] {
            border-radius:0 0 .75rem .75rem;
        }
        .st-key-admin_accounts_table [class*="st-key-admin_account_row_"]:is(:hover, :focus-within) {
            background:var(--admin-hover);
        }
        [class*="st-key-admin_account_menu_"] [data-testid="stPopoverButton"],
        .st-key-admin_accounts_table .stMarkdownBadge {
            background:var(--admin-surface); border:1px solid var(--admin-border);
            border-radius:.45rem; min-height:2.1rem; padding:.2rem .45rem;
        }
        .st-key-admin_accounts_table .stMarkdownBadge {
            background:var(--admin-surface) !important; color:var(--admin-muted) !important;
            min-width:2.1rem; justify-content:center;
        }
        [class*="st-key-admin_account_menu_"] [data-testid="stPopoverButton"] {color:var(--admin-accent)}
        [class*="st-key-admin_account_menu_"] [data-testid="stPopoverButton"]:is(:hover, :focus-visible, [aria-expanded="true"]) {
            background:var(--admin-header); border-color:var(--admin-accent); color:var(--admin-accent);
        }
        [class*="st-key-admin_account_menu_"] [data-testid="stPopoverButton"]:focus-visible {
            outline:2px solid var(--admin-accent); outline-offset:2px;
        }
        [data-testid="stPopoverBody"]:has([class*="st-key-admin_account_actions_"]) {
            width:min(15rem, calc(100vw - 1.5rem)); min-width:0; padding:.38rem;
            background:var(--admin-surface); border-color:var(--admin-border);
        }
        [class*="st-key-admin_account_actions_"] button {
            justify-content:flex-start; min-height:2.3rem; padding:.36rem .55rem;
            border-radius:.45rem; text-align:left;
        }
        [class*="st-key-admin_account_actions_"] button:hover {
            background:var(--admin-hover);
        }
        [class*="st-key-admin_account_actions_"] hr {
            margin:.2rem .4rem; border:0; border-top:1px solid var(--admin-border);
        }
        @container (max-width:710px) {
            .era5-account-field-label {
                display:block; color:var(--admin-muted); font-size:.75rem; margin-bottom:.15rem;
            }
            .st-key-admin_accounts_table [data-testid="stHorizontalBlock"] {
                grid-template-columns:minmax(0,1fr) minmax(0,1fr) 2.8rem; gap:.4rem .55rem;
            }
            .st-key-admin_accounts_header {display:none}
            .st-key-admin_account_filters [data-testid="stHorizontalBlock"] {
                grid-template-columns:repeat(2, minmax(0,1fr));
            }
            .st-key-admin_account_filters [data-testid="stColumn"]:is(:first-child,:last-child) {
                grid-column:1 / -1;
            }
            .st-key-admin_accounts_table [class*="st-key-admin_account_row_"] {padding-block:.75rem}
            .st-key-admin_accounts_table [data-testid="stColumn"] > [data-testid="stVerticalBlock"] {
                align-items:flex-start; text-align:left;
            }
            [class*="st-key-admin_account_row_"] [data-testid="stColumn"]:nth-child(1) {grid-area:1 / 1}
            [class*="st-key-admin_account_row_"] [data-testid="stColumn"]:nth-child(2) {grid-area:1 / 2}
            [class*="st-key-admin_account_row_"] [data-testid="stColumn"]:nth-child(3) {grid-area:2 / 1}
            [class*="st-key-admin_account_row_"] [data-testid="stColumn"]:nth-child(4) {grid-area:2 / 2 / 3 / 4}
            [class*="st-key-admin_account_row_"] [data-testid="stColumn"]:nth-child(5) {grid-area:3 / 1 / 4 / 4}
            [class*="st-key-admin_account_row_"] [data-testid="stColumn"]:nth-child(6) {grid-area:1 / 3}
        }
        </style>
    """)
    st.title("Quản lý tài khoản", anchor=False)
    if notice := st.session_state.pop("admin_account_notice", None):
        notify_success(notice, icon="✓", admin=True)
    try:
        users = api.list_users()
    except DashboardApiError as exc:
        st.error(str(exc))
        return

    with st.container(key="admin_account_filters"):
        search, role_field, state_field, login_field = st.columns(4)
        query = search.text_input("Tìm tài khoản", placeholder="Tên đăng nhập hoặc ID",
                                  key="account_search").strip().casefold()
        role_filter = role_field.selectbox("Vai trò", ["Tất cả", "Người dùng", "Quản trị viên"], key="admin_account_role")
        status_filter = state_field.selectbox("Trạng thái", ["Tất cả", "Đang hoạt động", "Đã vô hiệu hóa"],
                                              key="admin_account_status")
        login_options = {
            "Tất cả": (None, -1),
            "Mới nhất trước": (None, -1),
            "Cũ nhất trước": (None, 1),
            "Trong 7 ngày": ("Trong 7 ngày", -1),
            "Quá 7 ngày": ("Quá 7 ngày", 1),
            "Chưa ghi nhận": ("Chưa ghi nhận", -1),
        }
        selection = login_field.selectbox("Đăng nhập gần nhất", list(login_options), key="admin_account_login_view")
        login_filter, direction = login_options[selection]


    recent_cutoff = int(time.time()) - int(timedelta(days=7).total_seconds())
    local_timezone = timezone(timedelta(hours=7))
    id_query = query.removeprefix("id ").removeprefix("#").strip()
    filters = (query, role_filter, status_filter, selection)
    if st.session_state.get("admin_account_page_filters") != filters:
        st.session_state["admin_account_page_filters"] = filters
        st.session_state["admin_account_page"] = 1
    filtered = []
    for account in users:
        role = "Quản trị viên" if account["is_admin"] else "Người dùng"
        status = "Đang hoạt động" if account["is_active"] else "Đã vô hiệu hóa"
        last_login = account.get("last_login_at")
        recency = ("Chưa ghi nhận" if last_login is None else
                   "Trong 7 ngày" if last_login >= recent_cutoff else "Quá 7 ngày")
        if (query and query not in account["username"].casefold() and id_query != str(account["id"])
                or role_filter not in ("Tất cả", role)
                or status_filter not in ("Tất cả", status)
                or (login_filter is not None and login_filter != recency)):
            continue
        filtered.append((account, role, status, recency))

    filtered.sort(key=lambda row: (row[0].get("last_login_at") is None,
                                   direction * (row[0].get("last_login_at") or 0)))
    with st.container(gap=None, key="admin_account_count"):
        st.caption(f"{len(filtered)}/{len(users)} tài khoản · Giờ đăng nhập theo UTC+7")
    if not filtered:
        st.info("Không tìm thấy tài khoản phù hợp.")
        return
    page_size = 15
    page_count = (len(filtered) + page_size - 1) // page_size
    page = min(st.session_state.get("admin_account_page", 1), page_count)
    st.session_state["admin_account_page"] = page
    offset = (page - 1) * page_size
    with st.container(border=True, gap=None, key="admin_accounts_table"):
        with st.container(key="admin_accounts_header"):
            labels = ("Tài khoản", "ID", "Vai trò", "Trạng thái", "Đăng nhập gần nhất", "Thao tác")
            for column, label in zip(st.columns(6), labels):
                column.html(f'<span class="era5-account-heading">{label}</span>', width="content")
        for account, role, status, recency in filtered[offset:offset + page_size]:
            account_id = account["id"]
            with st.container(gap=None, key=f"admin_account_row_{account_id}"):
                identity, identifier, access, state, login, actions = st.columns(6, vertical_alignment="center")
                identity.html(f'<span class="era5-account-name">{escape(account["username"])}</span>')
                identifier.html(f'<span class="era5-account-id" aria-label="ID {account_id}">#{account_id}</span>',
                                width="content")
                role_class = ' class="era5-account-role-admin"' if account["is_admin"] else ""
                access.html('<span class="era5-account-field-label">Vai trò</span>'
                            f'<span{role_class}>{role}</span>', width="content")
                tone = "" if account["is_active"] else " disabled"
                state.html('<span class="era5-account-field-label">Trạng thái</span>'
                           f'<span class="era5-account-status{tone}">{status}</span>')
                login_label = '<span class="era5-account-field-label">Đăng nhập gần nhất</span>'
                if recency == "Chưa ghi nhận":
                    login.html(login_label + '<span class="era5-account-empty">Chưa ghi nhận</span>')
                else:
                    timestamp = datetime.fromtimestamp(account["last_login_at"], local_timezone)
                    login.html(login_label + f'<time class="era5-account-login" datetime="{timestamp.isoformat()}" title="{recency}">'
                               f'{timestamp:%H:%M · %d/%m/%Y}</time>')
                fixed = account["username"] == os.getenv("ADMIN_USERNAME", "admin").casefold()
                if fixed or account_id == current_user["id"]:
                    actions.badge("", icon=":material/lock:", color="gray", help="Tài khoản được bảo vệ")
                else:
                    with actions.popover("⋯", help=f'Thao tác với {account["username"]}', key=f"admin_account_menu_{account_id}"):
                        role_action = "Thu hồi quyền quản trị" if account["is_admin"] else "Cấp quyền quản trị"
                        state_action = "Vô hiệu hóa tài khoản" if account["is_active"] else "Kích hoạt lại tài khoản"
                        with st.container(gap=None, key=f"admin_account_actions_{account_id}"):
                            options = (
                                ("role", role_action, "shield_person"),
                                ("status", state_action, "person_off" if account["is_active"] else "person_check"),
                                ("delete", "Xóa tài khoản", "delete"),
                            )
                            for action, label, icon in options:
                                if action == "delete":
                                    st.html("<hr>")
                                button_label = ":red[Xóa tài khoản…]" if action == "delete" else label
                                if st.button(button_label, key=f"admin_{action}_{account_id}", type="tertiary",
                                             icon=f":material/{icon}:", width="stretch"):
                                    _confirm_account_action(api, account, action, label)

    if page_count > 1:
        with st.container(horizontal=True, horizontal_alignment="right", vertical_alignment="center", gap="small"):
            st.caption(f"Trang {page}/{page_count}")
            st.button("Trang trước", icon=":material/chevron_left:", disabled=page == 1,
                      on_click=st.session_state.update, args=({"admin_account_page": page - 1},))
            st.button("Trang sau", icon=":material/chevron_right:", disabled=page == page_count,
                      on_click=st.session_state.update, args=({"admin_account_page": page + 1},))
