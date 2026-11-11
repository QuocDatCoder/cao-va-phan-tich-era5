from typing import Any
import streamlit as st


def render_station_header(
    params: dict[str, Any],
    base_url: str,
    api_online: bool = True,
    location_name: str | None = None,
) -> None:
    """Render a high-precision meteorological station telemetry header."""
    lat = params.get("latitude", 10.8231)
    lon = params.get("longitude", 106.6297)
    variable = params.get("variable", "temperature")
    frequency = params.get("frequency", "hourly")

    variable_labels = {
        "temperature": "Nhiệt độ (°C)",
        "2m_temperature": "Nhiệt độ 2m (°C)",
        "precipitation": "Lượng mưa (mm)",
        "total_precipitation": "Tổng lượng mưa (mm)",
        "dewpoint": "Điểm sương 2m (°C)",
        "wind_speed": "Tốc độ gió 10m (m/s)",
        "wind_direction": "Hướng gió 10m (°)",
        "wind_u": "Gió thành phần u (m/s)",
        "wind_v": "Gió thành phần v (m/s)",
        "solar_radiation": "Bức xạ mặt trời (MJ/m²)",
    }
    freq_labels = {
        "hourly": "Theo giờ",
        "daily": "Theo ngày",
        "monthly": "Theo tháng",
        "yearly": "Theo năm",
    }

    var_display = variable_labels.get(variable, variable)
    freq_display = freq_labels.get(frequency, frequency)

    loc_str = f"{lat:.2f}°N, {lon:.2f}°E"
    if location_name and location_name != "Tùy chỉnh tọa độ":
        loc_str = f"{location_name} ({loc_str})"

    status_class = "online" if api_online else "syncing"
    status_text = "API Sẵn sàng" if api_online else "Đang kiểm tra kết nối"

    st.markdown(
        f"""
        <div class="telemetry-header-card">
            <div class="telemetry-title-row">
                <div class="telemetry-brand">
                    <div class="telemetry-brand-icon">⚡</div>
                    <div>
                        <h1 class="telemetry-title">TRẠM QUAN TRẮC & TÁI PHÂN TÍCH KHÍ TƯỢNG ERA5</h1>
                        <p class="telemetry-subtitle">Dữ liệu ECMWF Copernicus Climate Data Store · Giờ Việt Nam (UTC+7)</p>
                    </div>
                </div>
                <div class="telemetry-pill">
                    <span class="status-dot {status_class}"></span>
                    <span>{status_text}</span>
                </div>
            </div>
            <div class="telemetry-pills-row">
                <div class="telemetry-pill accent">
                    <span>Vị trí:</span>
                    <strong>{loc_str}</strong>
                </div>
                <div class="telemetry-pill">
                    <span>Biến quan trắc:</span>
                    <strong>{var_display}</strong>
                </div>
                <div class="telemetry-pill">
                    <span>Chu kỳ:</span>
                    <strong>{freq_display}</strong>
                </div>
                <div class="telemetry-pill">
                    <span>Quy chuẩn thời gian:</span>
                    <strong>UTC+7 (Asia/Ho_Chi_Minh)</strong>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

