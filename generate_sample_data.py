"""
Tạo bộ dữ liệu mẫu định dạng ERA5-Land chính xác theo chuẩn Copernicus CDS
Dùng để kiểm thử ngay lập tức các module processor, visualizer và cung cấp data cho bạn làm FE & AI Agent
"""
from datetime import datetime, timedelta
from pathlib import Path
import numpy as np
import pandas as pd

import config


def generate_mock_era5_csv(
    lat: float = 10.78,
    lon: float = 106.70,
    days: int = 14,
    output_filename: str = "era5_sample_hcmc_14days.csv",
) -> Path:
    """
    Sinh dữ liệu chuỗi thời gian hàng giờ (hourly) chuẩn ERA5-Land:
      - valid_time: chuẩn ISO UTC+0
      - 2m_temperature: Kelvin (khoảng 298K - 308K ~ 25°C - 35°C)
      - 2m_dewpoint_temperature: Kelvin
      - 10m_u_component_of_wind: m/s (thành phần u)
      - 10m_v_component_of_wind: m/s (thành phần v)
      - total_precipitation: mét (ERA5 gốc tính bằng m)
      - surface_solar_radiation_downwards: J/m2
    """
    np.random.seed(42)

    # Bắt đầu từ 14 ngày trước cho tới ngày hôm qua
    end_date = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    start_date = end_date - timedelta(days=days)

    timestamps = pd.date_range(start=start_date, end=end_date, freq="h")
    n = len(timestamps)

    hours = timestamps.hour.values

    # Giả lập nhiệt độ hình sin ngày/đêm (đỉnh lúc 14h chiều, thấp nhất 5h sáng) theo chuẩn Kelvin
    diurnal_cycle = np.sin((hours - 8) * np.pi / 12)
    temp_c = 28.5 + 4.5 * diurnal_cycle + np.random.normal(0, 0.8, n)
    temp_kelvin = temp_c + 273.15  # Đổi sang Kelvin như ERA5 gốc

    # Điểm sương thấp hơn nhiệt độ khoảng 3 - 6 độ
    dewpoint_c = temp_c - np.random.uniform(2.5, 5.0, n)
    dewpoint_kelvin = dewpoint_c + 273.15

    # Gió Tây Nam mùa hè / Đông Bắc mùa khô đặc trưng miền Nam
    u_wind = -2.0 + np.random.normal(0, 1.8, n)  # u component (m/s)
    v_wind = 1.5 + np.random.normal(0, 1.5, n)   # v component (m/s)

    # Mưa (ERA5 gốc là mét): chủ yếu xuất hiện vào chiều tối (14h - 18h)
    precip_prob = np.where((hours >= 13) & (hours <= 19), 0.35, 0.05)
    has_rain = np.random.random(n) < precip_prob
    rain_amount_mm = np.where(has_rain, np.random.exponential(scale=8.0, size=n), 0.0)
    rain_amount_m = rain_amount_mm / 1000.0  # mét

    # Bức xạ mặt trời (J/m2): ban ngày (6h - 18h) có bức xạ, ban đêm = 0
    solar_factor = np.clip(np.sin((hours - 6) * np.pi / 12), 0, 1)
    solar_radiation = solar_factor * 2.5e6 + np.random.uniform(0, 2e5, n) * (solar_factor > 0)

    data = {
        "valid_time": [ts.strftime("%Y-%m-%d %H:%M:%S") for ts in timestamps],
        "latitude": lat,
        "longitude": lon,
        "2m_temperature": np.round(temp_kelvin, 2),
        "2m_dewpoint_temperature": np.round(dewpoint_kelvin, 2),
        "10m_u_component_of_wind": np.round(u_wind, 3),
        "10m_v_component_of_wind": np.round(v_wind, 3),
        "total_precipitation": np.round(rain_amount_m, 6),
        "surface_solar_radiation_downwards": np.round(solar_radiation, 1),
    }

    df = pd.DataFrame(data)
    output_path = config.RAW_DATA_DIR / output_filename
    df.to_csv(output_path, index=False)
    print(f"[Sample Data] Đã tạo thành công bộ dữ liệu mẫu ERA5 gồm {n} bản ghi tại: {output_path}")
    return output_path


if __name__ == "__main__":
    generate_mock_era5_csv()
