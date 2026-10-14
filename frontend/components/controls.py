from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import streamlit as st


VARIABLES = {
    "Nhiệt độ": "temperature",
    "Nhiệt độ 2m": "2m_temperature",
    "Lượng mưa": "precipitation",
    "Tổng lượng mưa": "total_precipitation",
}

VIETNAM_TIMEZONE = timezone(timedelta(hours=7))


def local_date_range(start_date: date, end_date: date) -> tuple[str, str]:
    """Include every hourly observation in the selected Vietnam calendar days."""
    return (
        datetime.combine(start_date, time.min, tzinfo=VIETNAM_TIMEZONE).isoformat(),
        datetime.combine(end_date, time(hour=23), tzinfo=VIETNAM_TIMEZONE).isoformat(),
    )


def render_filter_controls() -> dict[str, Any]:
    default_start = date(2025, 1, 1)
    default_end = date(2025, 1, 2)

    with st.sidebar:
        st.header("Truy vấn")
        st.caption("Các ngày được chọn theo giờ Việt Nam (UTC+7).")
        variable_label = st.selectbox("Biến khí hậu", list(VARIABLES), index=0)
        start_date = st.date_input("Ngày bắt đầu", value=default_start)
        end_date = st.date_input("Ngày kết thúc", value=default_end)
        latitude = st.number_input("Vĩ độ", min_value=-90.0, max_value=90.0, value=10.8231)
        longitude = st.number_input("Kinh độ", min_value=-180.0, max_value=180.0, value=106.6297)

    start, end = local_date_range(start_date, end_date)
    return {
        "variable": VARIABLES[variable_label],
        "latitude": latitude,
        "longitude": longitude,
        "start": start,
        "end": end,
    }


def render_baseline_controls() -> dict[str, str]:
    with st.sidebar:
        st.header("Mốc nền")
        baseline_start = st.date_input("Ngày bắt đầu mốc nền", value=date(2024, 12, 30))
        baseline_end = st.date_input("Ngày kết thúc mốc nền", value=date(2024, 12, 31))
    start, end = local_date_range(baseline_start, baseline_end)
    return {"baseline_start": start, "baseline_end": end}


def render_comparison_controls() -> dict[str, str]:
    with st.sidebar:
        st.header("So sánh")
        period_b_start = st.date_input("Ngày bắt đầu giai đoạn B", value=date(2025, 1, 3))
        period_b_end = st.date_input("Ngày kết thúc giai đoạn B", value=date(2025, 1, 4))
    start, end = local_date_range(period_b_start, period_b_end)
    return {"period_b_start": start, "period_b_end": end}
