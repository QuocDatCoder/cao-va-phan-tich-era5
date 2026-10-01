"""
Configuration module for ERA5 Data Pipeline
Dự án: Cào và phân tích dữ liệu khí tượng toàn cầu ERA5
"""
import os
from pathlib import Path

# Thư mục gốc dự án
BASE_DIR = Path(__file__).resolve().parent

# Đường dẫn lưu trữ dữ liệu
DATA_DIR = BASE_DIR / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
PROCESSED_DATA_DIR = DATA_DIR / "processed"

# Tạo sẵn các thư mục nếu chưa có
RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)

# Tên cơ sở dữ liệu CDS API (Theo slide bài giảng của TS. Nguyễn Thị Tuyết Nam)
ERA5_LAND_DATASET = "reanalysis-era5-land-timeseries"

# Danh sách các biến số khí tượng mặc định
DEFAULT_VARIABLES = [
    "2m_temperature",                     # Nhiệt độ tại bề mặt 2m
    "2m_dewpoint_temperature",            # Điểm sương tại 2m
    "10m_u_component_of_wind",            # Thành phần gió Đông-Tây (u) tại 10m
    "10m_v_component_of_wind",            # Thành phần gió Bắc-Nam (v) tại 10m
    "total_precipitation",                # Tổng lượng mưa
    "surface_solar_radiation_downwards",  # Bức xạ mặt trời hướng xuống mặt đất
]

# Tọa độ mẫu của một số thành phố lớn tại Việt Nam
PRESET_LOCATIONS = {
    "TP. Hồ Chí Minh": {"lat": 10.78, "lon": 106.70},
    "Hà Nội": {"lat": 21.03, "lon": 105.85},
    "Đà Nẵng": {"lat": 16.05, "lon": 108.20},
    "Cần Thơ": {"lat": 10.04, "lon": 105.78},
    "Hải Phòng": {"lat": 20.84, "lon": 106.68},
    "Nha Trang": {"lat": 12.24, "lon": 109.19},
    "Đà Lạt": {"lat": 11.94, "lon": 108.44},
}

# Tọa độ mặc định (TP. Hồ Chí Minh theo ví dụ Slide 8)
DEFAULT_LOCATION = PRESET_LOCATIONS["TP. Hồ Chí Minh"]

# Múi giờ mặc định cho Việt Nam
DEFAULT_TIMEZONE = "Asia/Ho_Chi_Minh"  # UTC+7

# Độ trễ cập nhật của dữ liệu thời gian thực ERA5T (khoảng 5 ngày)
ERA5_REALTIME_LAG_DAYS = 5
