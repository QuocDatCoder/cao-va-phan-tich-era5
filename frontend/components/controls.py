from datetime import date, datetime, time
from typing import Any

import streamlit as st


VARIABLES = {
    "Temperature": "temperature",
    "2m Temperature": "2m_temperature",
    "Precipitation": "precipitation",
    "Total Precipitation": "total_precipitation",
}


def render_filter_controls() -> dict[str, Any]:
    default_start = date(2025, 1, 1)
    default_end = date(2025, 1, 2)

    with st.sidebar:
        st.header("Query")
        variable_label = st.selectbox("Variable", list(VARIABLES), index=0)
        start_date = st.date_input("Start date", value=default_start)
        end_date = st.date_input("End date", value=default_end)
        latitude = st.number_input("Latitude", min_value=-90.0, max_value=90.0, value=10.8231)
        longitude = st.number_input("Longitude", min_value=-180.0, max_value=180.0, value=106.6297)

    start = datetime.combine(start_date, time.min)
    end = datetime.combine(end_date, time(hour=23))
    return {
        "variable": VARIABLES[variable_label],
        "latitude": latitude,
        "longitude": longitude,
        "start": start.isoformat(),
        "end": end.isoformat(),
    }


def render_baseline_controls() -> dict[str, str]:
    with st.sidebar:
        st.header("Baseline")
        baseline_start = st.date_input("Baseline start", value=date(2024, 12, 30))
        baseline_end = st.date_input("Baseline end", value=date(2024, 12, 31))
    return {
        "baseline_start": datetime.combine(baseline_start, time.min).isoformat(),
        "baseline_end": datetime.combine(baseline_end, time(hour=23)).isoformat(),
    }


def render_comparison_controls() -> dict[str, str]:
    with st.sidebar:
        st.header("Comparison")
        period_b_start = st.date_input("Period B start", value=date(2025, 1, 3))
        period_b_end = st.date_input("Period B end", value=date(2025, 1, 4))
    return {
        "period_b_start": datetime.combine(period_b_start, time.min).isoformat(),
        "period_b_end": datetime.combine(period_b_end, time(hour=23)).isoformat(),
    }
