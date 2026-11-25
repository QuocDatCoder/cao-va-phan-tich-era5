import time

import streamlit as st

from frontend.api_client import DashboardApiClient, DashboardApiError


_TOKEN_KEY = "auth_access_token"
_EXPIRY_KEY = "auth_expires_at"
_USERNAME_KEY = "auth_username"


def _clear_session() -> None:
    for key in (_TOKEN_KEY, _EXPIRY_KEY, _USERNAME_KEY):
        st.session_state.pop(key, None)


def _store_session(payload: dict) -> None:
    st.session_state[_TOKEN_KEY] = payload["access_token"]
    st.session_state[_EXPIRY_KEY] = payload["expires_at"]
    st.session_state[_USERNAME_KEY] = payload["username"]


def render_login_gate(api: DashboardApiClient) -> bool:
    token = st.session_state.get(_TOKEN_KEY)
    expires_at = st.session_state.get(_EXPIRY_KEY)
    if token and isinstance(expires_at, int) and expires_at > int(time.time()):
        with st.sidebar:
            st.caption(f"Đang đăng nhập: {st.session_state.get(_USERNAME_KEY, '')}")
            if st.button("Đăng xuất", key="auth_logout"):
                try:
                    api.logout()
                except DashboardApiError as exc:
                    st.session_state["auth_logout_error"] = str(exc)
                _clear_session()
                st.rerun()
        return True

    expired = bool(token)
    _clear_session()
    if expired:
        st.warning("Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.")
    logout_error = st.session_state.pop("auth_logout_error", None)
    if logout_error:
        st.error(f"Không thể thu hồi phiên đăng nhập trên máy chủ: {logout_error}")

    st.title("Tài khoản ERA5")
    login_tab, register_tab = st.tabs(["Đăng nhập", "Đăng ký"])

    with login_tab:
        with st.form("auth_login_form"):
            login_username = st.text_input("Tên đăng nhập", key="auth_login_username")
            login_password = st.text_input(
                "Mật khẩu", type="password", key="auth_login_password"
            )
            login_submitted = st.form_submit_button(
                "Đăng nhập", key="auth_login_submit", type="primary"
            )
        if login_submitted:
            try:
                _store_session(api.login(login_username, login_password))
            except DashboardApiError as exc:
                st.error(str(exc))
            else:
                st.rerun()

    with register_tab:
        with st.form("auth_register_form"):
            register_username = st.text_input(
                "Tên đăng nhập",
                key="auth_register_username",
                help="3–32 ký tự: chữ cái, số, dấu chấm, gạch dưới hoặc gạch ngang.",
            )
            register_password = st.text_input(
                "Mật khẩu",
                type="password",
                key="auth_register_password",
                help="Từ 8 đến 128 ký tự.",
            )
            register_submitted = st.form_submit_button(
                "Tạo tài khoản", key="auth_register_submit"
            )
        if register_submitted:
            try:
                _store_session(api.register(register_username, register_password))
            except DashboardApiError as exc:
                st.error(str(exc))
            else:
                st.rerun()
    return False
