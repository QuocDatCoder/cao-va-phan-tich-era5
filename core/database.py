"""
Module Database: Quản lý Cơ sở dữ liệu SQLite lưu trữ chuỗi thời gian khí tượng ERA5
Hỗ trợ:
- Lưu trữ dữ liệu chuẩn hóa, chống trùng lặp dữ liệu (Deduplication)
- Truy vấn siêu tốc bằng Index phục vụ Frontend và Dashboard
- Cung cấp các hàm phân tích số liệu SQL cho AI Agent (Text-to-SQL / Function Calling)
"""
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import pandas as pd
import config

DB_FILE_PATH = config.DATA_DIR / "weather.db"


def get_connection() -> sqlite3.Connection:
    """Khởi tạo kết nối SQLite với chế độ WAL tối ưu tốc độ đọc/ghi."""
    conn = sqlite3.connect(str(DB_FILE_PATH))
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db():
    """Khởi tạo cấu trúc các bảng và chỉ mục (Index) cho CSDL."""
    conn = get_connection()
    cursor = conn.cursor()

    # 1. Bảng danh mục địa điểm
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS locations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        latitude REAL NOT NULL,
        longitude REAL NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 2. Bảng lưu trữ dữ liệu thời tiết chuỗi thời gian
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS weather_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        location_id INTEGER NOT NULL,
        valid_time_utc TEXT NOT NULL,
        datetime_vn TEXT NOT NULL,
        date TEXT NOT NULL,
        hour INTEGER NOT NULL,
        temperature REAL,
        dewpoint REAL,
        precipitation REAL,
        wind_u REAL,
        wind_v REAL,
        wind_speed REAL,
        wind_direction REAL,
        wind_direction_label TEXT,
        solar_radiation REAL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (location_id) REFERENCES locations(id) ON DELETE CASCADE,
        UNIQUE(location_id, valid_time_utc)
    );
    """)

    # 3. Tạo các chỉ mục Index để tăng tốc truy vấn lọc ngày/giờ
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_weather_time ON weather_records(location_id, datetime_vn);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_weather_date ON weather_records(location_id, date);")

    # Nạp sẵn các địa điểm mẫu từ file cấu hình config.py
    for name, coords in config.PRESET_LOCATIONS.items():
        cursor.execute("""
        INSERT OR IGNORE INTO locations (name, latitude, longitude)
        VALUES (?, ?, ?);
        """, (name, coords["lat"], coords["lon"]))

    conn.commit()
    conn.close()
    print(f"[Database] Đã khởi tạo CSDL thành công tại: {DB_FILE_PATH}")


def get_or_create_location(name: str, lat: float, lon: float) -> int:
    """Lấy location_id theo tên hoặc tạo mới nếu chưa tồn tại."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM locations WHERE name = ?;", (name,))
    row = cursor.fetchone()
    if row:
        loc_id = row[0]
    else:
        cursor.execute("INSERT INTO locations (name, latitude, longitude) VALUES (?, ?, ?);", (name, lat, lon))
        loc_id = cursor.lastrowid
        conn.commit()

    conn.close()
    return loc_id


def save_weather_dataframe(df: pd.DataFrame, location_name: str, lat: Optional[float] = None, lon: Optional[float] = None) -> int:
    """
    Lưu DataFrame đã xử lý vào CSDL SQLite.
    Tự động chống trùng lặp theo cặp (location_id, valid_time_utc).
    
    Returns:
        int: Số lượng bản ghi được thêm/cập nhật.
    """
    if df.empty:
        return 0

    init_db()

    # Lấy tọa độ nếu chưa truyền
    if lat is None or lon is None:
        if "latitude" in df.columns and "longitude" in df.columns:
            lat = float(df["latitude"].iloc[0])
            lon = float(df["longitude"].iloc[0])
        elif location_name in config.PRESET_LOCATIONS:
            lat = config.PRESET_LOCATIONS[location_name]["lat"]
            lon = config.PRESET_LOCATIONS[location_name]["lon"]
        else:
            lat, lon = config.DEFAULT_LOCATION["lat"], config.DEFAULT_LOCATION["lon"]

    loc_id = get_or_create_location(location_name, lat, lon)

    # Chuẩn bị dữ liệu để insert
    records = []
    for _, row in df.iterrows():
        # Lấy mốc UTC
        dt_vn = str(row.get("datetime_vn", ""))
        valid_utc = str(row.get("valid_time", dt_vn))
        date_str = str(row.get("date", dt_vn[:10]))
        hour_val = int(row.get("hour", 0))

        # Nhiệt độ
        t = row.get("2m_temperature", row.get("t2m", None))
        d = row.get("2m_dewpoint_temperature", row.get("d2m", None))
        p = row.get("total_precipitation", row.get("tp", 0.0))
        u = row.get("10m_u_component_of_wind", row.get("u10", None))
        v = row.get("10m_v_component_of_wind", row.get("v10", None))
        ws = row.get("wind_speed", None)
        wd = row.get("wind_direction", None)
        wd_lbl = row.get("wind_direction_label", None)
        solar = row.get("solar_radiation_mj_m2", row.get("surface_solar_radiation_downwards", None))

        records.append((
            loc_id, valid_utc, dt_vn, date_str, hour_val,
            t, d, p, u, v, ws, wd, wd_lbl, solar
        ))

    conn = get_connection()
    cursor = conn.cursor()

    # Sử dụng INSERT OR REPLACE để tự động cập nhật nếu đã có bản ghi cùng mốc giờ
    cursor.executemany("""
    INSERT OR REPLACE INTO weather_records (
        location_id, valid_time_utc, datetime_vn, date, hour,
        temperature, dewpoint, precipitation, wind_u, wind_v,
        wind_speed, wind_direction, wind_direction_label, solar_radiation
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, records)

    conn.commit()
    inserted_count = cursor.rowcount
    conn.close()

    print(f"[Database] Đã lưu {len(records)} bản ghi vào CSDL cho địa điểm '{location_name}'.")
    return len(records)


def query_weather_records(
    location_name: str = "TP. Hồ Chí Minh",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    """
    Truy vấn dữ liệu thời tiết từ SQLite phục vụ vẽ biểu đồ Frontend.
    Tốc độ cực nhanh nhờ đánh Index trên (location_id, datetime_vn).
    """
    conn = get_connection()
    query = """
    SELECT 
        w.datetime_vn, w.date, w.hour,
        w.temperature as [2m_temperature],
        w.dewpoint as [2m_dewpoint_temperature],
        w.precipitation as total_precipitation,
        w.wind_speed, w.wind_direction, w.wind_direction_label,
        w.solar_radiation as solar_radiation_mj_m2,
        l.name as location_name, l.latitude, l.longitude
    FROM weather_records w
    JOIN locations l ON w.location_id = l.id
    WHERE l.name = ?
    """
    params = [location_name]

    if start_date:
        query += " AND w.date >= ?"
        params.append(start_date)
    if end_date:
        query += " AND w.date <= ?"
        params.append(end_date)

    query += " ORDER BY w.datetime_vn ASC;"

    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return df


def get_latest_recorded_date(location_name: str = "TP. Hồ Chí Minh") -> Optional[str]:
    """Lấy ngày mới nhất hiện có trong CSDL để phục vụ kiểm tra đồng bộ Real-time."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT MAX(w.date)
    FROM weather_records w
    JOIN locations l ON w.location_id = l.id
    WHERE l.name = ?;
    """, (location_name,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row and row[0] else None


def query_analytics_summary(location_name: str = "TP. Hồ Chí Minh") -> Dict[str, Any]:
    """
    Truy vấn thống kê tổng hợp trực tiếp bằng SQL (rất nhẹ và nhanh) để cung cấp cho AI Agent:
    - Nhiệt độ trung bình, cao nhất, thấp nhất
    - Tổng lượng mưa, số giờ có mưa
    - Tốc độ gió trung bình và cực đại
    """
    conn = get_connection()
    cursor = conn.cursor()

    query = """
    SELECT 
        COUNT(*) as total_hours,
        MIN(datetime_vn) as start_time,
        MAX(datetime_vn) as end_time,
        ROUND(AVG(temperature), 2) as avg_temp,
        ROUND(MAX(temperature), 2) as max_temp,
        ROUND(MIN(temperature), 2) as min_temp,
        ROUND(SUM(precipitation), 2) as total_rain_mm,
        ROUND(MAX(precipitation), 2) as max_rain_hourly,
        ROUND(AVG(wind_speed), 2) as avg_wind_speed,
        ROUND(MAX(wind_speed), 2) as max_wind_speed
    FROM weather_records w
    JOIN locations l ON w.location_id = l.id
    WHERE l.name = ?;
    """
    cursor.execute(query, (location_name,))
    row = cursor.fetchone()

    # Truy vấn hướng gió chủ đạo
    cursor.execute("""
    SELECT wind_direction_label, COUNT(*) as freq
    FROM weather_records w
    JOIN locations l ON w.location_id = l.id
    WHERE l.name = ? AND wind_direction_label IS NOT NULL
    GROUP BY wind_direction_label
    ORDER BY freq DESC
    LIMIT 1;
    """, (location_name,))
    dominant_wind_row = cursor.fetchone()
    dominant_wind = dominant_wind_row[0] if dominant_wind_row else "N/A"

    # Truy vấn số sự kiện nắng nóng (>= 35 độ C)
    cursor.execute("""
    SELECT COUNT(*) FROM weather_records w
    JOIN locations l ON w.location_id = l.id
    WHERE l.name = ? AND temperature >= 35.0;
    """, (location_name,))
    heat_hours = cursor.fetchone()[0]

    conn.close()

    if not row or row[0] == 0:
        return {"status": "no_data", "message": "Chưa có dữ liệu cho địa điểm này"}

    return {
        "location": location_name,
        "total_records": row[0],
        "time_range": f"{row[1]} đến {row[2]}",
        "temperature": {
            "average": row[3],
            "max": row[4],
            "min": row[5],
            "heat_wave_hours": heat_hours,
        },
        "precipitation": {
            "total_mm": row[6],
            "max_hourly_mm": row[7],
        },
        "wind": {
            "average_speed": row[8],
            "max_speed": row[9],
            "dominant_direction": dominant_wind,
        }
    }


def execute_custom_sql_for_ai(sql_query: str) -> pd.DataFrame:
    """
    Hàm cho phép AI Agent thực thi truy vấn SQL để trả lời các câu hỏi tự nhiên của người dùng.
    Ví dụ: 'Tìm top 5 ngày mưa to nhất', 'Thời điểm gió mạnh nhất'.
    """
    # Chỉ cho phép SELECT để đảm bảo an toàn CSDL
    cleaned = sql_query.strip()
    if not cleaned.upper().startswith("SELECT"):
        raise PermissionError("AI Agent chỉ được phép thực thi các câu lệnh truy vấn đọc dữ liệu (SELECT)!")

    conn = get_connection()
    df = pd.read_sql_query(cleaned, conn)
    conn.close()
    return df
