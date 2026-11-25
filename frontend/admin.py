"""Account administration screen for trusted administrators."""
import streamlit as st

from frontend.api_client import DashboardApiError


def render_admin(api):
    from frontend.notifications import notify_error, notify_success

    st.title('Quản trị tài khoản')
    st.caption('Quản lý quyền truy cập và vai trò của các tài khoản đã đăng ký.')
    try:
        users = api.list_users()
    except DashboardApiError as exc:
        notify_error(str(exc))
        return
    for user in users:
        with st.container(border=True):
            left, role, status, action = st.columns([3, 2, 2, 2], vertical_alignment='center')
            left.markdown(f"**{user['username']}**  ·  ID {user['id']}")
            role.caption('Quản trị viên' if user['is_admin'] else 'Người dùng')
            status.caption('Đang hoạt động' if user['is_active'] else 'Đã khóa')
            is_fixed_admin = user['username'] == st.session_state.get('auth_admin_username', 'admin')
            if is_fixed_admin:
                action.caption('Admin cố định')
            else:
                with action.popover('Thay đổi'):
                    admin = st.checkbox('Quyền quản trị', value=bool(user['is_admin']), key=f"admin_role_{user['id']}")
                    active = st.checkbox('Tài khoản hoạt động', value=bool(user['is_active']), key=f"admin_active_{user['id']}")
                    if st.button('Lưu thay đổi', key=f"admin_save_{user['id']}", type='primary'):
                        try:
                            api.update_user(user['id'], is_admin=admin, is_active=active)
                        except DashboardApiError as exc:
                            notify_error(str(exc))
                        else:
                            notify_success(f"Đã cập nhật tài khoản {user['username']}.")
                            st.rerun()
