"""Administration of Open-Meteo Live Auto-Refresh schedules & Adaptive Backoff."""
from datetime import UTC, datetime

import pandas as pd
import streamlit as st

from backend.app.domain.live_refresh import LIVE_INTERVAL_HOURS
from frontend.api_client import DashboardApiClient, DashboardApiError

from .shell import (
    format_idle_duration,
    format_vn_time,
    navigate_admin,
    render_page_header,
    vietnam_datetime,
)


LIVE_BACKED_OFF = "Đang tự giãn"
LIVE_AUTO_DISABLED = "Tự tắt (7 ngày)"
LIVE_ADMIN_DISABLED = "Admin đã tắt"
LIVE_MANUAL_DISABLED = "Người dùng tự tắt"
LIVE_AUTO_DISABLED_REASON = "inactive_7_days"
LIVE_ADMIN_DISABLED_REASON = "disabled_by_admin"

_VIEW_MODES = (
    "Cần quản lý",
    "Đang bật",
    LIVE_BACKED_OFF,
    "Hệ thống / Admin tắt",
    "Tất cả",
)


def _live_status(row: dict) -> tuple[str, str]:
    if row.get("enabled"):
        return (LIVE_BACKED_OFF, "orange") if row.get("backed_off") else ("Đang bật", "green")
    reason = row.get("disabled_reason")
    if reason == LIVE_AUTO_DISABLED_REASON:
        return LIVE_AUTO_DISABLED, "orange"
    if reason == LIVE_ADMIN_DISABLED_REASON:
        return LIVE_ADMIN_DISABLED, "red"
    return LIVE_MANUAL_DISABLED, "gray"


def open_live_schedules(*, username: str = "", status: str = "Cần quản lý") -> None:
    view_mode = status
    if status == LIVE_AUTO_DISABLED:
        view_mode = "Hệ thống / Admin tắt"
    elif status not in _VIEW_MODES:
        view_mode = "Tất cả" if username else "Cần quản lý"
    navigate_admin(
        "live-schedules",
        admin_live_search=username,
        admin_live_user=username,
        admin_live_view_mode=view_mode,
        admin_live_interval="Tất cả",
    )


def _clear_live_user_preset() -> None:
    st.session_state.pop("admin_live_user", None)


def _format_next_refresh(next_refresh_at: str | None, enabled: bool) -> str:
    if not enabled or not next_refresh_at:
        return "—"
    target = vietnam_datetime(next_refresh_at)
    if target is None:
        return "—"
    now = datetime.now(UTC).astimezone(target.tzinfo)
    diff_seconds = int((target - now).total_seconds())
    if diff_seconds <= 60:
        return "Đến hạn"
    if diff_seconds < 3600:
        return f"Sau {diff_seconds // 60} phút"
    hours = diff_seconds // 3600
    mins = (diff_seconds % 3600) // 60
    return f"Sau {hours}g {mins}p" if mins else f"Sau {hours} giờ"


def _backoff_explanation(row: dict) -> str:
    status_label, _ = _live_status(row)
    username = row.get("username") or "người dùng"
    idle_text = format_idle_duration(row.get("idle_seconds"))
    req = row.get("requested_interval_hours")
    eff = row.get("effective_interval_hours")
    if status_label == "Đang bật":
        return f"Tài khoản **{username}** hoạt động gần đây ({idle_text}) → chạy đúng chu kỳ gốc **{req} giờ**."
    if status_label == LIVE_BACKED_OFF:
        return (
            f"Tài khoản **{username}** không hoạt động ({idle_text}) → Adaptive Backoff tự động giãn chu kỳ "
            f"từ **{req} giờ → {eff} giờ** để tiết kiệm lượt gọi Open-Meteo."
        )
    if status_label == LIVE_AUTO_DISABLED:
        return f"Tài khoản **{username}** không hoạt động quá 7 ngày ({idle_text}) → hệ thống tự động tắt lịch này."
    if status_label == LIVE_ADMIN_DISABLED:
        return f"Quản trị viên đã tắt chủ động lịch cập nhật này của **{username}**."
    return f"Người dùng **{username}** đã tự tắt lịch cập nhật này."


@st.dialog("Tạm dừng toàn bộ lịch cập nhật")
def _confirm_disable_all(api: DashboardApiClient, active_count: int, *, user_id: int | None = None, username: str = "") -> None:
    if user_id is not None:
        st.warning(
            f"Tắt toàn bộ lịch tự động cập nhật đang bật của tài khoản **{username}**?",
            icon=":material/warning:",
        )
    else:
        st.warning(
            f"Thao tác khẩn cấp: tắt toàn bộ **{active_count}** lịch đang bật trên toàn hệ thống?",
            icon=":material/warning:",
        )
    with st.container(horizontal=True, gap="small"):
        if st.button("Xác nhận tắt", type="primary", icon=":material/pause_circle:"):
            try:
                result = api.admin_disable_all_live_schedules(user_id=user_id)
            except DashboardApiError as exc:
                st.error(str(exc))
            else:
                st.toast(f"Đã tắt {result.get('disabled', 0)} lịch cập nhật.", icon=":material/check_circle:")
                st.rerun()
        if st.button("Hủy"):
            st.rerun()


@st.dialog("Dọn lịch đã tắt thủ công")
def _confirm_cleanup(api: DashboardApiClient, manual_count: int) -> None:
    st.write(f"Xóa **{manual_count}** bản ghi lịch do người dùng tự tắt khỏi bảng quản lý?")
    st.caption("Không ảnh hưởng đến bộ dữ liệu đã lưu hay các lịch đang bật / bị hệ thống tắt.")
    with st.container(horizontal=True, gap="small"):
        if st.button("Dọn ngay", type="primary", icon=":material/cleaning_services:"):
            try:
                result = api.admin_cleanup_live_schedules()
            except DashboardApiError as exc:
                st.error(str(exc))
            else:
                st.toast(f"Đã dọn {result.get('deleted', 0)} bản ghi lịch cũ.", icon=":material/check_circle:")
                st.rerun()
        if st.button("Hủy"):
            st.rerun()


def _render_live_inspector(api: DashboardApiClient, row: dict) -> None:
    user_id = row.get("user_id")
    collection_id = row.get("collection_id")
    status_label, color = _live_status(row)

    with st.container(border=True, key="admin_live_inspector"):
        head_l, head_r = st.columns([3, 1], vertical_alignment="center")
        with head_l:
            st.subheader(row.get("place_name") or "—", anchor=False)
            st.caption(
                f"Tài khoản: **{row.get('username') or '—'}** · Mã bộ: `{(collection_id or '—')[:8]}`"
            )
        with head_r.container(horizontal=True, horizontal_alignment="right"):
            st.badge(status_label, color=color)

        st.markdown(_backoff_explanation(row))

        if user_id is None or not collection_id:
            return

        with st.container(horizontal=True, vertical_alignment="bottom", gap="small"):
            if st.button("Chạy cập nhật ngay", key="admin_live_refresh_now", icon=":material/sync:"):
                try:
                    result = api.admin_refresh_live_schedule(user_id, collection_id)
                except DashboardApiError as exc:
                    st.toast(str(exc), icon=":material/error:")
                else:
                    added = result.get("added_hours", 0)
                    msg = (
                        f"Đã cập nhật thêm {added} giờ dữ liệu mới."
                        if result.get("status") == "updated"
                        else "Bộ dữ liệu đã ở giờ mới nhất."
                    )
                    st.toast(msg, icon=":material/check_circle:")
                    st.rerun()

            if row.get("enabled"):
                if st.button("Tắt lịch này", key="admin_live_disable_selected", icon=":material/pause_circle:"):
                    try:
                        api.admin_disable_live_schedule(user_id, collection_id)
                    except DashboardApiError as exc:
                        st.toast(str(exc), icon=":material/error:")
                    else:
                        st.toast("Đã tắt lịch tự động cập nhật.", icon=":material/check_circle:")
                        st.rerun()
            else:
                default_interval = row.get("requested_interval_hours") or 1
                idx = LIVE_INTERVAL_HOURS.index(default_interval) if default_interval in LIVE_INTERVAL_HOURS else 0
                chosen_interval = st.selectbox(
                    "Chu kỳ bật lại",
                    LIVE_INTERVAL_HOURS,
                    index=idx,
                    format_func=lambda h: f"{h} giờ",
                    key="admin_live_reenable_interval",
                    width=130,
                )
                if st.button("Bật lại lịch", key="admin_live_enable_selected", type="primary", icon=":material/play_circle:"):
                    try:
                        api.admin_enable_live_schedule(user_id, collection_id, interval_hours=chosen_interval)
                    except DashboardApiError as exc:
                        st.toast(str(exc), icon=":material/error:")
                    else:
                        st.toast(f"Đã bật lại lịch ({chosen_interval} giờ).", icon=":material/check_circle:")
                        st.rerun()

            if (row.get("user_active_live_count") or 0) >= 2:
                if st.button(
                    f"Tắt hết lịch của {row.get('username')}",
                    key="admin_live_disable_user_all",
                    icon=":material/block:",
                ):
                    _confirm_disable_all(
                        api,
                        int(row.get("user_active_live_count") or 0),
                        user_id=user_id,
                        username=row.get("username") or "",
                    )


def _render_live_schedules(api: DashboardApiClient) -> None:
    try:
        schedules = api.admin_live_schedules()
    except DashboardApiError as exc:
        render_page_header(
            "Quản lý cập nhật",
            "Điều phối lịch tự động cập nhật Open-Meteo và cơ chế tự giãn chu kỳ (Adaptive Backoff).",
        )
        st.error(str(exc))
        return

    active_rows = [r for r in schedules if r.get("enabled")]
    normal_active = sum(1 for r in active_rows if not r.get("backed_off"))
    backed_off_count = sum(1 for r in active_rows if r.get("backed_off"))
    auto_disabled = sum(1 for r in schedules if not r.get("enabled") and r.get("disabled_reason") == LIVE_AUTO_DISABLED_REASON)
    admin_disabled = sum(1 for r in schedules if not r.get("enabled") and r.get("disabled_reason") == LIVE_ADMIN_DISABLED_REASON)
    manual_disabled = sum(1 for r in schedules if not r.get("enabled") and not r.get("disabled_reason"))

    requested_rpd = sum(24.0 / (r.get("requested_interval_hours") or 1) for r in active_rows)
    effective_rpd = sum(24.0 / (r.get("effective_interval_hours") or r.get("requested_interval_hours") or 1) for r in active_rows)
    saved_pct = ((requested_rpd - effective_rpd) / requested_rpd * 100.0) if requested_rpd > 0 else 0.0

    def _header_actions() -> None:
        if st.button(
            "Dọn lịch đã tắt",
            key="admin_live_cleanup_btn",
            icon=":material/cleaning_services:",
            disabled=manual_disabled == 0,
        ):
            _confirm_cleanup(api, manual_disabled)
        if st.button(
            "Tạm dừng tất cả",
            key="admin_live_disable_all_btn",
            icon=":material/pause_circle:",
            disabled=len(active_rows) == 0,
        ):
            _confirm_disable_all(api, len(active_rows))

    render_page_header(
        "Quản lý cập nhật",
        "Điều phối lịch tự động cập nhật Open-Meteo và cơ chế tự giãn chu kỳ (Adaptive Backoff).",
        action_render=_header_actions,
    )

    kpis = st.columns(4, gap="small")
    kpis[0].metric("Đang chạy chuẩn", f"{normal_active:,}", border=True, height="stretch")
    kpis[1].metric("Đang tự giãn (Backoff)", f"{backed_off_count:,}", border=True, height="stretch")
    kpis[2].metric(
        "Tải Open-Meteo ước tính",
        f"~{effective_rpd:.0f} lượt/ngày",
        delta=f"-{saved_pct:.0f}% so với gốc (~{requested_rpd:.0f})" if saved_pct >= 1.0 else "Chu kỳ gốc",
        delta_color="normal" if saved_pct >= 1.0 else "off",
        border=True, height="stretch",
    )
    kpis[3].metric(
        "Đã dừng (Hệ thống / Admin)",
        f"{auto_disabled + admin_disabled:,}",
        delta=f"{auto_disabled} tự tắt 7 ngày · {admin_disabled} Admin tắt",
        delta_color="off",
        border=True, height="stretch",
    )

    _render_live_body(api, schedules)


@st.fragment
def _render_live_body(api: DashboardApiClient, schedules: list[dict]) -> None:
    if st.session_state.get("admin_live_view_mode") not in _VIEW_MODES:
        st.session_state["admin_live_view_mode"] = "Cần quản lý"

    with st.container(key="admin_live_filters"):
        view_mode = st.segmented_control(
            "Chế độ xem",
            options=_VIEW_MODES,
            default=st.session_state.get("admin_live_view_mode", "Cần quản lý"),
            key="admin_live_view_mode",
        ) or "Cần quản lý"
        search_col, interval_col = st.columns((2, 1), gap="small", vertical_alignment="bottom")
        term = search_col.text_input(
            "Tìm kiếm", placeholder="Tài khoản hoặc địa điểm", key="admin_live_search",
            on_change=_clear_live_user_preset,
        ).strip().casefold()
        intervals = ("Tất cả", *(f"{hours} giờ" for hours in LIVE_INTERVAL_HOURS))
        interval = interval_col.selectbox("Chu kỳ thực tế", intervals, key="admin_live_interval")

    preset_user = st.session_state.get("admin_live_user", "").casefold()
    rows = []
    for row in schedules:
        status_label, _ = _live_status(row)
        if view_mode == "Cần quản lý" and status_label == LIVE_MANUAL_DISABLED:
            continue
        elif view_mode == "Đang bật" and status_label != "Đang bật":
            continue
        elif view_mode == LIVE_BACKED_OFF and status_label != LIVE_BACKED_OFF:
            continue
        elif view_mode == "Hệ thống / Admin tắt" and status_label not in {LIVE_AUTO_DISABLED, LIVE_ADMIN_DISABLED}:
            continue

        if preset_user and term == preset_user:
            if (row.get("username") or "").casefold() != preset_user:
                continue
        elif term:
            if term not in (row.get("username") or "").casefold() and term not in (row.get("place_name") or "").casefold():
                continue

        if interval != "Tất cả" and f"{row.get('effective_interval_hours')} giờ" != interval:
            continue
        rows.append(row)

    row_ids = tuple((row.get("user_id"), row.get("collection_id")) for row in rows)
    if st.session_state.get("admin_live_row_ids") != row_ids:
        st.session_state["admin_live_row_ids"] = row_ids
        st.session_state["admin_live_table"] = {"selection": {"rows": [], "columns": [], "cells": []}}

    if not rows:
        st.info("Không có lịch cập nhật nào phù hợp với bộ lọc hiện tại.", icon=":material/info:")
        return

    frame = pd.DataFrame([
        {
            "Tài khoản": (
                f"{row.get('username') or '—'} ({row['user_active_live_count']}/5)"
                if row.get("user_active_live_count") is not None
                else (row.get("username") or "—")
            ),
            "Bộ dữ liệu": row.get("place_name") or "—",
            "Mã bộ": (row.get("collection_id") or "—")[:8],
            "Chu kỳ (Gốc → Thực tế)": (
                f"{row.get('requested_interval_hours', '—')}g → "
                + (f"{row['effective_interval_hours']}g" if row.get("effective_interval_hours") is not None else "—")
            ),
            "Trạng thái": _live_status(row)[0],
            "Hoạt động gần nhất": format_idle_duration(row.get("idle_seconds")),
            "Cập nhật gần nhất (UTC+7)": format_vn_time(row.get("last_refreshed_at")),
            "Lần chạy kế tiếp": _format_next_refresh(row.get("next_refresh_at"), bool(row.get("enabled"))),
        }
        for row in rows
    ])

    st.caption(f"Hiển thị **{len(rows)}/{len(schedules)}** lịch · Chọn một dòng để xem giải thích Adaptive Backoff & điều khiển.")
    selection = st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        selection_mode="single-row",
        on_select="rerun",
        key="admin_live_table",
        alt="Danh sách lịch tự động cập nhật",
    )

    selected_indices = selection.selection.rows if selection and hasattr(selection, "selection") else []
    if selected_indices:
        idx = selected_indices[0]
        if 0 <= idx < len(rows):
            _render_live_inspector(api, rows[idx])
    else:
        st.caption("Chọn một lịch trong bảng để xem chi tiết Adaptive Backoff và điều khiển.")
