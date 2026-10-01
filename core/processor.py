"""
Module Processor: Tiền xử lý, chuẩn hóa dữ liệu và tính toán khí tượng ERA5
Tuân thủ công thức tính gió và chuyển đổi múi giờ theo tài liệu của TS. Nguyễn Thị Tuyết Nam
"""
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

import config


def load_raw_data(csv_file_path: Path) -> pd.DataFrame:
    """Đọc file CSV thô tải về từ CDS API."""
    if not csv_file_path.exists():
        raise FileNotFoundError(f"Không tìm thấy file: {csv_file_path}")
    df = pd.read_csv(csv_file_path)
    return df


def detect_time_column(df: pd.DataFrame) -> str:
    """Tự động phát hiện cột mốc thời gian trong DataFrame ERA5."""
    candidates = ["valid_time", "time", "date", "datetime"]
    for col in candidates:
        if col in df.columns:
            return col
    # Nếu không thấy tên phổ biến, tìm cột có kiểu datetime hoặc object chứa ngày
    for col in df.columns:
        if "time" in col.lower() or "date" in col.lower():
            return col
    raise ValueError(f"Không thể xác định cột thời gian trong các cột: {list(df.columns)}")


def compute_wind(df: pd.DataFrame) -> pd.DataFrame:
    """
    Tính toán Tốc độ gió (Wind Speed) và Hướng gió (Wind Direction)
    Áp dụng chính xác 2 công thức vật lý ở Slide 11 của TS. Nguyễn Thị Tuyết Nam:
      1. Tốc độ gió: WS = sqrt(u^2 + v^2)  (m/s)
      2. Hướng gió:  WD = [180 + (180 / pi) * atan2(u, v)] mod 360  (độ, 0 <= WD < 360)
    """
    u_col = None
    v_col = None

    for col in df.columns:
        c_low = col.lower()
        if "10m_u_component" in c_low or c_low == "u10" or c_low == "u":
            u_col = col
        elif "10m_v_component" in c_low or c_low == "v10" or c_low == "v":
            v_col = col

    if u_col and v_col:
        u = df[u_col].values
        v = df[v_col].values

        # 1. Tính tốc độ gió (WS)
        ws = np.sqrt(u**2 + v**2)
        df["wind_speed"] = np.round(ws, 2)  # m/s

        # 2. Tính hướng gió khí tượng (WD)
        # Chú ý: atan2(u, v) trong numpy là np.arctan2(u, v)
        wd_rad = np.arctan2(u, v)
        wd_deg = (180.0 + (180.0 / np.pi) * wd_rad) % 360.0
        df["wind_direction"] = np.round(wd_deg, 1)

        # Gán nhãn hướng gió phương vị (16 hướng la bàn tiếng Việt)
        compass_labels = [
            "Bắc (N)", "Bắc Đông Bắc (NNE)", "Đông Bắc (NE)", "Đông Đông Bắc (ENE)",
            "Đông (E)", "Đông Đông Nam (ESE)", "Đông Nam (SE)", "Nam Đông Nam (SSE)",
            "Nam (S)", "Nam Tây Nam (SSW)", "Tây Nam (SW)", "Tây Tây Nam (WSW)",
            "Tây (W)", "Tây Tây Bắc (WNW)", "Tây Bắc (NW)", "Bắc Tây Bắc (NNW)"
        ]
        val_idx = np.floor((df["wind_direction"] + 11.25) / 22.5).astype(int) % 16
        df["wind_direction_label"] = [compass_labels[i] for i in val_idx]

    return df


def clean_and_transform(
    df: pd.DataFrame,
    target_timezone: str = config.DEFAULT_TIMEZONE,
) -> pd.DataFrame:
    """
    Chuẩn hóa dữ liệu theo Slide 9:
      - Chuyển múi giờ gốc UTC+0 sang UTC+7 (Giờ Việt Nam)
      - Quy đổi đơn vị:
        + Nhiệt độ Kelvin (K) sang độ Celsius (°C)
        + Lượng mưa từ m sang mm
        + Bức xạ mặt trời sang MJ/m2
      - Tính toán trường gió WS và WD
    """
    df_clean = df.copy()

    # 1. Xử lý cột thời gian & Múi giờ
    time_col = detect_time_column(df_clean)
    
    # Ép kiểu datetime, giả định dữ liệu gốc từ ERA5 là UTC
    datetime_series = pd.to_datetime(df_clean[time_col])
    if datetime_series.dt.tz is None:
        datetime_series = datetime_series.dt.tz_localize("UTC")
    
    # Chuyển đổi múi giờ sang Việt Nam (UTC+7)
    df_clean["datetime_vn"] = datetime_series.dt.tz_convert(target_timezone)
    df_clean["date"] = df_clean["datetime_vn"].dt.date
    df_clean["hour"] = df_clean["datetime_vn"].dt.hour
    df_clean["year_month"] = df_clean["datetime_vn"].dt.strftime("%Y-%m")

    # Đưa cột datetime_vn lên đầu
    cols = ["datetime_vn", "date", "hour"] + [c for c in df_clean.columns if c not in ["datetime_vn", "date", "hour", time_col]]
    df_clean = df_clean[cols]

    # 2. Quy đổi đơn vị các biến khí tượng
    for col in df_clean.columns:
        c_low = col.lower()
        
        # Nhiệt độ: nếu giá trị > 100 thì đó là Kelvin (khoảng 273 - 320K), đổi sang độ C
        if "temperature" in c_low or c_low in ["t2m", "d2m"]:
            if df_clean[col].dtype in [np.float64, np.float32, np.int64, np.int32]:
                if df_clean[col].mean() > 100:
                    df_clean[col] = np.round(df_clean[col] - 273.15, 2)
                    
        # Lượng mưa: total_precipitation đơn vị gốc của ERA5 là mét (m), đổi sang mm (x 1000)
        elif "precipitation" in c_low or c_low == "tp":
            if df_clean[col].dtype in [np.float64, np.float32, np.int64, np.int32]:
                if df_clean[col].max() < 1.0:  # nếu giá trị ở dạng mét
                    df_clean[col] = np.round(df_clean[col] * 1000.0, 3)

        # Bức xạ mặt trời: J/m2 sang MJ/m2 (/ 1e6)
        elif "solar_radiation" in c_low or c_low == "ssrd":
            if df_clean[col].dtype in [np.float64, np.float32, np.int64, np.int32]:
                df_clean["solar_radiation_mj_m2"] = np.round(df_clean[col] / 1e6, 3)

    # 3. Tính toán các trường gió
    df_clean = compute_wind(df_clean)

    return df_clean


def resample_data(df: pd.DataFrame, freq: str = "D") -> pd.DataFrame:
    """
    Tổng hợp dữ liệu theo chu kỳ linh hoạt theo Slide 9:
      - 'h': Theo giờ (giữ nguyên hoặc nội suy)
      - 'D': Theo ngày (Daily)
      - 'W': Theo tuần (Weekly)
      - 'ME': Theo tháng (Monthly)
      - 'YE': Theo năm (Yearly)
    """
    df_res = df.copy()
    df_res.set_index("datetime_vn", inplace=True)

    agg_rules = {}
    for col in df_res.columns:
        c_low = col.lower()
        if "2m_temperature" in c_low or c_low == "t2m":
            agg_rules[col] = ["mean", "min", "max"]
        elif "precipitation" in c_low or c_low == "tp":
            agg_rules[col] = "sum"
        elif col == "wind_speed":
            agg_rules[col] = ["mean", "max"]
        elif col == "solar_radiation_mj_m2":
            agg_rules[col] = "sum"
        elif pd.api.types.is_numeric_dtype(df_res[col]):
            agg_rules[col] = "mean"

    aggregated = df_res.resample(freq).agg(agg_rules)
    
    # Flatten MultiIndex cột nếu có
    if isinstance(aggregated.columns, pd.MultiIndex):
        aggregated.columns = [f"{c[0]}_{c[1]}" if c[1] else c[0] for c in aggregated.columns]
        
    aggregated.reset_index(inplace=True)
    return aggregated


def extract_weather_summary(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Trích xuất bản tóm tắt thống kê chuyên sâu phục vụ AI Agent và Giao diện FE:
      - Cực trị nhiệt độ (cao nhất, thấp nhất, trung bình)
      - Đợt nắng nóng hoặc lạnh bất thường
      - Tổng lượng mưa, ngày mưa to nhất
      - Tốc độ gió cực đại và hướng gió chủ đạo
    """
    summary = {
        "total_records": len(df),
        "start_time": str(df["datetime_vn"].min()),
        "end_time": str(df["datetime_vn"].max()),
        "metrics": {},
        "extreme_events": [],
    }

    temp_col = None
    for col in df.columns:
        if "2m_temperature" in col or col == "t2m":
            temp_col = col
            break

    precip_col = None
    for col in df.columns:
        if "precipitation" in col or col == "tp":
            precip_col = col
            break

    # 1. Thống kê Nhiệt độ
    if temp_col:
        t_max = df[temp_col].max()
        t_min = df[temp_col].min()
        t_mean = df[temp_col].mean()
        max_idx = df[temp_col].idxmax()
        min_idx = df[temp_col].idxmin()

        summary["metrics"]["temperature"] = {
            "mean": round(float(t_mean), 2),
            "max": round(float(t_max), 2),
            "max_time": str(df.loc[max_idx, "datetime_vn"]),
            "min": round(float(t_min), 2),
            "min_time": str(df.loc[min_idx, "datetime_vn"]),
        }

        # Cảnh báo nhiệt độ cực đoan (> 35 độ C là nắng nóng)
        heat_days = df[df[temp_col] >= 35.0]
        if len(heat_days) > 0:
            summary["extreme_events"].append({
                "type": "Nắng nóng gay gắt (>= 35°C)",
                "hours_count": len(heat_days),
                "peak_temp": round(float(t_max), 2),
            })

    # 2. Thống kê Lượng mưa
    if precip_col:
        p_total = df[precip_col].sum()
        p_max = df[precip_col].max()
        rainy_hours = len(df[df[precip_col] > 0.1])

        summary["metrics"]["precipitation"] = {
            "total_mm": round(float(p_total), 2),
            "max_hourly_mm": round(float(p_max), 2),
            "rainy_hours": rainy_hours,
        }

        # Cảnh báo mưa to (> 25mm/h là mưa rất to theo chuẩn khí tượng)
        heavy_rain = df[df[precip_col] >= 25.0]
        if len(heavy_rain) > 0:
            summary["extreme_events"].append({
                "type": "Mưa rất lớn (>= 25mm/h)",
                "hours_count": len(heavy_rain),
                "max_rain_intensity": round(float(p_max), 2),
            })

    # 3. Thống kê Gió
    if "wind_speed" in df.columns:
        ws_max = df["wind_speed"].max()
        ws_mean = df["wind_speed"].mean()
        ws_max_idx = df["wind_speed"].idxmax()
        
        summary["metrics"]["wind"] = {
            "mean_speed_ms": round(float(ws_mean), 2),
            "max_speed_ms": round(float(ws_max), 2),
            "max_wind_time": str(df.loc[ws_max_idx, "datetime_vn"]),
            "dominant_direction": df["wind_direction_label"].mode()[0] if "wind_direction_label" in df.columns else "N/A",
        }

        if ws_max >= 10.8:  # Gió cấp 6 trở lên (>= 10.8 m/s)
            summary["extreme_events"].append({
                "type": "Gió mạnh từ cấp 6 trở lên (>= 10.8 m/s)",
                "max_speed": round(float(ws_max), 2),
            })

    return summary
