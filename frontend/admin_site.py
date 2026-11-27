"""Dedicated admin dashboard and account management interface."""
import os
import time
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


_TOKEN_KEY = "admin_access_token"
_EXPIRY_KEY = "admin_expires_at"
_ADMIN_NAV = {
    "overview": ("Tổng quan", "dashboard"),
    "accounts": ("Quản lý tài khoản", "manage_accounts"),
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
        access_token=st.session_state.get("admin_access_token"),
    )


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
    if not valid_local and not st.session_state.get("admin_restore_ready") and not st.query_params.get("handoff"):
        restored = getattr(bridge, "restored", None)
        if not isinstance(restored, dict):
            st.caption("Đang khôi phục phiên quản trị…")
            return
        saved = restored.get("session")
        if isinstance(saved, dict):
            saved_token, saved_expiry = saved.get("access_token"), saved.get("expires_at")
            if isinstance(saved_token, str) and type(saved_expiry) is int and saved_expiry > int(time.time()):
                st.session_state[_TOKEN_KEY] = saved_token
                st.session_state[_EXPIRY_KEY] = saved_expiry
        st.session_state["admin_restore_ready"] = True
        st.rerun()
    ticket = st.query_params.get("handoff")
    redeemed_ticket_key = sha256(ticket.encode()).hexdigest() if ticket else None
    already_redeemed = bool(
        ticket
        and st.session_state.get("admin_handoff_ticket_key") == redeemed_ticket_key
        and st.session_state.get(_TOKEN_KEY)
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
        st.session_state["admin_access_token"] = session["access_token"]
        st.session_state[_EXPIRY_KEY] = session["expires_at"]
        st.session_state["admin_username"] = session["username"]
        st.session_state["admin_handoff_ticket_key"] = redeemed_ticket_key
        st.session_state["admin_restore_ready"] = True
        st.query_params.clear()
        st.rerun()
    elif already_redeemed:
        # Streamlit can rerun before the browser applies query-param removal.
        # The exchange is one-time, so continue with the session already issued.
        st.query_params.clear()
    if not st.session_state.get("admin_access_token"):
        st.info("Đăng nhập tài khoản admin tại trang chính để mở khu vực quản trị.")
        st.link_button("Đến trang chính", os.getenv("MAIN_APP_URL", "http://localhost:8501"), type="primary")
        return
    api = _client()
    try:
        user = api.current_user()
    except DashboardApiError as exc:
        st.session_state.pop("admin_access_token", None)
        st.session_state.pop(_EXPIRY_KEY, None)
        st.session_state.pop("admin_username", None)
        st.session_state["admin_restore_ready"] = True
        st.error("Phiên quản trị không còn hợp lệ. Vui lòng đăng nhập lại." if exc.status_code in {401, 403} else str(exc))
        st.rerun()
    if not user.get("is_admin"):
        st.session_state.clear()
        st.session_state["admin_restore_ready"] = True
        st.error("Tài khoản này không được cấp quyền quản trị.")
        return
    _render_admin_sidebar(api, user["username"])
    accounts = st.session_state.get("admin_section") == "accounts"
    with st.container(key="admin_workspace"):
        if accounts:
            with st.container(key="admin_accounts_view"):
                _render_accounts(api, user)
        else:
            st.header("Tổng quan hệ thống", anchor=False)
            _render_overview(api)


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
        .st-key-admin_workspace {
            --account-columns:minmax(0, 1.5fr) 3rem minmax(0, 1fr) minmax(0, 1.15fr) minmax(0, 1.55fr) 5rem;
        }
        :is(.st-key-admin_accounts_table, .st-key-admin_account_filters) [data-testid="stHorizontalBlock"] {
            display:grid; grid-template-columns:var(--account-columns);
            align-items:center; gap:.65rem;
        }
        :is(.st-key-admin_accounts_table, .st-key-admin_account_filters) [data-testid="stColumn"] {
            width:100%; min-width:0;
        }
        .st-key-admin_accounts_table {padding:.5rem .85rem; container-type:inline-size}
        .st-key-admin_account_filters {padding-inline:calc(.85rem + 1px); container-type:inline-size}
        .st-key-admin_account_filters [data-testid="stColumn"]:first-child {grid-column:span 2}
        .st-key-admin_account_filters [data-testid="stColumn"]:nth-child(2) {display:none}
        .st-key-admin_account_count {padding-inline:calc(.85rem + 1px)}
        .st-key-admin_accounts_header {
            background:color-mix(in srgb, var(--primary-color, #0f766e) 12%, transparent);
            border-radius:.4rem; padding-block:.4rem;
        }
        .era5-account-heading {font-size:.86rem; font-weight:700}
        .st-key-admin_accounts_table [data-testid="stColumn"] > [data-testid="stVerticalBlock"] {
            align-items:center; text-align:center;
        }
        .era5-account-id, .era5-account-empty {font-size:.82rem; opacity:.72}
        .era5-account-name {font-weight:650; overflow-wrap:anywhere}
        .era5-account-field-label {display:none}
        .era5-account-role-admin, .era5-account-status {
            display:inline-block; border-radius:.35rem; padding:.15rem .42rem;
            font-size:.87rem; font-weight:600;
        }
        .era5-account-role-admin {background:#dfeafb; color:#214877}
        .era5-account-status {background:#e2f5eb; color:#18573a}
        .era5-account-status.disabled {background:#fce3e0; color:#852923}
        .era5-account-status::before {
            content:""; display:inline-block; width:.45rem; height:.45rem; border-radius:50%;
            margin-right:.4rem; background:#2a9a67; vertical-align:middle;
        }
        .era5-account-status.disabled::before {background:#bc5c3c}
        .era5-login-recent, .era5-login-expired {
            display:inline-block; padding:.12rem .4rem; border-radius:.25rem; font-size:.86rem;
            font-variant-numeric:tabular-nums; font-weight:600;
        }
        .era5-login-recent {background:#dcf5e6; color:#18573a}
        .era5-login-expired {background:#fce3e0; color:#852923}
        [class*="st-key-admin_account_row_"] {padding:.5rem 0; border-top:1px solid var(--border-color, #d5e2df)}
        [class*="st-key-admin_account_menu_"] button {padding-inline:.65rem}
        [data-testid="stPopoverBody"]:has([class*="st-key-admin_account_actions_"]) {
            width:min(15rem, calc(100vw - 1.5rem)); min-width:0; padding:.38rem;
        }
        [class*="st-key-admin_account_actions_"] button {
            justify-content:flex-start; min-height:2.3rem; padding:.36rem .55rem;
            border-radius:.45rem; text-align:left;
        }
        [class*="st-key-admin_account_actions_"] button:hover {
            background:color-mix(in srgb, currentColor 8%, transparent);
        }
        [class*="st-key-admin_account_actions_"] hr {
            margin:.2rem .4rem; border:0; border-top:1px solid var(--border-color, #d5e2df);
        }
        @container(max-width:620px) {
            .era5-account-field-label {display:block; font-size:.75rem; opacity:.75; margin-bottom:.15rem}
            .st-key-admin_accounts_table [data-testid="stHorizontalBlock"] {
                grid-template-columns:minmax(0,1fr) minmax(0,1fr) 2.8rem; gap:.4rem .55rem;
            }
            .st-key-admin_accounts_header {display:none}
            .st-key-admin_account_filters [data-testid="stHorizontalBlock"] {
                grid-template-columns:repeat(2, minmax(0,1fr));
            }
            .st-key-admin_account_filters [data-testid="stColumn"]:is(:first-child,:nth-child(5)) {
                grid-column:1 / -1;
            }
            .st-key-admin_account_filters [data-testid="stColumn"]:last-child {display:none}
            [class*="st-key-admin_account_row_"] {padding:.75rem .1rem}
            [class*="st-key-admin_account_row_"] [data-testid="stColumn"] > [data-testid="stVerticalBlock"] {
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
        search, _, role_field, state_field, login_field, _ = st.columns(6)
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
                    tone = "recent" if recency == "Trong 7 ngày" else "expired"
                    login.html(login_label + f'<time class="era5-login-{tone}" datetime="{timestamp.isoformat()}" title="{recency}">'
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
