"""Dedicated admin dashboard and account management interface."""
import os
import time
from hashlib import sha256

import pandas as pd
import streamlit as st

from frontend.api_client import DashboardApiClient, DashboardApiError, DEFAULT_TIMEOUT_SECONDS
from frontend.workspace_sidebar import _SIDEBAR
from frontend.appearance import apply_appearance
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
        },
        on_navigate_change=_navigate_admin,
        on_logout_change=lambda: _logout_admin(api),
    )


def main() -> None:
    st.set_page_config(page_title="ERA5 · Quản trị", page_icon="🛡️", layout="wide")
    apply_appearance()
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
        with st.container(horizontal=True, vertical_alignment="center", height=44, border=False):
            st.header("Quản lý tài khoản" if accounts else "Tổng quan hệ thống", anchor=False)
        if accounts:
            _render_accounts(api, user)
        else:
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


def _render_accounts(api: DashboardApiClient, current_user: dict) -> None:
    st.caption("Cấp quyền quản trị, vô hiệu hóa hoặc xóa vĩnh viễn tài khoản.")
    try:
        users = api.list_users()
    except DashboardApiError as exc:
        st.error(str(exc))
        return
    query = st.text_input("Tìm tài khoản", placeholder="Nhập tên đăng nhập", key="account_search")
    filtered = [u for u in users if query.strip().casefold() in u["username"].casefold()]
    if not filtered:
        st.info("Không tìm thấy tài khoản phù hợp.")
    for account in filtered:
        with st.container(border=True):
            identity, role, state, actions = st.columns([3, 2, 2, 3], vertical_alignment="center")
            identity.markdown(f"**{account['username']}**  ·  ID {account['id']}")
            role.write("Quản trị viên" if account["is_admin"] else "Người dùng")
            active = account["is_active"]
            state.badge("Đang hoạt động" if active else "Đã vô hiệu hóa",
                        color="green" if active else "orange")
            fixed = account["username"] == os.getenv("ADMIN_USERNAME", "admin").casefold()
            if fixed or account["id"] == current_user["id"]:
                actions.caption("Tài khoản quản trị được bảo vệ")
            else:
                with actions.popover("Thao tác", width="stretch", icon=":material/settings:", key=f"site_actions_{account['id']}"):
                    new_admin = st.checkbox("Quyền quản trị", value=bool(account["is_admin"]), key=f"site_admin_{account['id']}")
                    new_active = st.checkbox("Đang hoạt động", value=bool(account["is_active"]), key=f"site_active_{account['id']}")
                    if st.button("Lưu thay đổi", key=f"site_save_{account['id']}", type="primary"):
                        try:
                            api.update_user(account["id"], is_admin=new_admin, is_active=new_active)
                        except DashboardApiError as exc:
                            st.error(str(exc))
                        else:
                            st.success("Đã cập nhật tài khoản.")
                            st.rerun()
                    st.divider()
                    confirm = st.checkbox("Tôi xác nhận xóa vĩnh viễn", key=f"site_confirm_{account['id']}")
                    if st.button("Xóa tài khoản", key=f"site_delete_{account['id']}", disabled=not confirm):
                        try:
                            api.delete_user(account["id"])
                        except DashboardApiError as exc:
                            st.error(str(exc))
                        else:
                            st.success(f"Đã xóa tài khoản {account['username']}.")
                            st.rerun()
