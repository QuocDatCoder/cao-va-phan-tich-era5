"""Administration of collection jobs and stored weather datasets."""
from datetime import UTC, datetime, timedelta

import pandas as pd
import streamlit as st

from frontend.api_client import DashboardApiClient, DashboardApiError
from frontend.components.charts import variable_label
from frontend.dashboard import _translate_status

from .shell import format_vn_time, render_page_header, vietnam_datetime


ALL_USERS = "Tất cả người dùng"
ALL_STATUSES = "Tất cả"
RECENT_FAILURES = "Thất bại / Gián đoạn (48 giờ)"
STALE_JOBS = "Chờ / Chạy quá 1 giờ"

_SOURCE_LABELS = {
    "open_meteo": "Open-Meteo",
    "era5": "ERA5",
    "land": "ERA5-Land",
    "mock": "Giả lập",
}
_STATUS_OPTIONS = (
    ALL_STATUSES,
    "Hoàn tất",
    "Đang chạy",
    "Thiếu dữ liệu",
    "Thất bại",
    "Đã hủy",
)
_TIME_RANGES = {
    "Tất cả": None,
    "Hôm nay": 1,
    "7 ngày": 7,
    "30 ngày": 30,
}


def collection_attention(job: dict, now: datetime) -> str | None:
    status = job.get("status")
    if status in ("failed", "interrupted") or job.get("collection_status") == "interrupted":
        event_at = vietnam_datetime(job.get("completed_at") or job.get("updated_at") or job.get("created_at"))
        if event_at is not None and timedelta(0) <= now - event_at <= timedelta(hours=48):
            return RECENT_FAILURES
    elif status in ("queued", "running"):
        updated_at = vietnam_datetime(job.get("updated_at") or job.get("created_at"))
        if updated_at is not None and now - updated_at >= timedelta(hours=1):
            return STALE_JOBS
    return None


def _display_status(job: dict) -> str:
    col_status = job.get("collection_status")
    status = job.get("status") or ""
    if col_status == "interrupted" or status == "interrupted":
        return "Gián đoạn"
    if col_status == "partial" and status == "completed":
        return "Thiếu dữ liệu"
    return _translate_status(status)


def _format_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "—"
    secs = max(0.0, float(seconds))
    if secs < 60:
        return f"{secs:.1f} giây"
    minutes, rem = divmod(int(secs), 60)
    if minutes < 60:
        return f"{minutes} phút {rem} giây"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} giờ {minutes} phút"


def _format_date_range(start: str | None, end: str | None) -> str:
    def _fmt(raw: str | None) -> str:
        if not raw:
            return "—"
        try:
            return datetime.fromisoformat(raw).strftime("%d/%m/%Y")
        except ValueError:
            return raw

    return f"{_fmt(start)} → {_fmt(end)}"


def _source_label(source: str | None) -> str:
    if not source:
        return "—"
    return _SOURCE_LABELS.get(source, source)


def _variables_summary(job: dict) -> str:
    count = job.get("variables_count")
    source_text = _source_label(job.get("source"))
    if isinstance(count, int) and count > 0:
        return f"{count} biến · {source_text}" if source_text != "—" else f"{count} biến"
    var_text = variable_label(job.get("variable"))
    return f"{var_text} · {source_text}" if source_text != "—" else var_text


def _coverage_percent(job: dict) -> float | None:
    coverage = job.get("coverage")
    if isinstance(coverage, (int, float)):
        return round(max(0.0, min(100.0, float(coverage) * 100.0)), 1)
    progress = job.get("progress")
    if isinstance(progress, (int, float)):
        return float(progress)
    return None


@st.dialog("Xác nhận hủy tác vụ")
def _confirm_cancel_job(api: DashboardApiClient, job: dict) -> None:
    place = job.get("place_name") or (job.get("job_id") or "")[:8]
    st.write(f"Hủy tác vụ thu thập **{place}** của tài khoản **{job['username']}**?")
    st.caption(f"Mã tác vụ: `{(job.get('job_id') or '')[:8]}`")
    with st.container(horizontal=True, gap="small"):
        if st.button("Hủy tác vụ", type="primary", icon=":material/cancel:"):
            try:
                api.admin_cancel_job(job["user_id"], job["job_id"])
            except DashboardApiError as exc:
                st.error(str(exc))
            else:
                st.toast("Đã gửi yêu cầu hủy tác vụ.", icon=":material/check_circle:")
                st.rerun()
        if st.button("Đóng"):
            st.rerun()


@st.dialog("Xác nhận xóa bộ dữ liệu")
def _confirm_delete_collection(api: DashboardApiClient, job: dict) -> None:
    place = job.get("place_name") or (job.get("job_id") or "")[:8]
    st.warning(
        f"Thao tác này sẽ xóa vĩnh viễn bộ dữ liệu **{place}** của tài khoản **{job['username']}**, "
        "bao gồm lịch sử tác vụ và lịch tự động cập nhật đi kèm (nếu có).",
        icon=":material/warning:",
    )
    with st.container(horizontal=True, gap="small"):
        if st.button("Xóa vĩnh viễn", type="primary", icon=":material/delete:"):
            try:
                api.admin_delete_collection(job["user_id"], job["job_id"])
            except DashboardApiError as exc:
                st.error(str(exc))
            else:
                st.toast("Đã xóa bộ dữ liệu và lịch sử liên quan.", icon=":material/check_circle:")
                st.rerun()
        if st.button("Hủy"):
            st.rerun()


def _render_collection_inspector(api: DashboardApiClient, job: dict) -> None:
    with st.container(border=True, key="admin_collection_inspector"):
        title = job.get("place_name") or "Chưa ghi nhận địa điểm"
        header_col, action_col = st.columns([3, 2], vertical_alignment="center")
        with header_col:
            st.subheader(title, anchor=False)
            st.caption(
                f"Tài khoản: **{job['username']}** · Mã: `{(job.get('job_id') or '—')[:8]}` "
                f"· Nguồn: {_source_label(job.get('source'))}"
            )
        with action_col.container(horizontal=True, horizontal_alignment="right", gap="small"):
            status = job.get("status")
            if status in {"queued", "running"} and job.get("user_id") is not None and job.get("job_id"):
                if st.button("Hủy tác vụ", key="admin_col_cancel_btn", icon=":material/cancel:"):
                    _confirm_cancel_job(api, job)
            elif job.get("user_id") is not None and job.get("job_id"):
                if st.button("Xóa bộ dữ liệu", key="admin_col_delete_btn", icon=":material/delete:"):
                    _confirm_delete_collection(api, job)

        left, right = st.columns(2, gap="medium")
        with left:
            lat, lon = job.get("latitude"), job.get("longitude")
            coords = f"{lat:.4f}, {lon:.4f}" if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) else "—"
            variables = job.get("variables") or []
            var_names = ", ".join(variable_label(v) for v in variables) if variables else variable_label(job.get("variable"))
            available, expected = job.get("available_count"), job.get("expected_count")
            coverage_text = (
                f"{available:,}/{expected:,} giờ ({_coverage_percent(job):.1f}%)"
                if isinstance(available, int) and isinstance(expected, int) and expected > 0
                else (f"{_coverage_percent(job):.1f}%" if _coverage_percent(job) is not None else "—")
            )
            st.markdown(
                f"- **Tọa độ:** `{coords}`\n"
                f"- **Khoảng dữ liệu:** {_format_date_range(job.get('start_date'), job.get('end_date'))}\n"
                f"- **Biến khí tượng:** {var_names}\n"
                f"- **Độ phủ dữ liệu:** {coverage_text}"
            )
        with right:
            imported = job.get("imported_values")
            imported_text = f"{imported:,}" if isinstance(imported, int) else "—"
            live_text = "Đang ghim Live" if job.get("has_live_schedule") else "Không ghim"
            st.markdown(
                f"- **Điểm quan trắc:** {imported_text}\n"
                f"- **Thời lượng xử lý:** {_format_duration(job.get('duration_seconds'))}\n"
                f"- **Bắt đầu → Kết thúc:** {format_vn_time(job.get('created_at'))} → {format_vn_time(job.get('completed_at'))}\n"
                f"- **Cập nhật tự động:** {live_text}"
            )

        error_msg = job.get("error")
        issues_count = job.get("quality_issues_count") or 0
        if error_msg:
            st.error(error_msg, icon=":material/error:")
        elif issues_count > 0:
            st.warning(f"Bộ dữ liệu có {issues_count} cảnh báo chất lượng.", icon=":material/warning:")
        elif _display_status(job) == "Hoàn tất":
            st.badge("Đủ dữ liệu · 0 lỗi chất lượng", color="green", icon=":material/check_circle:")


def _render_collections(api: DashboardApiClient) -> None:
    try:
        users = api.list_users()
        history = api.admin_collection_history()
    except DashboardApiError as exc:
        render_page_header(
            "Quản lý thu thập",
            "Giám sát tác vụ thu thập, độ phủ dữ liệu và chất lượng kho khí tượng (UTC+7).",
        )
        st.error(str(exc))
        return

    render_page_header(
        "Quản lý thu thập",
        "Giám sát tác vụ thu thập, độ phủ dữ liệu và chất lượng kho khí tượng (UTC+7).",
    )

    total = len(history)
    completed = sum(1 for j in history if j.get("status") == "completed")
    running = sum(1 for j in history if j.get("status") in {"queued", "running"})
    failed = sum(
        1 for j in history
        if j.get("status") in {"failed", "interrupted"} or j.get("collection_status") == "interrupted"
    )
    total_imported = sum(int(j.get("imported_values") or 0) for j in history)
    coverages = [float(j["coverage"]) * 100.0 for j in history if isinstance(j.get("coverage"), (int, float))]
    avg_cov = sum(coverages) / len(coverages) if coverages else None
    success_rate = (completed / total * 100.0) if total else 0.0

    kpis = st.columns(4, gap="small")
    kpis[0].metric(
        "Tổng tác vụ", f"{total:,}",
        delta=f"{running} đang chạy" if running else "Không có tác vụ treo",
        delta_color="normal" if running else "off",
        border=True, height="stretch",
    )
    kpis[1].metric(
        "Tỷ lệ hoàn tất", f"{success_rate:.1f}%",
        delta=f"{completed:,}/{total:,} tác vụ",
        delta_color="off",
        border=True, height="stretch",
    )
    kpis[2].metric(
        "Thất bại / Gián đoạn", f"{failed:,}",
        delta="Cần kiểm tra" if failed else "Ổn định",
        delta_color="inverse" if failed else "off",
        border=True, height="stretch",
    )
    kpis[3].metric(
        "Tổng điểm quan trắc", f"{total_imported:,}",
        delta=f"Độ phủ TB {avg_cov:.1f}%" if avg_cov is not None else "Chưa có dữ liệu",
        delta_color="off",
        border=True, height="stretch",
    )

    _render_collections_body(api, users, history)


@st.fragment
def _render_collections_body(api: DashboardApiClient, users: list[dict], history: list[dict]) -> None:
    usernames = {account["username"] for account in users if account.get("username")}
    usernames.update(job["username"] for job in history if job.get("username"))
    preset_user = st.session_state.get("admin_history_user")
    if preset_user and preset_user != ALL_USERS:
        usernames.add(preset_user)

    preset_status = st.session_state.get("admin_history_status")
    status_choices = list(_STATUS_OPTIONS)
    if preset_status in (RECENT_FAILURES, STALE_JOBS) and preset_status not in status_choices:
        status_choices.append(preset_status)
    if preset_status not in status_choices:
        st.session_state["admin_history_status"] = ALL_STATUSES

    with st.container(key="admin_history_filter"):
        row1_search, row1_user, row1_source, row1_time = st.columns((2, 1.2, 1, 1), gap="small")
        query = row1_search.text_input(
            "Tìm kiếm", placeholder="Địa điểm, tài khoản hoặc mã tác vụ", key="admin_history_search",
        ).strip().casefold()
        username = row1_user.selectbox(
            "Người dùng", (ALL_USERS, *sorted(usernames, key=str.casefold)), key="admin_history_user",
        )
        source_filter = row1_source.selectbox(
            "Nguồn dữ liệu", ("Tất cả", "Open-Meteo", "ERA5", "Giả lập"), key="admin_history_source",
        )
        time_filter = row1_time.selectbox(
            "Thời gian tạo", tuple(_TIME_RANGES.keys()), key="admin_history_time",
        )
        status = st.segmented_control(
            "Trạng thái",
            options=status_choices,
            default=st.session_state.get("admin_history_status", ALL_STATUSES),
            key="admin_history_status",
        ) or ALL_STATUSES

    now = datetime.now(UTC)
    days_limit = _TIME_RANGES.get(time_filter)
    cutoff = now - timedelta(days=days_limit) if days_limit else None

    rows = []
    for job in history:
        if username != ALL_USERS and job.get("username") != username:
            continue
        if source_filter != "Tất cả" and _source_label(job.get("source")) != source_filter:
            continue
        if cutoff is not None:
            created = vietnam_datetime(job.get("created_at"))
            if created is None or created < cutoff:
                continue
        if status in (RECENT_FAILURES, STALE_JOBS):
            if collection_attention(job, now) != status:
                continue
        elif status != ALL_STATUSES:
            disp = _display_status(job)
            if status == "Thất bại":
                if disp not in {"Thất bại", "Gián đoạn"}:
                    continue
            elif disp != status:
                continue
        if query:
            haystack = " ".join(
                str(v or "") for v in (job.get("username"), job.get("place_name"), job.get("job_id"))
            ).casefold()
            if query not in haystack:
                continue
        rows.append(job)

    row_ids = tuple(job.get("job_id") for job in rows)
    if st.session_state.get("admin_history_row_ids") != row_ids:
        st.session_state["admin_history_row_ids"] = row_ids
        st.session_state["admin_history_table"] = {"selection": {"rows": [], "columns": [], "cells": []}}

    if not rows:
        st.info("Chưa có lịch sử thu thập phù hợp.", icon=":material/info:")
        return

    frame = pd.DataFrame([
        {
            "Tài khoản": job.get("username") or "—",
            "Địa điểm": job.get("place_name") or "—",
            "Khoảng dữ liệu": _format_date_range(job.get("start_date"), job.get("end_date")),
            "Biến & Nguồn": _variables_summary(job),
            "Độ phủ (%)": _coverage_percent(job),
            "Điểm dữ liệu": int(job.get("imported_values") or 0),
            "Trạng thái": _display_status(job),
            "Thời lượng": _format_duration(job.get("duration_seconds")),
            "Tạo lúc (UTC+7)": format_vn_time(job.get("created_at")),
            "Live": "Đang ghim" if job.get("has_live_schedule") else "—",
        }
        for job in rows
    ])
    frame["Độ phủ (%)"] = pd.to_numeric(frame["Độ phủ (%)"], errors="coerce")

    with st.container(key="admin_history"):
        bar_left, bar_right = st.columns([3, 1], vertical_alignment="center")
        bar_left.caption(f"Hiển thị **{len(rows)}/{len(history)}** tác vụ · Chọn một dòng để xem chi tiết & thao tác.")
        with bar_right.container(horizontal=True, horizontal_alignment="right"):
            st.download_button(
                "Xuất CSV", frame.to_csv(index=False).encode("utf-8-sig"),
                file_name="lich-su-thu-thap.csv", mime="text/csv", key="admin_history_csv",
                on_click="ignore", icon=":material/download:",
            )

        selection = st.dataframe(
            frame,
            hide_index=True,
            width="stretch",
            selection_mode="single-row",
            on_select="rerun",
            key="admin_history_table",
            alt="Danh sách tác vụ thu thập",
            column_config={
                "Độ phủ (%)": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f%%"),
                "Điểm dữ liệu": st.column_config.NumberColumn(format="%d"),
            },
        )

    selected_indices = selection.selection.rows if selection and hasattr(selection, "selection") else []
    if selected_indices:
        idx = selected_indices[0]
        if 0 <= idx < len(rows):
            _render_collection_inspector(api, rows[idx])
    else:
        st.caption("Chọn một dòng trong bảng để xem chi tiết kỹ thuật, lỗi chất lượng và thao tác quản trị.")
