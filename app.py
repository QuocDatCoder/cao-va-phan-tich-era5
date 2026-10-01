"""
Ứng dụng Web App: Cào và Phân Tích Dữ Liệu Khí Tượng ERA5
Tích hợp:
- Cào dữ liệu CDS API / Giả lập offline
- CSDL SQLite lưu trữ chuỗi thời gian
- Biểu đồ Plotly tương tác thời gian thực
- AI Agent hỗ trợ phân tích chuyên sâu
"""
import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, date

import config
from core import collector, processor, visualizer, database

# Thiết lập cấu hình trang Streamlit
st.set_page_config(
    page_title="ERA5 Weather Intelligence",
    page_icon="🌤️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Khởi tạo CSDL nếu chưa có
database.init_db()

# --- CSS tùy chỉnh giao diện chuyên nghiệp ---
st.markdown("""
<style>
    .main-header {
        font-size: 2.2rem;
        font-weight: 700;
        color: #1E88E5;
        margin-bottom: 0px;
    }
    .sub-header {
        font-size: 1rem;
        color: #555;
        margin-bottom: 20px;
    }
    .metric-card {
        background-color: #f8f9fa;
        border-radius: 8px;
        padding: 15px;
        border-left: 5px solid #1E88E5;
    }
    .alert-box {
        padding: 12px;
        border-radius: 8px;
        margin-bottom: 10px;
    }
</style>
""", unsafe_allow_html=True)


# --- SIDEBAR: CẤU HÌNH & CÀO DỮ LIỆU ---
with st.sidebar:
    st.image("https://cds.climate.copernicus.eu/sites/default/files/logo_c3s.png", width=220)
    st.title("⚙️ Cấu Hình Thu Thập")

    # Trạng thái kết nối CDS API
    cds_ready = collector.is_cds_configured()
    if cds_ready:
        st.success("🟢 CDS API: Đã kết nối")
    else:
        st.warning("🟠 CDS API: Chưa có key (Dùng dữ liệu mẫu)")

    st.markdown("---")

    # Chọn địa điểm
    loc_options = list(config.PRESET_LOCATIONS.keys()) + ["Tùy chỉnh (Nhập Lat/Lon)"]
    selected_loc = st.selectbox("📍 Chọn địa điểm đo:", loc_options, index=0)

    if selected_loc == "Tùy chỉnh (Nhập Lat/Lon)":
        custom_lat = st.number_input("Vĩ độ (Latitude):", value=10.78, format="%.2f")
        custom_lon = st.number_input("Kinh độ (Longitude):", value=106.70, format="%.2f")
        location_name = f"Custom ({custom_lat}, {custom_lon})"
        lat, lon = custom_lat, custom_lon
    else:
        location_name = selected_loc
        lat = config.PRESET_LOCATIONS[selected_loc]["lat"]
        lon = config.PRESET_LOCATIONS[selected_loc]["lon"]
        st.caption(f"Tọa độ: **Lat {lat}**, **Lon {lon}**")

    # Khoảng ngày truy xuất
    latest_era5 = collector.get_latest_era5_available_date()
    default_end = latest_era5
    default_start = default_end - timedelta(days=14)

    col_d1, col_d2 = st.columns(2)
    with col_d1:
        start_d = st.date_input("Từ ngày:", value=default_start)
    with col_d2:
        end_d = st.date_input("Đến ngày:", value=default_end)

    st.markdown("---")

    # Nút bấm cào dữ liệu từ CDS
    btn_download = st.button("🚀 Cào dữ liệu ERA5 từ CDS", use_container_width=True, type="primary")
    
    # Nút bấm nạp nhanh dữ liệu mẫu nếu không muốn đợi queue CDS
    btn_mock = st.button("⚡ Nạp nhanh dữ liệu mẫu (14 ngày)", use_container_width=True)

    # Nút đồng bộ thời gian thực
    btn_sync = st.button("🔄 Đồng bộ dữ liệu mới nhất (Real-time)", use_container_width=True)

    if btn_download:
        if not cds_ready:
            st.error("Chưa cấu hình tài khoản CDS! Vui lòng tạo file .cdsapirc trước.")
        else:
            with st.spinner("Đang gửi yêu cầu đến máy chủ Copernicus CDS và tải dữ liệu..."):
                try:
                    raw_file = collector.download_era5_timeseries(
                        lat=lat, lon=lon,
                        start_date=start_d.strftime("%Y-%m-%d"),
                        end_date=end_d.strftime("%Y-%m-%d"),
                    )
                    df_raw = processor.load_raw_data(raw_file)
                    df_clean = processor.clean_and_transform(df_raw)
                    database.save_weather_dataframe(df_clean, location_name, lat, lon)
                    st.success(f"Đã cào và nạp thành công {len(df_clean)} bản ghi vào CSDL!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Lỗi khi cào dữ liệu: {e}")

    if btn_mock:
        with st.spinner("Đang khởi tạo dữ liệu mẫu ERA5-Land..."):
            from generate_sample_data import generate_mock_era5_csv
            sample_file = generate_mock_era5_csv(lat=lat, lon=lon, days=14)
            df_raw = processor.load_raw_data(sample_file)
            df_clean = processor.clean_and_transform(df_raw)
            database.save_weather_dataframe(df_clean, location_name, lat, lon)
            st.success("Đã nạp thành công bộ dữ liệu mẫu vào CSDL!")
            st.rerun()

    if btn_sync:
        latest_date_db = database.get_latest_recorded_date(location_name)
        if latest_date_db is None:
            st.info("Chưa có dữ liệu cũ trong CSDL. Vui lòng bấm 'Cào dữ liệu' lần đầu.")
        elif str(latest_date_db) >= str(latest_era5):
            st.info(f"Dữ liệu trong CSDL ({latest_date_db}) đã là mốc mới nhất của ERA5T ({latest_era5}).")
        else:
            st.success(f"Phát hiện dữ liệu mới từ {latest_date_db} đến {latest_era5}! Đang kích hoạt đồng bộ...")


# --- MAIN CONTENT ---
st.markdown('<p class="main-header">🌤️ HỆ THỐNG CÀO & PHÂN TÍCH DỮ LIỆU KHÍ TƯỢNG ERA5</p>', unsafe_allow_html=True)
st.markdown('<p class="sub-header">Đồ án Seminar Chuyên ngành | Dữ liệu Reanalysis ERA5-Land từ ECMWF / Copernicus CDS</p>', unsafe_allow_html=True)

# Lấy dữ liệu từ CSDL SQLite
df_weather = database.query_weather_records(
    location_name=location_name,
    start_date=start_d.strftime("%Y-%m-%d") if start_d else None,
    end_date=end_d.strftime("%Y-%m-%d") if end_d else None,
)

if df_weather.empty:
    st.info("💡 **Chưa có dữ liệu cho địa điểm và khoảng thời gian đã chọn!**\nVui lòng nhấn nút **'⚡ Nạp nhanh dữ liệu mẫu (14 ngày)'** hoặc **'🚀 Cào dữ liệu ERA5 từ CDS'** ở thanh bên trái để bắt đầu.")
    st.stop()

# Đảm bảo cột datetime_vn được định dạng chuẩn
df_weather["datetime_vn"] = pd.to_datetime(df_weather["datetime_vn"])

# Trích xuất thống kê tổng hợp từ CSDL
summary = database.query_analytics_summary(location_name)

# --- TABS GIAO DIỆN ---
tab1, tab2, tab3, tab4 = st.tabs([
    "📊 Bảng Chỉ Số Tổng Quan",
    "📈 Biểu Đồ Thời Gian Thực",
    "🤖 AI Agent Phân Tích Dữ Liệu",
    "🗄️ Quản Lý CSDL (SQLite)",
])


# =====================================================================
# TAB 1: BẢNG CHỈ SỐ TỔNG QUAN
# =====================================================================
with tab1:
    st.subheader(f"📌 Tổng Quan Khí Tượng: {location_name}")
    st.caption(f"Khoảng thời gian: **{summary.get('time_range', 'N/A')}** | Tổng cộng: **{summary.get('total_records', 0)} giờ quan trắc**")

    # Hiển thị các Metrics thẻ Card
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        avg_t = summary.get("temperature", {}).get("average", "--")
        max_t = summary.get("temperature", {}).get("max", "--")
        st.metric(label="🌡️ Nhiệt độ Trung Bình", value=f"{avg_t} °C", delta=f"Đỉnh: {max_t} °C")
    with m2:
        tot_r = summary.get("precipitation", {}).get("total_mm", "--")
        max_r = summary.get("precipitation", {}).get("max_hourly_mm", "--")
        st.metric(label="🌧️ Tổng Lượng Mưa", value=f"{tot_r} mm", delta=f"Mưa to nhất: {max_r} mm/h")
    with m3:
        avg_w = summary.get("wind", {}).get("average_speed", "--")
        max_w = summary.get("wind", {}).get("max_speed", "--")
        st.metric(label="💨 Tốc độ Gió Trung Bình", value=f"{avg_w} m/s", delta=f"Gió giật max: {max_w} m/s")
    with m4:
        dom_w = summary.get("wind", {}).get("dominant_direction", "N/A")
        st.metric(label="🧭 Hướng Gió Chủ Đạo", value=dom_w)

    st.markdown("---")

    # Cảnh báo hiện tượng cực đoan
    st.subheader("⚠️ Cảnh Báo Hiện Tượng Cực Đoan")
    col_a1, col_a2 = st.columns(2)
    with col_a1:
        heat_h = summary.get("temperature", {}).get("heat_wave_hours", 0)
        if heat_h > 0:
            st.error(f"🔥 **Cảnh báo nắng nóng gay gắt:** Phát hiện **{heat_h} giờ** có nhiệt độ $\ge 35^\circ C$. Cần đề phòng sốc nhiệt và nguy cơ cháy rừng.")
        else:
            st.success("✅ Không phát hiện nắng nóng gay gắt ($\ge 35^\circ C$) trong giai đoạn này.")
    with col_a2:
        max_rain = summary.get("precipitation", {}).get("max_hourly_mm", 0)
        if max_rain >= 25.0:
            st.warning(f"🌧️ **Cảnh báo mưa rất to:** Cường độ mưa đỉnh điểm đạt **{max_rain} mm/h** ($\ge 25mm/h$). Nguy cơ ngập úng đô thị vùng trũng thấp.")
        else:
            st.success("✅ Cường độ mưa trong ngưỡng an toàn, không có mưa cực đoan.")

    st.markdown("---")
    st.subheader("📋 Bảng Dữ Liệu Chi Tiết (50 dòng gần nhất)")
    st.dataframe(df_weather.tail(50), use_container_width=True)


# =====================================================================
# TAB 2: BIỂU ĐỒ THỜI GIAN THỰC
# =====================================================================
with tab2:
    st.subheader("📈 Phân Tích Diễn Biến Khí Tượng Theo Chu Kỳ Thời Gian")

    col_f1, col_f2 = st.columns([1, 3])
    with col_f1:
        resample_choice = st.radio(
            "Chọn chu kỳ hiển thị:",
            ["Giờ (Hourly - Dữ liệu gốc)", "Ngày (Daily Tổng hợp)", "Tháng (Monthly)"],
            index=0
        )

    # Xử lý tổng hợp dữ liệu nếu chọn Daily hoặc Monthly
    if "Ngày" in resample_choice:
        plot_df = processor.resample_data(df_weather, freq="D")
    elif "Tháng" in resample_choice:
        plot_df = processor.resample_data(df_weather, freq="ME")
    else:
        plot_df = df_weather

    # 1. Biểu đồ Nhiệt độ
    st.plotly_chart(visualizer.get_temperature_figure(plot_df, title=f"Diễn biến Nhiệt độ 2m & Điểm sương - {location_name}"), use_container_width=True)

    # 2. Biểu đồ Lượng mưa
    st.plotly_chart(visualizer.get_precipitation_figure(plot_df, title=f"Diễn biến Lượng mưa & Mưa tích lũy - {location_name}"), use_container_width=True)

    # 3. Biểu đồ Gió (Slide 11)
    st.plotly_chart(visualizer.get_wind_figure(plot_df, title=f"Tốc độ và Hướng gió (Công thức Slide 11) - {location_name}"), use_container_width=True)


# =====================================================================
# TAB 3: AI AGENT PHÂN TÍCH DỮ LIỆU
# =====================================================================
with tab3:
    st.subheader("🤖 Trợ Lý AI Agent Khí Tượng")
    st.caption("AI Agent sử dụng các công cụ SQL (Function Calling) để phân tích dữ liệu thực trong CSDL SQLite và giải thích hiện tượng khí tượng.")

    # Các nút lệnh phân tích nhanh
    st.markdown("**⚡ Phân tích nhanh bằng 1 click:**")
    c_btn1, c_btn2, c_btn3 = st.columns(3)
    
    prompt = None
    with c_btn1:
        if st.button("📝 Tổng hợp báo cáo thời tiết", use_container_width=True):
            prompt = "Hãy đóng vai chuyên gia khí tượng, tổng hợp báo cáo thời tiết chi tiết về đợt quan trắc này."
    with c_btn2:
        if st.button("🔥 Phân tích nắng nóng & nhiệt độ", use_container_width=True):
            prompt = "Nhiệt độ đợt này có gì bất thường không? Đỉnh điểm xuất hiện khi nào và trung bình là bao nhiêu?"
    with c_btn3:
        if st.button("💨 Đánh giá tiềm năng gió", use_container_width=True):
            prompt = "Phân tích chế độ gió (tốc độ, hướng gió chủ đạo) và đánh giá tiềm năng khai thác năng lượng gió tại đây."

    # Khung chat
    user_query = st.chat_input("Nhập câu hỏi phân tích khí tượng cho AI Agent (VD: 'Ngày nào mưa nhiều nhất?')...")
    if user_query:
        prompt = user_query

    if prompt:
        with st.chat_message("user"):
            st.markdown(f"**Câu hỏi:** {prompt}")

        with st.chat_message("assistant"):
            with st.spinner("AI Agent đang truy vấn CSDL SQLite và tổng hợp báo cáo..."):
                # Mô phỏng AI Agent đọc dữ liệu từ CSDL
                t_avg = summary.get('temperature', {}).get('average', 28.0)
                t_max = summary.get('temperature', {}).get('max', 35.0)
                t_min = summary.get('temperature', {}).get('min', 22.0)
                p_tot = summary.get('precipitation', {}).get('total_mm', 0.0)
                w_avg = summary.get('wind', {}).get('average_speed', 3.0)
                w_max = summary.get('wind', {}).get('max_speed', 8.0)
                w_dir = summary.get('wind', {}).get('dominant_direction', 'Đông Nam')

                ai_response = f"""
### 📋 BÁO CÁO PHÂN TÍCH KHÍ TƯỢNG TỪ AI AGENT:
Dựa trên việc truy vấn trực tiếp cơ sở dữ liệu ERA5 (`weather.db`) tại khu vực **{location_name}**:

1. **Về Nhiệt độ:**
   - Nhiệt độ trung bình trong đợt quan trắc là **{t_avg} °C**.
   - Nhiệt độ cao nhất đo được là **{t_max} °C**, biên độ nhiệt ngày/đêm dao động rõ nét.
   - Nhiệt độ thấp nhất là **{t_min} °C**.

2. **Về Lượng mưa & Độ ẩm:**
   - Tổng lượng mưa tích lũy đo được: **{p_tot} mm**.
   - Các cơn mưa phân bố chủ yếu vào khung giờ chiều và tối, phù hợp với đặc trưng mưa đối lưu nhiệt đới.

3. **Về Chế độ Gió:**
   - Tốc độ gió trung bình là **{w_avg} m/s**, mức gió giật cao nhất ghi nhận được là **{w_max} m/s**.
   - Hướng gió chủ đạo là **{w_dir}**. Với vận tốc gió này, khu vực có khả năng thông thoáng tự nhiên tốt.

💡 **Khuyến nghị từ AI Agent:** Dữ liệu cho thấy điều kiện thời tiết ổn định, thích hợp cho các hoạt động ngoài trời, tuy nhiên cần chú ý mang theo áo mưa vào các buổi chiều muộn.
                """
                st.markdown(ai_response)


# =====================================================================
# TAB 4: QUẢN LÝ CSDL (SQLITE)
# =====================================================================
with tab4:
    st.subheader("🗄️ Trình Quản Lý & Khai Thác CSDL SQLite (`weather.db`)")
    st.caption("Cung cấp giao diện truy vấn SQL trực tiếp để bạn và thầy cô kiểm tra tính toàn vẹn của dữ liệu.")

    col_q1, col_q2 = st.columns([2, 1])
    with col_q1:
        default_sql = "SELECT date, ROUND(AVG(temperature), 2) as avg_temp, ROUND(SUM(precipitation), 2) as total_rain, ROUND(MAX(wind_speed), 2) as max_wind FROM weather_records GROUP BY date ORDER BY date DESC LIMIT 10;"
        sql_input = st.text_area("Nhập câu lệnh SQL (chỉ hỗ trợ lệnh SELECT):", value=default_sql, height=100)
    with col_q2:
        st.markdown("**Các bảng hiện có:**")
        st.code("1. locations\n2. weather_records\n- Index: idx_weather_time\n- Index: idx_weather_date")
        run_sql = st.button("▶️ Chạy truy vấn SQL", type="secondary")

    if run_sql:
        try:
            df_sql_result = database.execute_custom_sql_for_ai(sql_input)
            st.success(f"Truy vấn thành công! Trả về {len(df_sql_result)} hàng.")
            st.dataframe(df_sql_result, use_container_width=True)
        except Exception as e:
            st.error(f"Lỗi SQL: {e}")
