import os
from datetime import UTC, datetime, timedelta, timezone
from time import monotonic
from typing import Any, Callable

import pandas as pd
import streamlit as st

from frontend.api_client import DEFAULT_TIMEOUT_SECONDS, DashboardApiClient, DashboardApiError
from frontend.live_updates import (
    completion_events, follow_window, latest_synced_date, matching_states, revision_key,
)
from frontend.components.charts import (
    heatmap_figure,
    map_figure,
    timeseries_figure,
    timeseries_frame,
)
from frontend.components.controls import render_filter_controls


def build_client() -> DashboardApiClient:
    return DashboardApiClient(
        base_url=os.getenv("API_BASE_URL", "http://localhost:8000"),
        timeout_seconds=float(os.getenv("API_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))),
        source=st.session_state.get("stored_source", "land"),
        stored_only=True,
    )


def main() -> None:
    st.set_page_config(page_title="Bảng điều khiển khí hậu ERA5", layout="wide")
    st.title("Bảng điều khiển khí hậu ERA5")
    st.caption("Dữ liệu hiển thị theo giờ Việt Nam (UTC+7); nhiệt độ và điểm sương dùng °C.")

    with st.sidebar:
        st.selectbox("Nguồn dữ liệu", ["land", "era5", "mock"], key="stored_source", format_func=lambda x: {
            "land": "Đất liền — ERA5-Land time-series (điểm)",
            "era5": "ERA5 chung — hỗ trợ cả biển (vùng)",
            "mock": "Demo — dữ liệu giả lập đã thu thập",
        }[x])
        st.caption("Truy vấn và biểu đồ chỉ đọc database. Tải CDS cần bấm Thu thập riêng.")
    client = build_client()
    params = render_filter_controls(follow_latest=False)
    if params["variable"] == "wind_direction":
        st.caption("Hướng gió khí tượng: Bắc 0°, Đông 90°, Nam 180°, Tây 270°. Giờ lặng gió không có hướng và được bỏ qua.")
    elif params["variable"] == "solar_radiation":
        st.caption("Bức xạ là năng lượng tích lũy trong từng giờ, đơn vị MJ/m².")

    with st.sidebar:
        st.header("Máy chủ API")
        st.code(client.base_url)
        if st.button("Kiểm tra máy chủ"):
            _run_request("trạng thái máy chủ", client.health, lambda _: st.success("Đang hoạt động"))
        _render_collection(client, params)
        if st.button("Làm mới dữ liệu đã lưu"):
            _cached_get.clear()
            st.rerun()

    # st.tabs chạy code của TẤT CẢ tab mỗi lần render => gọi 7+ API cùng lúc.
    # Dùng radio để chỉ render (và chỉ gọi API cho) mục đang chọn.
    section = st.radio(
        "Mục hiển thị",
        ["Tổng quan", "Chuỗi thời gian", "Không gian"],
        horizontal=True,
        label_visibility="collapsed",
        key="active_section",
    )
    if section == "Tổng quan":
        _render_overview(client, params)
    elif section == "Chuỗi thời gian":
        _render_temporal_analysis(client, params)
    else:
        _render_map(client, params)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_get(base_url: str, timeout_seconds: float, endpoint: str, params_key: tuple, revision: str = "",
                source: str | None = None, stored_only: bool = False) -> dict[str, Any]:
    client = DashboardApiClient(base_url=base_url, timeout_seconds=timeout_seconds,
                                source=source, stored_only=stored_only)
    params = dict(params_key)
    if endpoint == "timeseries":
        if params.get("frequency", "hourly") != "hourly":
            return client.get_aggregate(params)
        return client.get_timeseries(params)
    if endpoint == "statistics":
        return client.get_statistics(params)
    if endpoint == "trend":
        return client.get_trend(params)
    if endpoint == "heatmap":
        return client.get_heatmap(params)
    if endpoint == "map":
        return client.get_map(params)
    raise DashboardApiError(f"Endpoint dashboard không được hỗ trợ: {endpoint}")


def _params_key(params: dict[str, Any]) -> tuple:
    return tuple(sorted(params.items()))


def _load_cached(client: DashboardApiClient, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    return _cached_get(
        base_url=client.base_url,
        timeout_seconds=client.timeout_seconds,
        endpoint=endpoint,
        params_key=_params_key(params),
        revision=st.session_state.get("live_revision", ""),
        source=client.source,
        stored_only=client.stored_only,
    )


def _collection_payload(params, source):
    payload = _download_payload(params)
    payload["source"] = source
    if source in {"land", "mock"}:
        payload.pop("area")
        payload["location"] = {"latitude": float(params["latitude"]), "longitude": float(params["longitude"])}
    return payload


def _render_collection(client, params):
    st.subheader("Thu thập chủ động")
    payload = _collection_payload(params, client.source)
    st.caption(f"Ngày tải theo UTC: {payload['start_date']} đến {payload['end_date']}.")
    if client.source == "land":
        st.caption("Tải một điểm. Provider hiện xin cả 6 biến gốc để tái sử dụng; biến đang xem là biến yêu cầu.")
    elif client.source == "mock":
        st.caption("Sinh dữ liệu giả lập tại một điểm và lưu riêng dưới nguồn mock.")
    else:
        st.caption("Thu thập toàn bộ lưới trong vùng sau. ERA5 chung không phải bộ lọc chỉ lấy biển.")
        area = payload["area"]
        for key, label in [("north", "Bắc"), ("south", "Nam"), ("west", "Tây"), ("east", "Đông")]:
            limit = 90.0 if key in {"north", "south"} else 180.0
            area[key] = st.number_input(label + " của vùng", min_value=-limit, max_value=limit,
                                        value=area[key], key="collect_area_" + key)
    context = f"{client.base_url}|{client.source}"
    jobs = st.session_state.get("collection_jobs", {})
    if st.button("Bắt đầu thu thập", key="collect_now"):
        try:
            response = client.create_download(payload)
            st.session_state["collection_jobs"] = {**jobs, context: response["job_id"]}
            st.success("Đã tiếp nhận tác vụ. Theo dõi trạng thái bên dưới.")
        except DashboardApiError as exc:
            st.error(str(exc))
    _poll_collection(client, context)


@st.fragment(run_every=15)
def _poll_collection(client, context):
    job_id = st.session_state.get("collection_jobs", {}).get(context)
    if not job_id:
        return
    try:
        job = client.get_job(job_id)
        st.write("Trạng thái thu thập: " + _translate_status(job["status"]))
        st.caption(f"Nguồn: {job.get('source')} · Dataset: {job.get('dataset')} · Phạm vi: {job.get('spatial_mode')}")
        if job.get("retrieval_variables"):
            st.caption("Các biến trong yêu cầu tải: " + ", ".join(job["retrieval_variables"]))
        quality = job.get("quality")
        if quality:
            st.caption(f"Chất lượng: {quality['status']} · Đã lưu: {quality['imported_values']} giá trị · Cảnh báo: {quality['issue_count']}")
            if not quality["complete"]:
                st.warning("Dữ liệu tải chưa đầy đủ hoặc có lỗi chất lượng; mốc đồng bộ không được nâng.")
            with st.expander("Báo cáo chất lượng của tác vụ"):
                st.json(quality)
                details = client.get_job_quality(job_id)
                st.caption(f"Hiển thị {len(details['issues'])}/{details['total']} cảnh báo đầu tiên.")
                if details["issues"]:
                    st.dataframe(details["issues"], hide_index=True)
        if job.get("error"):
            st.error(job["error"])
        if job["status"] == "completed" and st.session_state.get("collection_completed") != job_id:
            st.session_state["collection_completed"] = job_id
            _cached_get.clear()
            st.rerun(scope="app")
    except DashboardApiError as exc:
        st.error(str(exc))


@st.fragment(run_every=15)
def _render_live_updates(client, params, follow, days, auto_sync):
    st.subheader("Đồng bộ dữ liệu")
    context = (client.base_url, params["variable"], params["latitude"], params["longitude"],
               follow, days, params["start"] if not follow else None, params["end"] if not follow else None)
    if st.session_state.get("live_context") != context:
        st.session_state["live_context"] = context
        st.session_state["live_events"] = {}
        st.session_state["live_revision"] = None
        st.session_state["live_last_attempt"] = None
        st.session_state["live_states"] = []
    try:
        config = client.get_sync_config()
        states = matching_states(client.list_synchronizations()["states"], params)
        limit = min(config["max_days_per_request"], config["max_days_per_run"])
        if follow and days + 1 > limit:
            st.warning(f"Hãy chọn tối đa {max(0, limit-1)} ngày theo dõi theo cấu hình máy chủ.")
            st.session_state["live_states"] = []
            return
        target = follow_window(params, config["latest_available_date"], days) if follow else params
        payload = _download_payload(target)
        payload["latest_available_date"] = payload.pop("end_date")
        in_flight = any(state["status"] in {"queued", "running"} for state in states)
        manual = st.button("Đồng bộ khoảng đang chọn", disabled=in_flight)
        last_attempt = st.session_state.get("live_last_attempt")
        due = last_attempt is None or monotonic() - last_attempt >= config["interval_hours"] * 3600
        if manual or (follow and auto_sync and due and not in_flight):
            st.session_state["live_last_attempt"] = monotonic()
            response = client.run_synchronization(payload)
            states = [response] + [state for state in states if state["sync_id"] != response["sync_id"]]

        job_id = st.session_state.get("download_job_id")
        job = client.get_job(job_id) if job_id else None
        if job:
            st.caption(f"Tác vụ tải: {_translate_status(job['status'])}")
            if job["status"] == "failed":
                st.error(job.get("error") or "Tác vụ tải thất bại.")
        events = completion_events(st.session_state.get("live_events", {}), states, job)
        old_revision = st.session_state.get("live_revision")
        revision = revision_key(config["provider"], events)
        previous_latest = latest_synced_date(st.session_state.get("live_states", []))
        st.session_state["live_states"] = states
        st.session_state["live_events"] = events
        st.session_state["live_revision"] = revision
        st.caption(f"Kiểm tra trạng thái mỗi 15 giây; đồng bộ tự động mỗi {config['interval_hours']} giờ khi bật theo dõi.")
        st.caption("Lịch đồng bộ backend: " + ("đang bật" if config["scheduler_enabled"] else "đang tắt"))
        if states:
            current = states[0]
            st.write("Trạng thái: " + _translate_status(current["status"]))
            if current.get("error"):
                st.error(current["error"])
            synced = latest_synced_date(states)
            if synced:
                st.caption(f"Đã đồng bộ đến ngày {synced} UTC; theo dõi hiển thị các ngày Việt Nam trọn vẹn.")
            completed_at = current.get("completed_at")
            if completed_at:
                timestamp = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
                st.caption("Lần kết thúc gần nhất: " + timestamp.astimezone(
                    timezone(timedelta(hours=7))
                ).strftime("%d/%m/%Y %H:%M UTC+7"))
        else:
            st.info("Chưa có tác vụ đồng bộ cho vị trí và biến này.")
        if ((old_revision is not None and old_revision != revision)
            or (follow and previous_latest != latest_synced_date(states))):
            st.rerun(scope="app")
    except DashboardApiError as exc:
        st.error("Không đọc được trạng thái đồng bộ: " + str(exc))


def _render_overview(client: DashboardApiClient, params: dict[str, Any]) -> None:
    st.subheader("Tổng quan")
    left, right = st.columns([3, 2])
    with left:
        _render_statistics(client, params)
    with right:
        _render_trend(client, params)


def _render_temporal_analysis(client: DashboardApiClient, params: dict[str, Any]) -> None:
    st.subheader("Chuỗi thời gian")
    _render_timeseries(client, params, show_title=False)
    if params["variable"] == "wind_direction":
        st.info("Hướng gió là đại lượng vòng tròn; xem trung bình vòng tròn trong tab Tổng quan.")
        return
    st.divider()
    _render_heatmap(client, params)


def _render_timeseries(
    client: DashboardApiClient,
    params: dict[str, Any],
    show_title: bool = True,
) -> None:
    if show_title:
        st.subheader("Chuỗi thời gian")
    _run_request(
        "chuỗi thời gian",
        lambda: _load_cached(client, "timeseries", params),
        lambda payload: _show_timeseries(payload),
    )
    if st.button("Làm mới chuỗi thời gian", key="refresh_timeseries"):
        _cached_get.clear()
        st.rerun()


def _show_timeseries(payload: dict[str, Any]) -> None:
    if not payload.get("data"):
        st.info("Không có quan sát nào cho bộ lọc đã chọn.")
        return
    if "aggregation" in payload:
        aggregation_names = {
            "mean": "trung bình", "sum": "tổng", "min": "nhỏ nhất", "max": "lớn nhất",
            "circular_mean": "trung bình vòng tròn", "auto": "tự động",
        }
        aggregation_label = aggregation_names.get(str(payload["aggregation"]), payload["aggregation"])
        st.caption(f"Tổng hợp: {aggregation_label} · Múi giờ: UTC+7. Min/max trong bảng là cực trị của các quan sát giờ.")
        if any(point.get("partial_period") for point in payload["data"]):
            st.info("Có kỳ chưa trọn ngày/tháng/năm vì khoảng truy vấn chỉ bao phủ một phần kỳ đó.")
        if any(point.get("coverage", 1) < 1 for point in payload["data"]):
            st.info("Có kỳ chưa đủ quan sát; riêng hướng gió bỏ qua các giờ lặng gió.")
    st.plotly_chart(timeseries_figure(payload), use_container_width=True)
    _show_extremes(payload)
    table = timeseries_frame(payload).rename(columns={
        "time": "Thời gian Việt Nam (UTC+7)", "value": "Giá trị", "period_end": "Kết thúc kỳ (không gồm)",
        "min": "Nhỏ nhất theo giờ", "max": "Lớn nhất theo giờ", "count": "Số quan sát",
        "expected_count": "Số giờ yêu cầu", "coverage": "Tỷ lệ quan sát",
        "partial_period": "Kỳ chưa trọn", "complete": "Kỳ đầy đủ",
    })
    if payload.get("source"):
        grid = payload.get("grid_location") or {}
        for name, value in {
            "source": payload["source"], "dataset": payload["dataset"], "unit": payload["unit"],
            "variable": payload["variable"],
            "requested_latitude": payload["location"]["latitude"],
            "requested_longitude": payload["location"]["longitude"],
            "grid_latitude": grid.get("latitude"), "grid_longitude": grid.get("longitude"),
            "query_coverage": payload["coverage"], "query_complete": payload["complete"],
        }.items():
            table[name] = value
    st.dataframe(table, use_container_width=True)
    st.download_button(
        "Tải bảng dữ liệu (CSV)",
        data=table.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"{payload.get('source', 'era5')}_{payload.get('variable', 'data')}.csv",
        mime="text/csv",
        key="download_timeseries_csv",
    )


def _show_extremes(payload: dict[str, Any]) -> None:
    """Giá trị cao nhất/thấp nhất kèm thời điểm, tính bằng code (không để LLM đoán)."""
    unit = payload.get("unit", "")
    if unit == "°":  # hướng gió là góc vòng tròn, cực trị không có nghĩa
        return
    points = [p for p in payload.get("data", []) if p.get("value") is not None]
    if not points:
        return
    date_only = payload.get("frequency") in ("daily", "monthly", "yearly")

    def when(point: dict[str, Any]) -> str:
        try:
            stamp = datetime.fromisoformat(str(point["time"]))
        except (KeyError, ValueError):
            return str(point.get("time"))
        return stamp.strftime("%d/%m/%Y" if date_only else "%d/%m/%Y %H:%M")

    highest = max(points, key=lambda p: p["value"])
    lowest = min(points, key=lambda p: p["value"])
    if payload.get("frequency") in (None, "hourly"):
        high_label, low_label = "Cao nhất", "Thấp nhất"
    else:  # dữ liệu đã tổng hợp: đây là kỳ có giá trị tổng hợp cao/thấp nhất, không phải cực trị giờ
        high_label, low_label = "Kỳ có giá trị cao nhất", "Kỳ có giá trị thấp nhất"
    st.markdown(
        f"**{high_label}:** {_format_value(highest['value'], unit)} tại {when(highest)} · "
        f"**{low_label}:** {_format_value(lowest['value'], unit)} tại {when(lowest)}"
    )


def _render_statistics(client: DashboardApiClient, params: dict[str, Any]) -> None:
    st.subheader("Thống kê")
    if params.get("frequency", "hourly") != "hourly":
        st.caption("Thống kê tổng quan vẫn tính trên các quan sát giờ; lựa chọn chu kỳ áp dụng cho biểu đồ Chuỗi thời gian.")
    _run_request(
        "thống kê",
        lambda: _load_cached(client, "statistics", params),
        lambda payload: _show_statistics(payload),
    )
    if st.button("Làm mới thống kê", key="refresh_statistics"):
        _cached_get.clear()
        st.rerun()


def _show_statistics(payload: dict[str, Any]) -> None:
    unit = payload.get("unit", "")
    cols = st.columns(5)
    labels = [
        ("Trung bình", "mean"),
        ("Nhỏ nhất", "min"),
        ("Lớn nhất", "max"),
        ("Độ lệch chuẩn", "std"),
        ("Số quan sát", "count"),
    ]
    if unit == "°":
        labels = [("Trung bình vòng tròn", "mean"), ("Độ phân tán góc", "std"), ("Số quan sát", "count")]
        st.caption("Dấu — nghĩa là không xác định được hướng trung bình cho tập hướng đối nhau.")
    for column, (label, key) in zip(cols, labels):
        value = _format_value(payload.get(key), unit) if key != "count" else _format_number(payload.get(key), 0)
        column.metric(label, value)


def _render_trend(client: DashboardApiClient, params: dict[str, Any]) -> None:
    st.subheader("Xu hướng")
    if params["variable"] == "wind_direction":
        st.info("Không áp dụng hồi quy tuyến tính trực tiếp cho góc hướng gió.")
        return
    _run_request(
        "xu hướng",
        lambda: _load_cached(client, "trend", params),
        lambda payload: _show_trend(payload),
    )
    if st.button("Làm mới xu hướng", key="refresh_trend"):
        _cached_get.clear()
        st.rerun()


def _show_trend(payload: dict[str, Any]) -> None:
    col1, col2, col3 = st.columns(3)
    col1.metric("Chiều hướng", _translate_direction(payload.get("direction")))
    col2.metric("Hệ số dốc", _format_number(payload.get("slope"), 4))
    col3.metric("Số quan sát", _format_number(payload.get("count"), 0))
    st.caption("Hệ số dốc được tính ở backend và không tự đổi nhãn thành theo ngày/tháng/năm.")


def _render_heatmap(client: DashboardApiClient, params: dict[str, Any]) -> None:
    st.subheader("Bản đồ nhiệt theo tháng và năm")
    _run_request(
        "bản đồ nhiệt",
        lambda: _load_cached(client, "heatmap", params),
        lambda payload: _show_heatmap(payload),
    )
    if st.button("Làm mới bản đồ nhiệt", key="refresh_heatmap"):
        _cached_get.clear()
        st.rerun()


def _show_heatmap(payload: dict[str, Any]) -> None:
    if not payload.get("data"):
        st.info("Không có ô dữ liệu nào cho bản đồ nhiệt.")
        return
    st.caption("Phép tổng hợp: " + payload.get("aggregation", "mean")
               + ". Mỗi ô chỉ bao phủ khoảng ngày đã chọn, có thể chưa trọn tháng.")
    st.plotly_chart(heatmap_figure(payload), use_container_width=True)


def _render_map(client: DashboardApiClient, params: dict[str, Any]) -> None:
    st.subheader("Bản đồ")
    if params["variable"] == "wind_direction":
        st.info("Xem hướng gió theo giờ trong tab Chuỗi thời gian.")
        return
    st.caption("Máy chủ API hiện trả dữ liệu bản đồ theo một điểm tọa độ đã chọn.")
    _run_request(
        "bản đồ",
        lambda: _load_cached(client, "map", params),
        lambda payload: _show_map(payload),
    )
    if st.button("Làm mới bản đồ", key="refresh_map"):
        _cached_get.clear()
        st.rerun()


def _show_map(payload: dict[str, Any]) -> None:
    if not payload.get("data"):
        st.info("Không có điểm bản đồ nào được trả về.")
        return
    st.plotly_chart(map_figure(payload), use_container_width=True)
    st.dataframe(
        pd.DataFrame(payload["data"]).rename(
            columns={"latitude": "Vĩ độ", "longitude": "Kinh độ", "value": "Giá trị"}
        ),
        use_container_width=True,
    )


def _download_payload(params: dict[str, Any]) -> dict[str, Any]:
    latitude = float(params["latitude"])
    longitude = float(params["longitude"])
    return {
        "variable": params["variable"],
        "start_date": _utc_date(params["start"]),
        "end_date": _utc_date(params["end"]),
        "area": {
            "north": min(90.0, latitude + 0.25),
            "west": max(-180.0, longitude - 0.25),
            "south": max(-90.0, latitude - 0.25),
            "east": min(180.0, longitude + 0.25),
        },
        "frequency": "hourly",
    }


def _utc_date(value: str) -> str:
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC).date().isoformat()


def _run_request(
    label: str,
    request: Callable[[], dict[str, Any]],
    render: Callable[[dict[str, Any]], None],
) -> None:
    try:
        with st.spinner(f"Đang tải {label.lower()}..."):
            payload = request()
    except DashboardApiError as exc:
        st.error(str(exc))
        return
    if payload.get("query_mode") == "db_only":
        st.caption(f"Nguồn: {payload['source']} · Dataset: {payload['dataset']} · Chỉ đọc SQLite")
        if payload.get("grid_location"):
            grid = payload["grid_location"]
            st.caption(f"Điểm lưới thực: {grid['latitude']}, {grid['longitude']}")
        if not payload["complete"]:
            st.warning(f"Chưa đủ dữ liệu: {payload['available_count']}/{payload['expected_count']} giờ "
                       f"({payload['coverage']:.1%}). Kết quả chỉ dựa trên phần đã lưu; không tải CDS tự động.")
            with st.expander("Khoảng giờ nguồn còn thiếu (UTC+7)"):
                st.json(payload["missing_ranges"])
        if payload.get("undefined_count"):
            st.info(f"Có {payload['undefined_count']} giờ đủ dữ liệu gió nhưng hướng không xác định (lặng gió).")
        if payload.get("message"):
            st.info(payload["message"])
            return
    render(payload)


def _translate_direction(direction: Any) -> str:
    translations = {
        "increasing": "Tăng",
        "decreasing": "Giảm",
        "stable": "Ổn định",
    }
    return translations.get(str(direction), str(direction))


def _translate_status(status: Any) -> str:
    translations = {
        "queued": "Đang chờ",
        "running": "Đang chạy",
        "completed": "Hoàn tất",
        "failed": "Thất bại",
        "cancelled": "Đã hủy",
        "up_to_date": "Đã cập nhật đủ",
    }
    return translations.get(str(status), str(status))


def _format_number(value: Any, decimals: int = 2) -> str:
    if value is None:
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if decimals <= 0:
        return str(int(round(number)))
    return f"{number:.{decimals}f}".rstrip("0").rstrip(".")


def _format_value(value: Any, unit: str = "", decimals: int = 2) -> str:
    formatted = _format_number(value, decimals)
    return f"{formatted} {unit}".strip()


if __name__ == "__main__":
    main()
