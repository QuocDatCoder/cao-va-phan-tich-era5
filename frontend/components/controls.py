from datetime import date, datetime, time, timedelta, timezone
from typing import Any
import json
import urllib.parse
import urllib.request

import streamlit as st


VARIABLES = {
    "Nhiệt độ": "temperature",
    "Lượng mưa": "precipitation",
    "Điểm sương": "dewpoint",
    "Tốc độ gió": "wind_speed",
    "Hướng gió": "wind_direction",
    "Bức xạ mặt trời": "solar_radiation",
}

VIETNAM_TIMEZONE = timezone(timedelta(hours=7))

DEFAULT_LATITUDE = 10.8231
DEFAULT_LONGITUDE = 106.6297
DEFAULT_PLACE = "TP. Hồ Chí Minh (mặc định)"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_places(query: str) -> list[dict[str, Any]]:
    """Tra tọa độ theo tên bằng Open-Meteo Geocoding. Lỗi mạng thì ném ngoại lệ (không bị cache)."""
    params = urllib.parse.urlencode({"name": query, "count": 8, "language": "vi", "format": "json"})
    with urllib.request.urlopen(f"{GEOCODING_URL}?{params}", timeout=8) as response:
        payload = json.load(response)
    places = []
    for item in payload.get("results") or []:
        latitude, longitude = item.get("latitude"), item.get("longitude")
        if latitude is None or longitude is None:
            continue
        parts = dict.fromkeys(p for p in (item.get("name"), item.get("admin1"), item.get("country")) if p)
        places.append({
            "label": f"{', '.join(parts)} ({latitude:.4f}, {longitude:.4f})",
            "latitude": float(latitude),
            "longitude": float(longitude),
        })
    return places


def search_places(query: str) -> list[dict[str, Any]] | None:
    """Danh sách địa điểm khớp tên; None nếu không gọi được dịch vụ tra cứu."""
    try:
        return _fetch_places(query)
    except (OSError, ValueError):
        return None


def _apply_search_result(result_key: str) -> None:
    label = st.session_state.get(result_key)
    place = st.session_state.get("_place_results", {}).get(label)
    if place:
        st.session_state["latitude_input"] = place["latitude"]
        st.session_state["longitude_input"] = place["longitude"]
        st.session_state["selected_place"] = label


def _mark_manual_coordinates() -> None:
    st.session_state["selected_place"] = "Tọa độ nhập tay"


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
        st.header("Truy vấn", anchor=False)
        st.caption("Các ngày được chọn theo giờ Việt Nam (UTC+7).")
        variable_label = st.selectbox("Biến khí hậu", list(VARIABLES), index=0)
        frequencies = {"Theo giờ": "hourly", "Theo ngày": "daily", "Theo tháng": "monthly", "Theo năm": "yearly"}
        frequency = frequencies[st.selectbox("Chu kỳ hiển thị", list(frequencies))]
        methods = {"Tự động theo biến": "auto", "Trung bình": "mean", "Nhỏ nhất": "min", "Lớn nhất": "max"}
        variable = VARIABLES[variable_label]
        if variable in {"precipitation", "total_precipitation", "solar_radiation"}:
            methods["Tổng"] = "sum"
        if variable == "wind_direction":
            methods = {"Trung bình vòng tròn": "auto"}
        aggregation = methods[st.selectbox("Phép tổng hợp", list(methods), disabled=frequency == "hourly")]
        start_date = st.date_input("Ngày bắt đầu", value=default_start, disabled=follow_latest, format="DD/MM/YYYY")
        end_date = st.date_input("Ngày kết thúc", value=default_end, disabled=follow_latest, format="DD/MM/YYYY")
        st.session_state.setdefault("latitude_input", DEFAULT_LATITUDE)
        st.session_state.setdefault("longitude_input", DEFAULT_LONGITUDE)
        st.session_state.setdefault("selected_place", DEFAULT_PLACE)
        query = st.text_input(
            "Nhập tên thành phố",
            key="location_query",
            placeholder="Ví dụ: Rome, Seoul, Bangkok",
            help="Gõ tên rồi nhấn Enter, vĩ độ và kinh độ sẽ tự điền. Tên được gửi tới dịch vụ "
                 "Open-Meteo Geocoding (cần Internet). Có thể bỏ qua và nhập tọa độ trực tiếp.",
        ).strip()
        if len(query) >= 2:
            with st.spinner("Đang tìm địa điểm..."):
                places = search_places(query)
            if places is None:
                st.caption("Không tra cứu được (lỗi mạng hoặc dịch vụ). Hãy nhập vĩ độ, kinh độ trực tiếp.")
            elif not places:
                st.caption("Không tìm thấy địa điểm phù hợp. Thử tên khác hoặc viết bằng tiếng Anh.")
            else:
                st.session_state["_place_results"] = {p["label"]: p for p in places}
                if st.session_state.get("_applied_query") != query.lower():
                    # Tên mới: dùng ngay kết quả đầu tiên, người dùng có thể chọn kết quả khác bên dưới.
                    st.session_state["latitude_input"] = places[0]["latitude"]
                    st.session_state["longitude_input"] = places[0]["longitude"]
                    st.session_state["selected_place"] = places[0]["label"]
                    st.session_state["_applied_query"] = query.lower()
                result_key = f"location_result::{query.lower()}"
                st.selectbox(
                    "Kết quả tìm kiếm",
                    list(st.session_state["_place_results"]),
                    key=result_key,
                    on_change=_apply_search_result,
                    args=(result_key,),
                )
        st.caption(f"Đang chọn: {st.session_state['selected_place']}")
        latitude = st.number_input(
            "Vĩ độ", min_value=-90.0, max_value=90.0, format="%.4f",
            key="latitude_input", on_change=_mark_manual_coordinates,
        )
        longitude = st.number_input(
            "Kinh độ", min_value=-180.0, max_value=180.0, format="%.4f",
            key="longitude_input", on_change=_mark_manual_coordinates,
        )

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