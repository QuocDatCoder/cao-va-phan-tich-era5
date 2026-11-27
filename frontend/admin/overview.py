"""System health, operational trends, and the administration audit log."""
import time
from collections import Counter
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import streamlit as st

from backend.app.domain.live_refresh import MAX_ACTIVE_LIVE_SCHEDULES
from backend.app.domain.time_utils import to_vietnam_time
from frontend.api_client import DashboardApiClient, DashboardApiError

from .collections import ALL_USERS, RECENT_FAILURES, STALE_JOBS, collection_attention
from .live_schedules import LIVE_AUTO_DISABLED, LIVE_AUTO_DISABLED_REASON, LIVE_BACKED_OFF, open_live_schedules
from .shell import format_bytes, format_vn_time, navigate_admin, render_page_header, vietnam_datetime


_JOB_SERIES = ("Hoàn tất", "Đang chạy", "Thất bại / Hủy")
_LIVE_SERIES = ("Đang bật", LIVE_BACKED_OFF, LIVE_AUTO_DISABLED, "Admin đã tắt")
_AUDIT_ACTIONS = {
    "create_user": "Tạo tài khoản",
    "update_user": "Cập nhật quyền hoặc trạng thái tài khoản",
    "reset_password": "Đặt lại mật khẩu",
    "revoke_sessions": "Thu hồi phiên",
    "delete_user": "Xóa vĩnh viễn tài khoản",
    "cancel_job": "Hủy tác vụ thu thập",
    "delete_collection": "Xóa bộ dữ liệu",
    "disable_live_schedule": "Tắt lịch Live",
    "disable_all_live_schedules": "Tắt hàng loạt lịch Live",
    "cleanup_live_schedules": "Dọn lịch Live",
    "refresh_live_schedule": "Cập nhật ngay lịch Live",
    "enable_live_schedule": "Bật lịch Live",
    "list_audit_logs": "Xem nhật ký quản trị",
}
_AUDIT_TARGETS = {
    "user": "Tài khoản", "job": "Tác vụ", "collection": "Bộ dữ liệu",
    "live_schedule": "Lịch Live", "audit_log": "Nhật ký",
}


def _render_overview(api: DashboardApiClient) -> None:
    render_page_header(
        "Tổng quan hệ thống",
        "Giám sát vận hành, lưu trữ và trạng thái cập nhật tự động (UTC+7).",
    )
    try:
        overview = api.admin_overview()
        history = api.admin_collection_history()
        schedules = api.admin_live_schedules()
    except DashboardApiError as exc:
        st.error(str(exc), icon=":material/error:")
        return

    now = datetime.now(UTC)
    _render_health(overview)
    daily_jobs = _daily_jobs(history, to_vietnam_time(now).date())
    _render_kpis(overview, daily_jobs.tail(7), schedules)
    _render_charts(daily_jobs, history, schedules)

    attention, audit = st.columns(2, gap="small")
    with attention.container(border=True, key="admin_panel_attention"):
        _render_attention(history, schedules, now)
    with audit.container(border=True, key="admin_panel_audit"):
        _render_recent_audit(api, overview.get("recent_audit_logs") or [])


def _render_health(overview: dict) -> None:
    scheduler = overview.get("scheduler_status") or {}
    last_error = scheduler.get("last_error")
    storage = format_bytes(overview.get("total_storage_bytes"))
    observations = overview.get("total_observations") or 0
    with st.container(border=True, key="admin_health"):
        backend, live, provider, storage_cell = st.columns([1, 1.6, 1.15, 1.4], gap="small")
        backend.caption("Backend API")
        backend.badge("Trực tuyến", icon=":material/check_circle:", color="green")
        live.caption("Live Scheduler")
        if scheduler.get("running") and last_error:
            live.badge(
                "Đang chạy · Có lỗi gần đây", icon=":material/warning:", color="orange", help=last_error,
            )
        elif scheduler.get("running"):
            live.badge("Đang chạy · quét 1 phút", icon=":material/sync:", color="green")
        else:
            live.badge("Chưa chạy", icon=":material/schedule:", color="orange")
        live.caption(
            f"Lần quét tới · {format_vn_time(scheduler.get('next_run_at'))}\n\n"
            f"Lần quét gần nhất · {format_vn_time(scheduler.get('last_tick_at'))}"
        )
        provider.caption("Nguồn mặc định")
        provider.markdown("**Open-Meteo GFS Global**")
        storage_cell.caption("Lưu trữ")
        storage_cell.markdown(f"**{storage} · {observations:,} điểm quan trắc**")


def _daily_jobs(history: list[dict], today: date) -> pd.DataFrame:
    dates = [today - timedelta(days=offset) for offset in range(29, -1, -1)]
    counts = {day: Counter() for day in dates}
    for job in history:
        created_at = vietnam_datetime(job.get("created_at"))
        if created_at is None or created_at.date() not in counts:
            continue
        status = job.get("status")
        if job.get("collection_status") == "interrupted" or status in ("failed", "cancelled", "interrupted"):
            series = _JOB_SERIES[2]
        else:
            series = _JOB_SERIES[0] if status == "completed" else _JOB_SERIES[1]
        counts[created_at.date()][series] += 1
    return pd.DataFrame([
        {"Ngày": day.strftime("%d/%m"), **{name: counts[day][name] for name in _JOB_SERIES}}
        for day in dates
    ])


def _render_kpis(overview: dict, week: pd.DataFrame, schedules: list[dict]) -> None:
    active_7d = overview.get("active_7d_users", overview.get("active_users")) or 0
    total_users = overview.get("total_users") or 0
    new_today = overview.get("new_today") or 0
    daily_counts = week[list(_JOB_SERIES)].sum(axis=1)
    jobs_7d = daily_counts.sum()
    success_rate = 100 * week["Hoàn tất"].sum() / jobs_7d if jobs_7d else 0
    active_live = [schedule for schedule in schedules if schedule.get("enabled")]
    requested_rpd = effective_rpd = 0.0
    for schedule in active_live:
        requested = schedule.get("requested_interval_hours") or schedule.get("effective_interval_hours") or 1
        effective = schedule.get("effective_interval_hours") or requested
        requested_rpd += 24 / requested
        effective_rpd += 24 / effective
    saved_pct = 100 * (1 - effective_rpd / requested_rpd) if requested_rpd else 0
    live_delta = f"~{effective_rpd:.0f} lượt/ngày"
    if saved_pct > 0:
        live_delta += f" (-{saved_pct:.0f}%)"

    users, storage, jobs, live = st.columns(4, gap="small")
    users.metric(
        "Người dùng hoạt động (7 ngày)", f"{active_7d}/{total_users}",
        delta=f"+{new_today} hôm nay" if new_today else 0,
        delta_description=None if new_today else "hôm nay", border=True, height="stretch",
        help=f"{overview.get('active_sessions') or 0:,} phiên đang mở · "
             f"{overview.get('disabled_users') or 0:,} tài khoản đã vô hiệu hóa",
    )
    storage.metric(
        "Kho dữ liệu & Dung lượng", f"{overview.get('total_collections') or 0:,} bộ",
        delta=format_bytes(overview.get("total_storage_bytes")), delta_color="off",
        border=True, height="stretch",
    )
    jobs.metric(
        "Thu thập (7 ngày)", f"{jobs_7d:,} tác vụ", delta=f"{success_rate:.0f}% thành công",
        delta_color="off", chart_data=daily_counts.tolist(), chart_type="bar",
        border=True, height="stretch",
    )
    live.metric(
        "Cập nhật tự động (Live)", f"{len(active_live):,} đang bật", delta=live_delta, delta_color="off",
        border=True, height="stretch",
    )


def _render_charts(daily_jobs: pd.DataFrame, history: list[dict], schedules: list[dict]) -> None:
    accent = "#60a5fa" if st.context.theme.type == "dark" else "#0f766e"
    left, right = st.columns([3, 2], gap="small")
    with left.container(border=True, key="admin_panel_trend", height="stretch"):
        st.subheader("Xu hướng thu thập theo ngày", anchor=False)
        period = st.segmented_control(
            "Khoảng thời gian", (7, 14, 30), format_func=lambda days: f"{days} ngày",
            default=7, required=True, key="admin_trend_period", label_visibility="collapsed",
        )
        if not history:
            st.info("Chưa có lịch sử thu thập.", icon=":material/info:")
        else:
            st.echarts_chart(
                {
                    "dataset": {"source": daily_jobs.tail(period or 7)},
                    "color": [accent, "#eab308", "#ef4444"],
                    "legend": {"bottom": 0},
                    "grid": {"left": 42, "right": 12, "top": 12, "bottom": 60},
                    "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}, "renderMode": "richText"},
                    "xAxis": {"type": "category", "axisLabel": {"hideOverlap": True}},
                    "yAxis": {"type": "value", "minInterval": 1},
                    "series": [
                        {"name": name, "type": "bar", "stack": "jobs", "encode": {"x": "Ngày", "y": name}}
                        for name in _JOB_SERIES
                    ],
                },
                height=280, key="admin_collection_trend", alt=f"Tác vụ thu thập trong {period or 7} ngày theo trạng thái",
            )

    with right.container(border=True, key="admin_panel_distribution", height="stretch"):
        st.subheader("Phân bố vận hành", anchor=False)
        view = st.segmented_control(
            "Biểu đồ vận hành", ("Trạng thái Live", "Top địa điểm"), default="Trạng thái Live",
            required=True, key="admin_chart_view", label_visibility="collapsed",
        )
        if view == "Top địa điểm":
            _render_top_places(history, accent)
        else:
            _render_live_chart(schedules, accent)


def _render_live_chart(schedules: list[dict], accent: str) -> None:
    counts = Counter()
    for schedule in schedules:
        if schedule.get("enabled"):
            counts[LIVE_BACKED_OFF if schedule.get("backed_off") else "Đang bật"] += 1
        elif schedule.get("disabled_reason") == LIVE_AUTO_DISABLED_REASON:
            counts[LIVE_AUTO_DISABLED] += 1
        elif schedule.get("disabled_reason") == "disabled_by_admin":
            counts["Admin đã tắt"] += 1
    st.echarts_chart(
        {
            "color": [accent, "#eab308", "#f97316", "#94a3b8"],
            "tooltip": {"trigger": "item", "renderMode": "richText"},
            "legend": {"bottom": 0, "type": "scroll"},
            "series": [{
                "type": "pie", "radius": ["45%", "72%"], "center": ["50%", "43%"],
                "label": {"show": False}, "stillShowZeroSum": False,
                "data": [{"name": name, "value": counts[name]} for name in _LIVE_SERIES],
            }],
        },
        height=280, key="admin_live_distribution", alt="Phân bố trạng thái lịch cập nhật Live",
    )


def _render_top_places(history: list[dict], accent: str) -> None:
    counts = Counter(job["place_name"] for job in history if job.get("place_name"))
    if not counts:
        st.info("Chưa có dữ liệu địa điểm.", icon=":material/info:")
        return
    places = pd.DataFrame(counts.most_common(6), columns=["Địa điểm", "Tác vụ"])
    st.echarts_chart(
        {
            "dataset": {"source": places},
            "color": [accent],
            "grid": {"left": 130, "right": 28, "top": 12, "bottom": 28},
            "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}, "renderMode": "richText"},
            "xAxis": {"type": "value", "minInterval": 1},
            "yAxis": {"type": "category", "inverse": True, "axisLabel": {"width": 112, "overflow": "truncate"}},
            "series": [{"type": "bar", "encode": {"x": "Tác vụ", "y": "Địa điểm"}}],
        },
        height=280, key="admin_top_places", alt="Sáu địa điểm có nhiều tác vụ thu thập nhất",
    )


def _render_attention(history: list[dict], schedules: list[dict], now: datetime) -> None:
    st.subheader("Cần chú ý ngay", anchor=False)
    job_alerts = Counter(collection_attention(job, now) for job in history)
    live_backed_off = sum(bool(row.get("enabled") and row.get("backed_off")) for row in schedules)
    live_auto_disabled = sum(row.get("disabled_reason") == LIVE_AUTO_DISABLED_REASON for row in schedules)
    active_by_user = Counter(row["username"] for row in schedules if row.get("username") and row.get("enabled"))
    for row in schedules:
        if (username := row.get("username")) and (reported := row.get("user_active_live_count")) is not None:
            active_by_user[username] = max(active_by_user[username], reported)

    alerts = {
        RECENT_FAILURES: job_alerts[RECENT_FAILURES], STALE_JOBS: job_alerts[STALE_JOBS],
        LIVE_BACKED_OFF: live_backed_off, LIVE_AUTO_DISABLED: live_auto_disabled,
    }
    near_limit = {name: count for name, count in active_by_user.items() if count >= MAX_ACTIVE_LIVE_SCHEDULES - 1}
    if not any(alerts.values()) and not near_limit:
        st.success("Hệ thống vận hành ổn định, chưa có cảnh báo cần xử lý.", icon=":material/check_circle:")
        return

    with st.container(height=340, gap="small"):
        for index, (status, count) in enumerate(alerts.items()):
            if not count:
                continue
            is_job = status in (RECENT_FAILURES, STALE_JOBS)
            with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
                st.text(f"{count} {'tác vụ' if is_job else 'lịch Live'} · {status}")
                if st.button("Xem thu thập" if is_job else "Xem lịch Live",
                             key=f"admin_attention_{index}", icon=":material/arrow_forward:"):
                    if is_job:
                        navigate_admin("collections", admin_history_user=ALL_USERS, admin_history_status=status)
                    else:
                        open_live_schedules(status=status)
        for username, count in sorted(near_limit.items()):
            with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
                st.text(f"{username} · {count}/{MAX_ACTIVE_LIVE_SCHEDULES} lịch Live đang bật")
                if st.button("Xem lịch Live", key=f"admin_attention_limit_{username}", icon=":material/arrow_forward:"):
                    open_live_schedules(username=username)


def _audit_rows(logs: list[dict], limit: int):
    ordered = sorted(
        logs,
        key=lambda row: (vietnam_datetime(row.get("created_at")) or datetime.min.replace(tzinfo=UTC),
                         row.get("id") or 0),
        reverse=True,
    )
    for row in ordered[:limit]:
        action = row.get("action") or "—"
        target_type = row.get("target_type")
        target = " · ".join(str(value) for value in (
            _AUDIT_TARGETS.get(target_type, target_type), row.get("target_id"),
        ) if value is not None)
        yield {
            "Thao tác": _AUDIT_ACTIONS.get(action, action),
            "Người thực hiện": row.get("actor_username") or "—",
            "Đối tượng": target or "—",
            "Chi tiết": row.get("detail") or "—",
            "Thời gian (UTC+7)": format_vn_time(row.get("created_at")),
        }


def _render_recent_audit(api: DashboardApiClient, logs: list[dict]) -> None:
    with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
        st.subheader("Nhật ký quản trị gần đây", anchor=False)
        if st.button("Xem toàn bộ", key="admin_open_audit", icon=":material/history:"):
            _audit_dialog(api)
    if not logs:
        st.caption("Chưa có thao tác quản trị.")
        return
    with st.container(height=340, gap="small"):
        for row in _audit_rows(logs, limit=8):
            with st.container(border=True, gap=None):
                st.text(row["Thao tác"])
                st.text(f'{row["Người thực hiện"]} → {row["Đối tượng"]}')
                st.caption(row["Thời gian (UTC+7)"])
                if row["Chi tiết"] != "—":
                    st.text(row["Chi tiết"])


@st.dialog("Nhật ký quản trị", width="large")
def _audit_dialog(api: DashboardApiClient) -> None:
    try:
        logs = api.admin_audit_logs(limit=100)
    except DashboardApiError as exc:
        st.error(str(exc), icon=":material/error:")
        return
    if not logs:
        st.caption("Chưa có thao tác quản trị.")
        return
    st.dataframe(
        pd.DataFrame(_audit_rows(logs, limit=100)), hide_index=True, width="stretch",
        alt="Nhật ký quản trị", key="admin_audit_table",
    )
