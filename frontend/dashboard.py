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
    st.title("Bảng điều khiển khí hậu ERA5")
    st.caption("Bảng điều khiển phân tích dữ liệu khí hậu kết nối qua FastAPI")

    client = build_client()
    params = render_filter_controls()
    baseline_params = render_baseline_controls()
    comparison_params = render_comparison_controls()

    with st.sidebar:
        st.header("Máy chủ API")
        st.code(client.base_url)
        if st.button("Kiểm tra máy chủ"):
            _run_request("trạng thái máy chủ", client.health, lambda _: st.success("Đang hoạt động"))

    tabs = st.tabs(
        [
            "Tổng quan",
            "Chuỗi thời gian",
            "Không gian",
            "Dữ liệu",
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
    st.subheader("Tổng quan")
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
    st.subheader("Chuỗi thời gian")
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
    st.plotly_chart(timeseries_figure(payload), use_container_width=True)
    st.dataframe(
        pd.DataFrame(payload["data"]).rename(columns={"time": "Thời gian", "value": "Giá trị"}),
        use_container_width=True,
    )


def _render_statistics(client: DashboardApiClient, params: dict[str, Any]) -> None:
    st.subheader("Thống kê")
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
    for column, (label, key) in zip(cols, labels):
        column.metric(label, f"{payload.get(key)} {unit}" if key != "count" else payload.get(key))


def _render_trend(client: DashboardApiClient, params: dict[str, Any]) -> None:
    st.subheader("Xu hướng")
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
    col2.metric("Hệ số dốc", payload.get("slope"))
    col3.metric("Số quan sát", payload.get("count"))
    st.caption("Hệ số dốc được tính ở backend và không tự đổi nhãn thành theo ngày/tháng/năm.")


def _render_anomaly(
    client: DashboardApiClient,
    params: dict[str, Any],
    baseline_params: dict[str, str],
) -> None:
    st.subheader("Độ lệch so với mốc nền")
    request_params = {**params, **baseline_params}
    _run_request(
        "độ lệch",
        lambda: _load_cached(client, "anomaly", request_params),
        lambda payload: _show_anomaly(payload),
    )
    if st.button("Làm mới độ lệch", key="refresh_anomaly"):
        _cached_get.clear()
        st.rerun()


def _show_anomaly(payload: dict[str, Any]) -> None:
    st.metric("Trung bình mốc nền", f"{payload.get('baseline_mean')} {payload.get('unit', '')}")
    if not payload.get("data"):
        st.info("Không có giá trị độ lệch nào được trả về.")
        return
    st.plotly_chart(anomaly_figure(payload), use_container_width=True)
    st.dataframe(
        pd.DataFrame(payload["data"]).rename(
            columns={"time": "Thời gian", "value": "Giá trị", "anomaly": "Độ lệch"}
        ),
        use_container_width=True,
    )


def _render_comparison(
    client: DashboardApiClient,
    params: dict[str, Any],
    comparison_params: dict[str, str],
) -> None:
    st.subheader("So sánh giai đoạn")
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
    st.metric("Trung bình giai đoạn A - giai đoạn B", f"{payload.get('difference')} {payload.get('unit', '')}")
    st.plotly_chart(comparison_figure(payload), use_container_width=True)


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
    st.plotly_chart(heatmap_figure(payload), use_container_width=True)


def _render_map(client: DashboardApiClient, params: dict[str, Any]) -> None:
    st.subheader("Bản đồ")
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


def _render_downloads(client: DashboardApiClient, params: dict[str, Any]) -> None:
    st.subheader("Tác vụ tải dữ liệu")
    st.caption("Tạo tác vụ tải dữ liệu ở backend và hiển thị trạng thái xử lý.")

    if st.button("Tạo tác vụ tải dữ liệu", key="create_download"):
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


if __name__ == "__main__":
    main()
