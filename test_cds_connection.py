"""
Kiểm tra kết nối thực tế tới máy chủ Copernicus CDS API
"""
import cdsapi

def test_connection():
    print("[1] Khởi tạo CDS API Client...")
    try:
        client = cdsapi.Client()
        print("[2] Kết nối thành công tới CDS API Client.")
        
        # Test request nhỏ 2 ngày
        dataset = "reanalysis-era5-land-timeseries"
        request = {
            "variable": ["2m_temperature"],
            "location": {"longitude": 106.70, "latitude": 10.78},
            "date": ["2025-01-01/2025-01-02"],
            "data_format": "csv",
        }
        print("[3] Gửi yêu cầu kiểm tra tới server CDS...")
        target = "data/raw/test_cds_conn.csv"
        client.retrieve(dataset, request, target)
        print("🎉 [4] THÀNH CÔNG: Dữ liệu thật đã được tải về máy!")
    except Exception as e:
        print(f"❌ LỖI KHI GỌI CDS API: {e}")

if __name__ == "__main__":
    test_connection()
