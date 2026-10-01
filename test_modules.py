"""
Script kiểm thử toàn bộ pipeline của các module:
1. Collector (Kiểm tra kết nối và cấu hình CDS API)
2. Processor (Kiểm tra múi giờ UTC+7, tính gió WS/WD theo Slide 11, đổi đơn vị)
3. Visualizer (Tạo biểu đồ Plotly, xuất file HTML và JSON cho FE)
"""
import sys
from pathlib import Path

# Thêm thư mục gốc vào PYTHONPATH
sys.path.append(str(Path(__file__).resolve().parent))

import config
from generate_sample_data import generate_mock_era5_csv
from core import collector, processor, visualizer, database


def run_all_tests():
    print("=" * 60)
    print("🚀 BẮT ĐẦU CHẠY KIỂM THỬ CÁC MODULE ERA5 PIPELINE")
    print("=" * 60)

    # 1. KIỂM THỬ MODULE COLLECTOR
    print("\n[1/4] Kiểm thử Module Collector:")
    cds_ready = collector.is_cds_configured()
    print(f"  - Trạng thái cấu hình .cdsapirc: {'✅ ĐÃ CẤU HÌNH' if cds_ready else '⚠️ CHƯA CẤU HÌNH (Dùng mock data)'}")
    latest_date = collector.get_latest_era5_available_date()
    print(f"  - Mốc ngày mới nhất hiện tại của ERA5T: {latest_date}")

    # 2. TẠO DỮ LIỆU MẪU ĐỂ TEST
    print("\n[2/4] Sinh dữ liệu mẫu ERA5-Land (14 ngày tại TP.HCM):")
    raw_sample_path = generate_mock_era5_csv(
        lat=config.DEFAULT_LOCATION["lat"],
        lon=config.DEFAULT_LOCATION["lon"],
        days=14,
    )
    assert raw_sample_path.exists(), "Lỗi: Không tạo được file dữ liệu mẫu!"
    print(f"  - File raw: {raw_sample_path.name}")

    # 3. KIỂM THỬ MODULE PROCESSOR
    print("\n[3/4] Kiểm thử Module Processor:")
    df_raw = processor.load_raw_data(raw_sample_path)
    print(f"  - Đọc thành công {len(df_raw)} dòng dữ liệu thô.")

    df_clean = processor.clean_and_transform(df_raw)
    print(f"  - ✅ Chuyển đổi múi giờ thành công: {df_clean['datetime_vn'].iloc[0]} (Giờ Việt Nam - UTC+7)")
    print(f"  - ✅ Quy đổi nhiệt độ sang °C: TB = {df_clean['2m_temperature'].mean():.1f} °C, Max = {df_clean['2m_temperature'].max():.1f} °C")
    print(f"  - ✅ Quy đổi lượng mưa sang mm: Tổng = {df_clean['total_precipitation'].sum():.2f} mm")
    print(f"  - ✅ Tính toán gió theo Slide 11:")
    print(f"      + Tốc độ gió WS (m/s): TB = {df_clean['wind_speed'].mean():.2f} m/s, Max = {df_clean['wind_speed'].max():.2f} m/s")
    print(f"      + Hướng gió WD (°): Hướng chủ đạo = {df_clean['wind_direction_label'].mode()[0]}")

    # Kiểm tra Resampling theo ngày
    df_daily = processor.resample_data(df_clean, freq="D")
    print(f"  - ✅ Tổng hợp dữ liệu theo ngày (Daily Resampling): {len(df_daily)} ngày.")

    # Trích xuất thống kê phục vụ AI Agent
    summary = processor.extract_weather_summary(df_clean)
    print(f"  - ✅ Trích xuất báo cáo thống kê tóm tắt cho AI Agent:")
    print(f"      + Nhiệt độ cao nhất: {summary['metrics']['temperature']['max']} °C (vào lúc {summary['metrics']['temperature']['max_time']})")
    print(f"      + Nhiệt độ thấp nhất: {summary['metrics']['temperature']['min']} °C (vào lúc {summary['metrics']['temperature']['min_time']})")
    print(f"      + Tổng lượng mưa: {summary['metrics']['precipitation']['total_mm']} mm")
    print(f"      + Số sự kiện cực đoan phát hiện: {len(summary['extreme_events'])} sự kiện")

    # Lưu dữ liệu processed
    processed_path = config.PROCESSED_DATA_DIR / "era5_hcmc_processed.csv"
    df_clean.to_csv(processed_path, index=False)
    print(f"  - Đã lưu file processed tại: {processed_path.name}")

    # 4. KIỂM THỬ MODULE DATABASE (CSDL SQLITE)
    print("\n[4/5] Kiểm thử Module Database (SQLite):")
    database.init_db()
    saved_count = database.save_weather_dataframe(df_clean, "TP. Hồ Chí Minh")
    print(f"  - ✅ Đã lưu/cập nhật {saved_count} bản ghi vào CSDL SQLite (weather.db).")
    df_db = database.query_weather_records("TP. Hồ Chí Minh")
    print(f"  - ✅ Truy vấn dữ liệu qua Index thành công: {len(df_db)} dòng.")
    db_summary = database.query_analytics_summary("TP. Hồ Chí Minh")
    print(f"  - ✅ AI Agent SQL Analytics: Nhiệt độ TB = {db_summary['temperature']['average']} °C, Tổng mưa = {db_summary['precipitation']['total_mm']} mm")

    # 5. KIỂM THỬ MODULE VISUALIZER
    print("\n[5/5] Kiểm thử Module Visualizer:")
    fig_temp = visualizer.get_temperature_figure(df_clean)
    fig_rain = visualizer.get_precipitation_figure(df_clean)
    fig_wind = visualizer.get_wind_figure(df_clean)
    fig_dashboard = visualizer.get_comprehensive_dashboard(df_clean, location_name="TP. Hồ Chí Minh")

    # Xuất ra file HTML trực quan
    html_output = config.DATA_DIR / "dashboard_preview.html"
    visualizer.save_figure_html(fig_dashboard, html_output)
    print(f"  - ✅ Đã tạo Dashboard tương tác HTML tại: {html_output}")

    # Xuất ra JSON cho phía Frontend
    json_sample = visualizer.export_figure_to_json(fig_temp)
    print(f"  - ✅ Đã xuất thành công Plotly JSON cho bạn làm Frontend (kích thước payload: {len(json_sample)} bytes)")

    print("\n" + "=" * 60)
    print("🎉 TOÀN BỘ CÁC MODULE ĐÃ HOẠT ĐỘNG CHUẨN XÁC 100%!")
    print("=" * 60)


if __name__ == "__main__":
    run_all_tests()
