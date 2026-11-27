"""Custom workspace navigation and shell controls."""
from pathlib import Path

import streamlit as st

from frontend.auth import logout_account

HOME_SECTION = 'Trang chủ'
NAVIGATION = {
    HOME_SECTION: (HOME_SECTION, 'home'),
    'Thu thập': ('Thu thập', 'cloud_download'),
    'Dữ liệu đã lưu': ('Kho dữ liệu', 'folder_open'),
    'Biểu đồ': ('Biểu đồ', 'monitoring'),
}
_ASSETS = Path(__file__).with_name('components')
_SIDEBAR = st.components.v2.component(
    'era5_workspace_sidebar', isolate_styles=False,
    html='<div class="era5-shell"></div>',
    css=(_ASSETS / 'workspace_sidebar.css').read_text(encoding='utf-8'),
    js=(_ASSETS / 'workspace_sidebar.js').read_text(encoding='utf-8'),
)


def select_section(section):
    if section in NAVIGATION:
        st.session_state['workflow_section'] = section
        st.session_state.pop('workflow_login_requested', None)
        st.session_state['ai_chat_expanded'] = False
        st.query_params['view'] = section


def _navigate():
    event = st.session_state.get('era5_workspace_sidebar')
    select_section(getattr(event, 'navigate', None))


def _login():
    select_section(HOME_SECTION)
    st.session_state['workflow_login_requested'] = True


def render_sidebar(api, *, guest=False):
    if st.session_state.get('workflow_section') not in NAVIGATION:
        requested = st.query_params.get('view')
        st.session_state['workflow_section'] = requested if requested in NAVIGATION else HOME_SECTION
    _SIDEBAR(key='era5_workspace_sidebar', height=0, data={
        'navigation': [{'id': key, 'label': label, 'icon': icon}
                       for key, (label, icon) in NAVIGATION.items()],
        'active': None if guest and st.session_state.get('workflow_login_requested')
                  else st.session_state['workflow_section'],
        'workspaceNavigation': True,
        'guest': guest,
        'username': '' if guest else st.session_state.get('auth_username', ''),
    }, on_navigate_change=_navigate, on_login_change=_login if guest else None,
        on_logout_change=None if guest else lambda: logout_account(api))
