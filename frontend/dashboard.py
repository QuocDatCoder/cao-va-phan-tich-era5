import os
from typing import Any, Callable

import pandas as pd
import streamlit as st

from frontend.api_client import DEFAULT_TIMEOUT_SECONDS, DashboardApiClient, DashboardApiError
from frontend.components.charts import (
    anomaly_figure,
    comparison_figure,
    heatmap_figure,
    map_figure,
    timeseries_figure,
)
from frontend.components.controls import (
    render_baseline_controls,
    render_comparison_controls,
    render_filter_controls,
)


def build_client() -> DashboardApiClient:
    return DashboardApiClient(
        base_url=os.getenv("API_BASE_URL", "http://localhost:8000"),
        timeout_seconds=float(os.getenv("API_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))),
    )


def main() -> None:
    st.set_page_config(page_title="Bảng điều khiển khí hậu ERA5", layout="wide")
    st.title("Phân tích dữ liệu khí hậu ERA5")

    client = build_client()
    params = render_filter_controls()
    baseline_params = render_baseline_controls()
    comparison_params = render_comparison_controls()

    with st.sidebar:
        st.header("Máy chủ API")
        st.code(client.base_url)
        if st.button("Kiểm tra máy chủ"):
            _run_request("trạng thái máy chủ", client.health, lambda _: st.success("Đang hoạt động"))

    _render_current_selection(params, client.base_url)

    tabs = st.tabs(
        [
            "Tổng quan",
            "Diễn biến theo thời gian",
            "Vị trí trên bản đồ",
            "Tải thêm dữ liệu",
        ]
    )

    with tabs[0]:
        _render_overview(client, params, comparison_params)
    with tabs[1]:
        _render_temporal_analysis(client, params, baseline_params)
    with tabs[2]:
        _render_map(client, params)
    with tabs[3]:
        _render_downloads(client, params)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_get(base_url: str, timeout_seconds: float, endpoint: str, params_key: tuple) -> dict[str, Any]:
    client = DashboardApiClient(base_url=base_url, timeout_seconds=timeout_seconds)
    params = dict(params_key)
    if endpoint == "timeseries":
        return client.get_timeseries(params)
    if endpoint == "statistics":
        return client.get_statistics(params)
    if endpoint == "trend":
        return client.get_trend(params)
    if endpoint == "anomaly":
        return client.get_anomaly(params)
    if endpoint == "compare":
        return client.get_compare(params)
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
    )


def _render_overview(
    client: DashboardApiClient,
    params: dict[str, Any],
    comparison_params: dict[str, str],
) -> None:
    st.subheader("Hiểu nhanh")
    st.caption("Phần này trả lời nhanh: giá trị cao/thấp ra sao, xu hướng thế nào, và khác gì so với giai đoạn khác.")
    left, right = st.columns([3, 2])
    with left:
        _render_statistics(client, params)
    with right:
        _render_trend(client, params)
    st.divider()
    _render_comparison(client, params, comparison_params)


def _render_temporal_analysis(
    client: DashboardApiClient,
    params: dict[str, Any],
    baseline_params: dict[str, str],
) -> None:
    st.subheader("Diễn biến theo thời gian")
    st.caption("Xem giá trị thay đổi từng giờ, lệch bao nhiêu so với mốc nền, và mẫu theo tháng/năm.")
    _render_timeseries(client, params, show_title=False)
    st.divider()
    _render_anomaly(client, params, baseline_params)
    st.divider()
    _render_heatmap(client, params)


def _render_timeseries(
    client: DashboardApiClient,
    params: dict[str, Any],
    show_title: bool = True,
) -> None:
    if show_title:
        st.subheader("Giá trị thay đổi theo từng giờ")
    else:
        st.markdown("#### Giá trị thay đổi theo từng giờ")
    st.caption("Biểu đồ này cho thấy biến khí hậu đã tăng, giảm hoặc dao động như thế nào trong khoảng thời gian đã chọn.")
    _run_request(
        "diễn biến theo thời gian",
        lambda: _load_cached(client, "timeseries", params),
        lambda payload: _show_timeseries(payload),
    )
    if st.button("Làm mới diễn biến theo thời gian", key="refresh_timeseries"):
        _cached_get.clear()
        st.rerun()


def _show_timeseries(payload: dict[str, Any]) -> None:
    if not payload.get("data"):
        st.info("Không có quan sát nào cho bộ lọc đã chọn.")
        return
    _show_unit_note(payload)
    _show_short_period_note(payload)
    st.plotly_chart(timeseries_figure(payload), use_container_width=True)
    st.dataframe(
        pd.DataFrame(payload["data"]).rename(columns={"time": "Thời gian", "value": "Giá trị đo được"}),
        use_container_width=True,
    )


def _render_statistics(client: DashboardApiClient, params: dict[str, Any]) -> None:
    st.subheader("Các giá trị chính")
    st.caption("Tóm tắt giá trị trung bình, thấp nhất, cao nhất và mức dao động trong khoảng thời gian đã chọn.")
    _run_request(
        "các con số chính",
        lambda: _load_cached(client, "statistics", params),
        lambda payload: _show_statistics(payload),
    )
    if st.button("Làm mới các con số chính", key="refresh_statistics"):
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
        ("Số giờ có dữ liệu", "count"),
    ]
    for column, (label, key) in zip(cols, labels):
        value = _format_value(payload.get(key), unit) if key != "count" else _format_number(payload.get(key), 0)
        column.metric(label, value)


def _render_trend(client: DashboardApiClient, params: dict[str, Any]) -> None:
    st.subheader("Xu hướng tổng quát")
    st.caption("Cho biết dữ liệu trong giai đoạn này nghiêng về tăng, giảm hay gần như ổn định.")
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
    col2.metric("Mức thay đổi ước tính", _format_number(payload.get("slope"), 4))
    col3.metric("Số giờ có dữ liệu", _format_number(payload.get("count"), 0))
    st.caption("Mức thay đổi ước tính được backend tính từ chuỗi thời gian, dùng để đọc nhanh chiều hướng dữ liệu.")


def _render_anomaly(
    client: DashboardApiClient,
    params: dict[str, Any],
    baseline_params: dict[str, str],
) -> None:
    st.subheader("Lệch bao nhiêu so với mốc nền")
    st.caption("Mỗi cột cho biết giá trị tại thời điểm đó cao hơn hoặc thấp hơn mức trung bình của giai đoạn nền.")
    request_params = {**params, **baseline_params}
    _run_request(
        "độ lệch so với mốc nền",
        lambda: _load_cached(client, "anomaly", request_params),
        lambda payload: _show_anomaly(payload),
    )
    if st.button("Làm mới độ lệch so với mốc nền", key="refresh_anomaly"):
        _cached_get.clear()
        st.rerun()


def _show_anomaly(payload: dict[str, Any]) -> None:
    st.metric("Giá trị trung bình của mốc nền", _format_value(payload.get("baseline_mean"), payload.get("unit", "")))
    if not payload.get("data"):
        st.info("Không có giá trị độ lệch nào được trả về.")
        return
    st.plotly_chart(anomaly_figure(payload), use_container_width=True)
    st.dataframe(
        pd.DataFrame(payload["data"]).rename(
            columns={"time": "Thời gian", "value": "Giá trị đo được", "anomaly": "Độ lệch so với mốc nền"}
        ),
        use_container_width=True,
    )


def _render_comparison(
    client: DashboardApiClient,
    params: dict[str, Any],
    comparison_params: dict[str, str],
) -> None:
    st.subheader("So sánh hai giai đoạn")
    st.caption("So sánh giai đoạn đang chọn với giai đoạn B để xem trung bình khác nhau bao nhiêu.")
    request_params = {
        "variable": params["variable"],
        "latitude": params["latitude"],
        "longitude": params["longitude"],
        "period_a_start": params["start"],
        "period_a_end": params["end"],
        **comparison_params,
    }
    _run_request(
        "so sánh",
        lambda: _load_cached(client, "compare", request_params),
        lambda payload: _show_comparison(payload),
    )
    if st.button("Làm mới so sánh", key="refresh_compare"):
        _cached_get.clear()
        st.rerun()


def _show_comparison(payload: dict[str, Any]) -> None:
    st.metric(
        "Chênh lệch trung bình",
        _format_value(payload.get("difference"), payload.get("unit", ""), decimals=3),
    )
    st.plotly_chart(comparison_figure(payload), use_container_width=True)


def _render_heatmap(client: DashboardApiClient, params: dict[str, Any]) -> None:
    st.subheader("Mẫu theo tháng và năm")
    st.caption("Gom dữ liệu theo tháng/năm để nhìn nhanh mùa vụ hoặc bất thường dài hạn. Nếu dữ liệu ít ngày, biểu đồ này chỉ mang tính minh họa.")
    _run_request(
        "mẫu theo tháng và năm",
        lambda: _load_cached(client, "heatmap", params),
        lambda payload: _show_heatmap(payload),
    )
    if st.button("Làm mới mẫu theo tháng và năm", key="refresh_heatmap"):
        _cached_get.clear()
        st.rerun()


def _show_heatmap(payload: dict[str, Any]) -> None:
    if not payload.get("data"):
        st.info("Không có ô dữ liệu nào cho bản đồ nhiệt.")
        return
    st.plotly_chart(heatmap_figure(payload), use_container_width=True)


def _render_map(client: DashboardApiClient, params: dict[str, Any]) -> None:
    st.subheader("Vị trí đang phân tích")
    st.caption("Bản đồ hiển thị giá trị tại tọa độ đã chọn. Đây chưa phải bản đồ phủ kín toàn vùng.")
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
            columns={"latitude": "Vĩ độ", "longitude": "Kinh độ", "value": "Giá trị đo được"}
        ),
        use_container_width=True,
    )


def _render_downloads(client: DashboardApiClient, params: dict[str, Any]) -> None:
    st.subheader("Tải dữ liệu ERA5 cho lựa chọn hiện tại")
    st.caption("Backend sẽ tải NetCDF theo biến, ngày và vùng nhỏ quanh tọa độ đang chọn, rồi lưu trạng thái xử lý.")

    if st.button("Tải dữ liệu ERA5 cho vùng quanh tọa độ này", key="create_download"):
        payload = _download_payload(params)
        _run_request(
            "tác vụ tải dữ liệu",
            lambda: client.create_download(payload),
            lambda response: _remember_job(response),
        )

    job_id = st.session_state.get("download_job_id")
    if job_id:
        st.info(f"Tác vụ hiện tại: {job_id}")
        if st.button("Làm mới trạng thái tác vụ", key="refresh_job"):
            _run_request(
                "trạng thái tác vụ",
                lambda: client.get_job(job_id),
                lambda payload: _show_job_status(payload),
            )


def _download_payload(params: dict[str, Any]) -> dict[str, Any]:
    latitude = float(params["latitude"])
    longitude = float(params["longitude"])
    return {
        "variable": params["variable"],
        "start_date": str(params["start"])[:10],
        "end_date": str(params["end"])[:10],
        "area": {
            "north": min(90.0, latitude + 0.25),
            "west": max(-180.0, longitude - 0.25),
            "south": max(-90.0, latitude - 0.25),
            "east": min(180.0, longitude + 0.25),
        },
        "frequency": "hourly",
    }


def _remember_job(payload: dict[str, Any]) -> None:
    st.session_state["download_job_id"] = payload.get("job_id")
    st.success(f"Đã đưa tác vụ vào hàng đợi: {payload.get('job_id')}")


def _show_job_status(payload: dict[str, Any]) -> None:
    status = payload.get("status")
    if status == "completed":
        st.success("Tác vụ đã hoàn tất")
    elif status == "failed":
        st.error(payload.get("error") or "Tác vụ thất bại")
    else:
        st.info(f"Trạng thái tác vụ: {_translate_status(status)}")
    st.dataframe(_job_status_frame(payload), use_container_width=True, hide_index=True)


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


def _job_status_frame(payload: dict[str, Any]) -> pd.DataFrame:
    rows = [
        {"Thông tin": "Mã tác vụ", "Giá trị": payload.get("job_id")},
        {"Thông tin": "Biến khí hậu", "Giá trị": payload.get("variable")},
        {"Thông tin": "Ngày bắt đầu", "Giá trị": payload.get("start_date")},
        {"Thông tin": "Ngày kết thúc", "Giá trị": payload.get("end_date")},
        {"Thông tin": "Trạng thái", "Giá trị": _translate_status(payload.get("status"))},
        {"Thông tin": "Tiến độ", "Giá trị": f"{payload.get('progress')}%"},
    ]
    if payload.get("file_path"):
        rows.append({"Thông tin": "Đường dẫn tệp", "Giá trị": payload.get("file_path")})
    if payload.get("error"):
        rows.append({"Thông tin": "Lỗi", "Giá trị": payload.get("error")})
    return pd.DataFrame(rows)


def _render_current_selection(params: dict[str, Any], base_url: str) -> None:
    st.info(
        "Đang phân tích "
        f"{_variable_label(params.get('variable'))} tại "
        f"{_format_number(params.get('latitude'), 4)}, {_format_number(params.get('longitude'), 4)} "
        f"trong giai đoạn {_date_only(params.get('start'))} đến {_date_only(params.get('end'))}. "
        f"Dữ liệu được lấy qua API: {base_url}."
    )


def _variable_label(variable: Any) -> str:
    labels = {
        "temperature": "nhiệt độ 2m",
        "2m_temperature": "nhiệt độ 2m",
        "dewpoint": "điểm sương 2m",
        "2m_dewpoint_temperature": "điểm sương 2m",
        "precipitation": "lượng mưa",
        "total_precipitation": "tổng lượng mưa",
    }
    return labels.get(str(variable), str(variable))


def _date_only(value: Any) -> str:
    return str(value)[:10] if value is not None else "-"


def _show_unit_note(payload: dict[str, Any]) -> None:
    if payload.get("unit") == "K":
        st.caption("ERA5 đang trả nhiệt độ theo Kelvin (K); dashboard hiện giữ nguyên đơn vị từ backend.")


def _show_short_period_note(payload: dict[str, Any]) -> None:
    data = payload.get("data") or []
    if len(data) < 72:
        st.info("Khoảng dữ liệu đang khá ngắn, nên xu hướng chỉ nên đọc như mô tả nhanh, chưa phải kết luận khí hậu dài hạn.")


if __name__ == "__main__":
    main()
