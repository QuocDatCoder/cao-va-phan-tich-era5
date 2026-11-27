"""Account KPIs, searchable user directory, and administration dialogs."""
import os
import re
from datetime import UTC, datetime

import pandas as pd
import streamlit as st

from backend.app.domain.live_refresh import INACTIVE_AFTER_SECONDS, MAX_ACTIVE_LIVE_SCHEDULES
from frontend.api_client import DashboardApiClient, DashboardApiError

from .collections import ALL_STATUSES
from .live_schedules import open_live_schedules
from .shell import format_bytes, format_idle_duration, format_vn_time, navigate_admin, render_page_header, vietnam_datetime


_REGULAR_ROLE = "Người dùng"
_ADMIN_ROLE = "Quản trị viên"
_TABLE_KEY = "admin_users_table"
_USER_COLUMNS = (
    "Tài khoản", "ID", "Vai trò", "Trạng thái", "Hoạt động gần nhất",
    "Phiên mở", "Kho dữ liệu", "Lịch Live", "Dung lượng",
)
_EMPTY_SELECTION = "Chọn một tài khoản trong bảng để xem hồ sơ chi tiết và thao tác quản trị."


def _render_accounts(api: DashboardApiClient, current_user: dict) -> None:
    def render_create_button() -> None:
        if st.button("+ Tạo tài khoản", key="admin_create_user", type="primary"):
            _create_user_dialog(api)

    render_page_header(
        "Quản lý người dùng",
        "Quản lý tài khoản, phiên đăng nhập, dung lượng kho dữ liệu và phân quyền.",
        action_render=render_create_button,
    )
    if notice := st.session_state.pop("admin_account_notice", None):
        st.success(notice, icon=":material/check_circle:")
    try:
        users = api.list_users()
    except DashboardApiError as exc:
        st.error(str(exc), icon=":material/error:")
        return

    now = datetime.now(UTC)
    recently_active = sum((idle := _idle_seconds(account, now)) is not None and idle < INACTIVE_AFTER_SECONDS
                          for account in users)
    metrics = (
        ("Tổng tài khoản", len(users)),
        ("Hoạt động (7 ngày)", recently_active),
        ("Phiên đang mở", sum(account.get("active_sessions") or 0 for account in users)),
        ("Đã vô hiệu hóa", sum(not account.get("is_active", True) for account in users)),
    )
    for card, (label, value) in zip(st.columns(4, gap="small"), metrics):
        card.metric(label, f"{value:,}", border=True, height="stretch")
    _user_directory(api, current_user, users)


def _last_activity(account: dict):
    last_active = account.get("last_active_at")
    return last_active if last_active is not None else account.get("last_login_at")


def _idle_seconds(account: dict, now: datetime) -> int | None:
    if (idle := account.get("idle_seconds")) is not None:
        return idle
    activity = _last_activity(account)
    last = vietnam_datetime(activity if activity is not None else account.get("created_at"))
    return max(0, int((now - last).total_seconds())) if last is not None else None


def _timestamp(value) -> float:
    instant = vietnam_datetime(value)
    return instant.timestamp() if instant is not None else float("-inf")


def _role(account: dict) -> str:
    return _ADMIN_ROLE if account.get("is_admin") else _REGULAR_ROLE


def _status(account: dict) -> str:
    return "Hoạt động" if account.get("is_active", True) else "Đã khóa"


@st.fragment
def _user_directory(api: DashboardApiClient, current_user: dict, users: list[dict]) -> None:
    sort_keys = {
        "Hoạt động gần nhất": lambda account: _timestamp(_last_activity(account)),
        "Dung lượng giảm dần": lambda account: account.get("storage_bytes") or 0,
        "Nhiều bộ dữ liệu nhất": lambda account: account.get("collections_count") or 0,
        "Mới tạo nhất": lambda account: _timestamp(account.get("created_at")),
    }
    with st.container(key="admin_panel_user_directory", border=True):
        search, status_field, role_field, sort_field = st.columns([1.35, 1.4, .9, 1.2], gap="small")
        query = search.text_input(
            "Tìm tài khoản", placeholder="Tên đăng nhập hoặc #ID", key="admin_user_search",
        ).strip().casefold()
        status = status_field.segmented_control(
            "Trạng thái", ("Tất cả", "Hoạt động", "Đã khóa"), default="Tất cả", required=True,
            key="admin_user_status", wrap=True,
        )
        role = role_field.selectbox("Vai trò", ("Tất cả", _REGULAR_ROLE, _ADMIN_ROLE), key="admin_user_role")
        order = sort_field.selectbox("Sắp xếp", tuple(sort_keys), key="admin_user_sort")

        search_by_id = query.startswith("#")
        id_query = query.removeprefix("#")
        rows = [
            account for account in users
            if (id_query == str(account.get("id"))
                or (not search_by_id and query in (account.get("username") or "").casefold()))
            and status in ("Tất cả", _status(account))
            and role in ("Tất cả", _role(account))
        ]
        rows.sort(key=sort_keys[order], reverse=True)
        st.caption(f"{len(rows):,}/{len(users):,} tài khoản · UTC+7")

        # Selections are row positions. Clear them before a different account
        # can occupy the same position after filtering, sorting, or refresh.
        row_ids = tuple(account.get("id") for account in rows)
        if st.session_state.get("admin_user_row_ids") != row_ids:
            st.session_state["admin_user_row_ids"] = row_ids
            st.session_state[_TABLE_KEY] = {"selection": {"rows": [], "columns": [], "cells": []}}

        frame = pd.DataFrame.from_records([
            (
                account.get("username") or "—", account.get("id"), _role(account), _status(account),
                format_vn_time(_last_activity(account)), account.get("active_sessions") or 0,
                account.get("collections_count") or 0, account.get("live_active_count") or 0,
                format_bytes(account.get("storage_bytes")),
            )
            for account in rows
        ], columns=_USER_COLUMNS)
        event = st.dataframe(
            frame, selection_mode="single-row", on_select="rerun", hide_index=True, width="stretch",
            alt="Danh sách người dùng", key=_TABLE_KEY, placeholder="—",
            column_config={"Tài khoản": st.column_config.TextColumn(pinned=True)},
        )

    selected = event.selection.rows
    if not selected or not 0 <= selected[0] < len(rows):
        st.caption(_EMPTY_SELECTION)
        return
    _render_inspector(api, current_user, rows[selected[0]])


def _render_inspector(api: DashboardApiClient, current_user: dict, account: dict) -> None:
    username, user_id = account["username"], account["id"]
    fixed_admin = account.get("is_fixed_admin", username.casefold() == os.getenv("ADMIN_USERNAME", "admin").casefold())
    protected = fixed_admin or user_id == current_user.get("id")
    idle = _idle_seconds(account, datetime.now(UTC))
    last_active = format_vn_time(account.get("last_active_at"))
    if idle is not None and account.get("last_active_at") is not None:
        last_active += f" · {format_idle_duration(idle)}"
    with st.container(border=True, key="admin_panel_user_inspector"):
        st.subheader("Hồ sơ tài khoản", anchor=False)
        with st.container(horizontal=True, vertical_alignment="center"):
            st.text(f"{username} · #{user_id}")
            st.badge(_role(account), icon=":material/person:", color="blue" if account.get("is_admin") else "gray")
            st.badge(_status(account), color="green" if account.get("is_active", True) else "red")
            if protected:
                st.badge("Tài khoản được bảo vệ", icon=":material/lock:", color="gray")

        access, resources, usage = st.columns(3, gap="small")
        access.caption("Ngày tạo")
        access.text(format_vn_time(account.get("created_at")))
        access.caption("Đăng nhập gần nhất")
        access.text(format_vn_time(account.get("last_login_at")))
        access.caption("Hoạt động gần nhất")
        access.text(last_active)
        resources.caption("Phiên đang mở")
        resources.text(f"{account.get('active_sessions') or 0:,}")
        resources.caption("Kho dữ liệu")
        resources.text(f"{account.get('collections_count') or 0:,} bộ")
        resources.caption("Điểm quan trắc")
        resources.text(f"{account.get('observations_count') or 0:,}")
        usage.caption("Lịch Live đang bật")
        usage.text(f"{account.get('live_active_count') or 0}/{MAX_ACTIVE_LIVE_SCHEDULES}")
        usage.caption("Dung lượng")
        usage.text(format_bytes(account.get("storage_bytes")))

        with st.container(horizontal=True, gap="small"):
            if st.button("Xem thu thập của tài khoản này", key=f"admin_user_collections_{user_id}",
                         icon=":material/cloud_download:"):
                navigate_admin("collections", admin_history_user=username, admin_history_status=ALL_STATUSES)
            if st.button("Xem lịch Live", key=f"admin_user_live_{user_id}", icon=":material/sync:"):
                open_live_schedules(username=username)

        if not protected:
            _render_user_actions(api, account)


def _render_user_actions(api: DashboardApiClient, account: dict) -> None:
    user_id = account["id"]
    promote = not account.get("is_admin", False)
    enable = not account.get("is_active", True)
    role = "Cấp quyền quản trị" if promote else "Thu hồi quyền quản trị"
    status = "Kích hoạt lại" if enable else "Vô hiệu hóa"
    st.markdown("**Thao tác quản trị**")
    with st.container(horizontal=True, gap="small"):
        if st.button(role, key=f"admin_user_role_{user_id}", icon=":material/admin_panel_settings:"):
            _confirm_user_action(account, role, api.update_user, user_id, is_admin=promote)
        if st.button(status, key=f"admin_user_status_{user_id}", icon=":material/manage_accounts:"):
            _confirm_user_action(account, status, api.update_user, user_id, is_active=enable)
        if st.button("Đặt lại mật khẩu", key=f"admin_user_password_{user_id}", icon=":material/password:"):
            _reset_password_dialog(api, account)
        if st.button("Thu hồi phiên", key=f"admin_user_revoke_{user_id}", icon=":material/logout:"):
            _confirm_user_action(account, "Thu hồi phiên", api.admin_revoke_sessions, user_id)
        if st.button("Xóa vĩnh viễn", key=f"admin_user_delete_{user_id}", icon=":material/delete_forever:"):
            try:
                live_count = account.get("live_schedules_count")
                if live_count is None:
                    live_count = sum(row.get("user_id") == user_id for row in api.admin_live_schedules())
            except DashboardApiError as exc:
                st.error(str(exc), icon=":material/error:")
            else:
                _delete_user_dialog(api, account, live_count)


def _submit_action(message, operation, *args, **kwargs) -> None:
    try:
        operation(*args, **kwargs)
    except DashboardApiError as exc:
        st.error(str(exc), icon=":material/error:")
    else:
        st.session_state["admin_account_notice"] = message
        st.rerun()


@st.dialog("Tạo tài khoản mới", icon=":material/person_add:")
def _create_user_dialog(api: DashboardApiClient) -> None:
    with st.form("admin_create_user_form", border=False):
        username = st.text_input("Tên đăng nhập", key="admin_new_username", max_chars=32, autocomplete="off")
        password = st.text_input(
            "Mật khẩu", type="password", key="admin_new_password", max_chars=128,
            help="Ít nhất 8 ký tự.", autocomplete="new-password",
        )
        role = st.selectbox("Vai trò", (_REGULAR_ROLE, _ADMIN_ROLE), key="admin_new_role")
        submit = st.form_submit_button("Tạo tài khoản", key="admin_submit_create", type="primary")
    if submit:
        username = username.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
            st.error("Tên đăng nhập cần 3–32 ký tự gồm chữ, số, dấu chấm, gạch dưới hoặc gạch ngang.")
        elif len(password) < 8:
            st.error("Mật khẩu cần ít nhất 8 ký tự.")
        else:
            _submit_action(f"Đã tạo tài khoản {username}.", api.admin_create_user,
                           username, password, is_admin=role == _ADMIN_ROLE)


@st.dialog("Xác nhận thao tác", icon=":material/manage_accounts:")
def _confirm_user_action(account: dict, label: str, operation, *args, **kwargs) -> None:
    username = account["username"]
    st.text(f"{label} cho tài khoản {username}?")
    with st.container(horizontal=True, gap="small"):
        confirm = st.button("Xác nhận", key="admin_confirm_user_action", type="primary")
        cancel = st.button("Hủy", key="admin_cancel_user_action")
    if cancel:
        st.rerun()
    if confirm:
        _submit_action(f"Đã thực hiện thao tác «{label}» cho {username}.", operation, *args, **kwargs)


@st.dialog("Đặt lại mật khẩu", icon=":material/password:")
def _reset_password_dialog(api: DashboardApiClient, account: dict) -> None:
    username = account["username"]
    st.text(username)
    with st.form("admin_reset_password_form", border=False):
        password = st.text_input(
            "Mật khẩu mới", key="admin_reset_password", type="password", max_chars=128,
            help="Ít nhất 8 ký tự.", autocomplete="new-password",
        )
        revoke = st.checkbox("Thu hồi các phiên đăng nhập", value=True, key="admin_reset_revoke_sessions")
        submit = st.form_submit_button("Đặt lại mật khẩu", key="admin_submit_password", type="primary")
    if submit:
        if len(password) < 8:
            st.error("Mật khẩu cần ít nhất 8 ký tự.")
        else:
            _submit_action(f"Đã đặt lại mật khẩu cho {username}.", api.admin_reset_password,
                           account["id"], password, revoke_sessions=revoke)


@st.dialog("Xóa vĩnh viễn", icon=":material/delete_forever:")
def _delete_user_dialog(api: DashboardApiClient, account: dict, live_count: int) -> None:
    username = account["username"]
    st.warning(
        f"Tài khoản {username} sẽ bị xóa vĩnh viễn cùng với "
        f"{account.get('collections_count') or 0:,} bộ dữ liệu, "
        f"{format_bytes(account.get('storage_bytes'))} dung lượng lưu trữ và {live_count:,} lịch Live.",
        icon=":material/warning:",
    )
    typed = st.text_input("Nhập chính xác tên đăng nhập để xác nhận", key="admin_delete_username", autocomplete="off")
    confirm = st.button("Xóa vĩnh viễn", key="admin_submit_delete", type="primary", disabled=typed != username)
    if confirm and typed == username:
        _submit_action(f"Đã xóa tài khoản {username}.", api.delete_user, account["id"])
