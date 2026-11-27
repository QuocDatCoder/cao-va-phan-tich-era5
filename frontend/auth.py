import time
import os
from dataclasses import replace
from hashlib import sha256

import streamlit as st

from frontend.api_client import DashboardApiClient, DashboardApiError


_TOKEN_KEY = "auth_access_token"
_EXPIRY_KEY = "auth_expires_at"
_USERNAME_KEY = "auth_username"

_ADMIN_REDIRECT = st.components.v2.component(
    "era5_admin_redirect",
    html='<span aria-hidden="true"></span>',
    js="""
export default function({data}) {
  if (data.url && window.top.location.href !== data.url) window.top.location.assign(data.url);
}
""",
)

_SESSION_BRIDGE = st.components.v2.component("era5_auth_session",
    html="<span aria-hidden='true'></span>", js="""
export default function({data, parentElement, setStateValue}) {
  if (data.mode === 'read' && !parentElement.authRead) {
    parentElement.authRead = true;
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
""")


def _store_session(payload: dict) -> None:
    st.session_state[_TOKEN_KEY] = payload["access_token"]
    st.session_state[_EXPIRY_KEY] = payload["expires_at"]
    st.session_state[_USERNAME_KEY] = payload["username"]
    st.session_state['auth_is_admin'] = bool(payload.get('is_admin', False))
    st.session_state['auth_admin_username'] = os.getenv('ADMIN_USERNAME', 'admin').casefold()
    st.session_state['admin_handoff_done'] = False


def restore_account(api: DashboardApiClient) -> bool:
    from frontend.notifications import notify_warning

    ready = st.session_state.get('auth_restore_ready', False)
    token = st.session_state.get(_TOKEN_KEY)
    expires_at = st.session_state.get(_EXPIRY_KEY)
    valid = bool(token and type(expires_at) is int and expires_at > int(time.time()))
    key = 'era5.auth.v1.' + sha256(api.base_url.rstrip('/').encode()).hexdigest()[:20]
    data = dict(key=key, mode='write' if valid else 'clear' if ready else 'read')
    if valid:
        data['session'] = dict(access_token=token, expires_at=expires_at)
    with st.container(key='era5_auth_bridge'):
        bridge = _SESSION_BRIDGE(key='auth_browser_bridge', data=data, height=0,
                                 on_restored_change=lambda: None)
    if not ready and not valid:
        restored = getattr(bridge, 'restored', None)
        if not isinstance(restored, dict):
            return False
        session = restored.get('session')
        if isinstance(session, dict):
            saved_token, deadline = session.get('access_token'), session.get('expires_at')
            if isinstance(saved_token,str) and 1<=len(saved_token)<=256 and type(deadline) is int and deadline>int(time.time()):
                try:
                    user = replace(api, access_token=saved_token, timeout_seconds=8).current_user()
                except DashboardApiError as exc:
                    if exc.status_code not in {401,403}:
                        st.warning('Chưa kiểm tra được phiên đăng nhập. Vui lòng thử lại.')
                        if st.button('Thử lại', key='auth_retry_restore'):
                            st.rerun()
                        return False
                else:
                    _store_session(dict(access_token=saved_token,expires_at=deadline,username=user['username'],is_admin=user.get('is_admin',False)))
        st.session_state['auth_restore_ready'] = True
        st.rerun()
    if valid:
        try:
            user = replace(api, access_token=token, timeout_seconds=8).current_user()
        except DashboardApiError as exc:
            if exc.status_code in {401, 403}:
                st.session_state.clear()
                st.session_state['auth_restore_ready'] = True
                notify_warning('Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.')
                return False
            st.warning('Chưa kiểm tra được quyền tài khoản. Vui lòng thử lại.')
            if st.button('Thử lại', key='auth_retry_current_user'):
                st.rerun()
            return False
        st.session_state[_USERNAME_KEY] = user['username']
        st.session_state['auth_is_admin'] = bool(user.get('is_admin', False))
        st.session_state['auth_admin_username'] = os.getenv('ADMIN_USERNAME', 'admin').casefold()
        if user.get('is_admin') and not st.session_state.get('admin_handoff_done'):
            _redirect_to_admin(replace(api, access_token=token, timeout_seconds=8))
        return True
    if token:
        st.session_state.clear()
        st.session_state['auth_restore_ready'] = True
        notify_warning('Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.')
    return False


def render_login_gate(api: DashboardApiClient, *, on_back):
    with st.container(key='era5_login_card', border=True):
        st.button('Quay lại Trang chủ', key='auth_back_home', icon=':material/arrow_back:',
                  type='tertiary', on_click=on_back)
        _render_login_forms(api)


def logout_account(api):
    from frontend.notifications import notify_error
    try:
        api.logout()
    except DashboardApiError as exc:
        if exc.status_code not in {401,403}:
            notify_error(f"Không thể đăng xuất: {exc}")
            return
    st.session_state.clear()
    st.session_state['auth_restore_ready'] = True
    st.query_params.clear()


def _render_login_forms(api):
    from frontend.notifications import notify_error
    st.html(
        '<div style="display:flex;align-items:center;gap:.45rem;margin:.25rem 0 .35rem;">'
        '<span style="font-size:1.55rem;font-weight:750;letter-spacing:-.02em;">Tài khoản</span>'
        '<strong class="weahub-wordmark" style="font-size:1.55rem;">Weather <span>Hub</span></strong>'
        '</div>'
    )
    st.caption("Đăng nhập để mở kho dữ liệu và tiếp tục công việc của bạn.")
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
                if exc.status_code == 401:
                    notify_error("Tên đăng nhập hoặc mật khẩu không đúng. Nếu tài khoản đã bị khóa, hãy liên hệ quản trị viên để được mở lại.")
                else:
                    notify_error(str(exc))
            else:
                if st.session_state.get('auth_is_admin'):
                    _redirect_to_admin(replace(api, access_token=st.session_state[_TOKEN_KEY]))
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
                notify_error(str(exc))
            else:
                st.rerun()


def _redirect_to_admin(api: DashboardApiClient) -> None:
    from urllib.parse import quote

    try:
        ticket = api.create_admin_handoff()
    except DashboardApiError as exc:
        from frontend.notifications import notify_error
        notify_error(f"Không thể mở trang quản trị: {exc}")
        return
    st.session_state['admin_handoff_done'] = True
    admin_url = os.getenv('ADMIN_BASE_URL', 'http://localhost:8502').rstrip('/')
    target = f"{admin_url}/?handoff={quote(ticket)}"
    _ADMIN_REDIRECT(key='admin_redirect', data={'url': target}, height=0)
    st.stop()
