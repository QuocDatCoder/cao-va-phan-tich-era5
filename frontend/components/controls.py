from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import streamlit as st


VARIABLES = {
    "Nhiệt độ": "temperature",
    "Nhiệt độ 2m": "2m_temperature",
    "Lượng mưa": "precipitation",
    "Tổng lượng mưa": "total_precipitation",
    "Điểm sương 2m": "dewpoint",
    "Tốc độ gió 10m": "wind_speed",
    "Hướng gió 10m": "wind_direction",
    "Thành phần gió u (Đông–Tây)": "wind_u",
    "Thành phần gió v (Bắc–Nam)": "wind_v",
    "Bức xạ mặt trời": "solar_radiation",
}

VIETNAM_LOCATIONS: dict[str, tuple[float, float] | None] = {
    "TP. Hồ Chí Minh": (10.8231, 106.6297),
    "Hà Nội": (21.0285, 105.8542),
    "Đà Nẵng": (16.0544, 108.2022),
    "Cần Thơ": (10.0452, 105.7469),
    "Hải Phòng": (20.8449, 106.6881),
    "Sa Pa (Lào Cai)": (22.3364, 103.8438),
    "Đà Lạt (Lâm Đồng)": (11.9404, 108.4583),
    "Nha Trang (Khánh Hòa)": (12.2388, 109.1967),
    "Huế (Thừa Thiên Huế)": (16.4637, 107.5909),
    "Quần đảo Trường Sa": (8.6444, 111.9189),
    "Tùy chỉnh tọa độ": None,
}

VIETNAM_TIMEZONE = timezone(timedelta(hours=7))


def local_date_range(start_date: date, end_date: date) -> tuple[str, str]:
    """Include every hourly observation in the selected Vietnam calendar days."""
    return (
        datetime.combine(start_date, time.min, tzinfo=VIETNAM_TIMEZONE).isoformat(),
        datetime.combine(end_date, time(hour=23), tzinfo=VIETNAM_TIMEZONE).isoformat(),
    )


def render_filter_controls(follow_latest: bool = False) -> dict[str, Any]:
    default_start = date(2025, 1, 1)
    default_end = date(2025, 1, 2)

    with st.sidebar:
        st.header("Truy vấn Khí hậu")
        st.caption("Múi giờ quy chuẩn: Việt Nam (UTC+7).")

        # 1. Biến khí hậu (selectbox[0])
        variable_label = st.selectbox("Biến khí hậu", list(VARIABLES), index=0)

        # 2. Chu kỳ hiển thị (selectbox[1])
        frequencies = {
            "Theo giờ": "hourly",
            "Theo ngày": "daily",
            "Theo tháng": "monthly",
            "Theo năm": "yearly",
        }
        frequency = frequencies[st.selectbox("Chu kỳ hiển thị", list(frequencies))]

        # 3. Phép tổng hợp (selectbox[2])
        methods = {
            "Tự động theo biến": "auto",
            "Trung bình": "mean",
            "Nhỏ nhất": "min",
            "Lớn nhất": "max",
        }
        variable = VARIABLES[variable_label]
        if variable in {"precipitation", "total_precipitation", "solar_radiation"}:
            methods["Tổng"] = "sum"
        if variable == "wind_direction":
            methods = {"Trung bình vòng tròn": "auto"}
        aggregation = methods[
            st.selectbox("Phép tổng hợp", list(methods), disabled=frequency == "hourly")
        ]

        # 4. Khoảng thời gian
        st.subheader("Khoảng thời gian")
        start_date = st.date_input("Ngày bắt đầu", value=default_start, disabled=follow_latest)
        end_date = st.date_input("Ngày kết thúc", value=default_end, disabled=follow_latest)

        # 5. Vị trí & Tọa độ quan trắc
        st.subheader("Tọa độ quan sát")
        preset_choice = st.selectbox(
            "Điểm quan trắc (Việt Nam)",
            list(VIETNAM_LOCATIONS.keys()),
            index=0,
        )

        default_lat, default_lon = 10.8231, 106.6297
        if VIETNAM_LOCATIONS[preset_choice] is not None:
            default_lat, default_lon = VIETNAM_LOCATIONS[preset_choice]  # type: ignore

        # If user switched preset, update active session state coordinates
        if st.session_state.get("active_preset") != preset_choice:
            st.session_state["active_preset"] = preset_choice
            st.session_state["active_lat"] = default_lat
            st.session_state["active_lon"] = default_lon
            st.session_state["selected_location_name"] = preset_choice

        col_lat, col_lon = st.columns(2)
        with col_lat:
            latitude = st.number_input(
                "Vĩ độ (°N)",
                min_value=-90.0,
                max_value=90.0,
                value=float(st.session_state.get("active_lat", default_lat)),
                format="%.4f",
            )
            st.session_state["active_lat"] = latitude
        with col_lon:
            longitude = st.number_input(
                "Kinh độ (°E)",
                min_value=-180.0,
                max_value=180.0,
                value=float(st.session_state.get("active_lon", default_lon)),
                format="%.4f",
            )
            st.session_state["active_lon"] = longitude

    start, end = local_date_range(start_date, end_date)
    return {
        "variable": VARIABLES[variable_label],
        "latitude": latitude,
        "longitude": longitude,
        "start": start,
        "end": end,
        "frequency": frequency,
        "aggregation": aggregation if frequency != "hourly" else "auto",
    }


def render_baseline_controls() -> dict[str, str]:
    with st.sidebar:
        with st.expander("Mốc nền phân tích (Baseline)", expanded=False):
            st.caption("Khoảng thời gian dùng làm mốc quy chiếu tính độ lệch (Anomaly).")
            baseline_start = st.date_input("Bắt đầu mốc nền", value=date(2024, 12, 30))
            baseline_end = st.date_input("Kết thúc mốc nền", value=date(2024, 12, 31))
    start, end = local_date_range(baseline_start, baseline_end)
    return {"baseline_start": start, "baseline_end": end}


def render_comparison_controls() -> dict[str, str]:
    with st.sidebar:
        with st.expander("Giai đoạn so sánh đối chứng", expanded=False):
            st.caption("Khoảng thời gian (Giai đoạn B) để so sánh với khoảng hiện tại (Giai đoạn A).")
            period_b_start = st.date_input("Bắt đầu giai đoạn B", value=date(2025, 1, 3))
            period_b_end = st.date_input("Kết thúc giai đoạn B", value=date(2025, 1, 4))
    start, end = local_date_range(period_b_start, period_b_end)
    return {"period_b_start": start, "period_b_end": end}
