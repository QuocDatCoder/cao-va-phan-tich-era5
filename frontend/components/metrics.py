from typing import Any
import streamlit as st


def format_number(value: Any, decimals: int = 2) -> str:
    if value is None:
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if decimals <= 0:
        return str(int(round(number)))
    return f"{number:.{decimals}f}".rstrip("0").rstrip(".")


def format_value(value: Any, unit: str = "", decimals: int = 2) -> str:
    formatted = format_number(value, decimals)
    if not unit:
        return formatted
    return f"{formatted} {unit}".strip()


def render_overview_kpis(payload: dict[str, Any]) -> None:
    """Render meteorological statistics using polished metric cards."""
    unit = payload.get("unit", "")
    if unit == "°":
        cols = st.columns(3)
        labels = [
            ("Trung bình vòng tròn", "mean"),
            ("Độ phân tán góc", "std"),
            ("Số quan sát", "count"),
        ]
        st.caption("Dấu — nghĩa là không xác định được hướng trung bình cho tập hướng đối nhau.")
    else:
        cols = st.columns(5)
        labels = [
            ("Trung bình (Mean)", "mean"),
            ("Cực tiểu (Min)", "min"),
            ("Cực đại (Max)", "max"),
            ("Độ lệch chuẩn (Std)", "std"),
            ("Số quan sát (Count)", "count"),
        ]

    for col, (label, key) in zip(cols, labels):
        val = payload.get(key)
        if key == "count":
            display_val = format_number(val, 0)
        else:
            display_val = format_value(val, unit)
        col.metric(label, display_val)

