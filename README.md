# ĐỒ ÁN SEMINAR CHUYÊN NGÀNH: CÀO VÀ PHÂN TÍCH DỮ LIỆU KHÍ TƯỢNG ERA5

Hệ thống thu thập, tiền xử lý, lưu trữ CSDL SQLite chuỗi thời gian, trực quan hóa biểu đồ tương tác thời gian thực và tích hợp AI Agent phân tích dữ liệu khí quyển ERA5 từ ECMWF / Copernicus Climate Data Store (CDS).

---

## 🏗️ KIẾN TRÚC HỆ THỐNG VÀ PHÂN CHIA MODULE

```
cao-va-phan-tich-era5/
│
├── core/
│   ├── collector.py           # [MODULE 1] Cào dữ liệu CDS API & Quản lý đồng bộ Real-time
│   ├── processor.py           # [MODULE 2] Xử lý dữ liệu (Múi giờ UTC+7, tính gió WS/WD, đổi đơn vị)
│   ├── visualizer.py          # [MODULE 3] Trực quan hóa dữ liệu, xuất biểu đồ Plotly (JSON / HTML)
│   └── database.py            # [MODULE 4] Quản lý CSDL SQLite, chống trùng lặp, hỗ trợ AI Agent
│
├── data/
│   ├── raw/                   # Dữ liệu thô (.csv, .zip) tải từ CDS API
│   ├── processed/             # Dữ liệu đã chuẩn hóa (UTC+7, °C, mm, WS, WD)
│   ├── weather.db             # CƠ SỞ DỮ LIỆU SQLITE (Lưu trữ chuỗi thời gian khí tượng)
│   └── dashboard_preview.html # Dashboard tương tác Plotly xuất mẫu
│
├── app.py                     # Giao diện Web App Streamlit hoàn chỉnh (UI + Real-time + AI Agent)
├── run_app.bat                # Phím tắt mở Web App chỉ bằng 1 cú click đúp chuột trên Windows
├── config.py                  # Cấu hình biến số, tọa độ mẫu, đường dẫn
├── generate_sample_data.py    # Sinh dữ liệu mẫu ERA5-Land thực tế để test nhanh offline
├── test_modules.py            # Script kiểm thử tự động toàn diện pipeline (100% pass)
└── requirements.txt           # Danh sách thư viện cần thiết
```

---

## ⚡ HƯỚNG DẪN CHẠY ỨNG DỤNG

### FastAPI backend
```powershell
uvicorn backend.app.main:app --reload
```

### Streamlit dashboard
Dashboard Phase 7 communicates only with the FastAPI backend.

```powershell
$env:API_BASE_URL="http://localhost:8000"
streamlit run frontend/dashboard.py
```

### Cách 1: Khởi động Web App cực nhanh (Khuyên dùng)
👉 **Click đúp chuột vào file `run_app.bat`**  
Ứng dụng sẽ tự động bật trình duyệt web tại địa chỉ: `http://localhost:8501`.

### Cách 2: Chạy bằng dòng lệnh PowerShell
```powershell
D:\cao-va-phan-tich-era5\.venv\Scripts\streamlit.exe run app.py
```

### Cách 3: Chạy kiểm thử tự động toàn bộ 5 bước của các Module
```powershell
D:\cao-va-phan-tich-era5\.venv\Scripts\python.exe test_modules.py
```

---

## 🔌 TÀI LIỆU GIAO TIẾP VỚI CÁC THÀNH VIÊN KHÁC TRONG NHÓM

### 1. Dành cho bạn làm Frontend (FE) & API:
Bạn có thể kết nối trực tiếp với CSDL SQLite hoặc dùng Module Visualizer:
```python
from core import database, visualizer

# 1. Truy vấn dữ liệu siêu tốc từ CSDL SQLite qua Index
df = database.query_weather_records(location_name="TP. Hồ Chí Minh", start_date="2026-09-01", end_date="2026-09-30")

# 2. Lấy biểu đồ dạng Plotly JSON (để nhúng Web hoặc truyền qua REST API)
fig = visualizer.get_temperature_figure(df)
json_payload = visualizer.export_figure_to_json(fig)
```

### 2. Dành cho 2 bạn làm AI Agent:
CSDL đã tích hợp sẵn 2 hàm công cụ chuyên biệt để làm **Function Calling / Tools cho AI Agent**:
```python
from core import database

# Công cụ 1: Tự động trích xuất thống kê tổng hợp (Nhiệt độ max/min, tổng mưa, gió cực đại)
summary = database.query_analytics_summary("TP. Hồ Chí Minh")

# Công cụ 2: Cho phép AI Agent tự sinh câu lệnh SQL an toàn (Text-to-SQL)
ai_sql = "SELECT date, MAX(temperature) as peak_temp FROM weather_records GROUP BY date ORDER BY peak_temp DESC LIMIT 5;"
df_result = database.execute_custom_sql_for_ai(ai_sql)
```

---

## 🔑 HƯỚNG DẪN CẤU HÌNH TÀI KHOẢN CDS API THẬT
File cấu hình đã được tạo tự động tại `C:\Users\XXXXXX\.cdsapirc`:
```text
url: https://cds.climate.copernicus.eu/api
key: <YOUR_CDS_API_KEY>
```
Đã kiểm tra kết nối và tải thành công dữ liệu thật từ máy chủ ECMWF Châu Âu.
