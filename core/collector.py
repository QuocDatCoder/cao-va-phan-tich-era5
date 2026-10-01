"""
Module Collector: Cào và tải dữ liệu khí tượng ERA5 từ Copernicus Climate Data Store (CDS API)
Tuân theo chuẩn hướng dẫn của TS. Nguyễn Thị Tuyết Nam
"""
import os
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Optional, Tuple

import config


def get_cdsapirc_path() -> Path:
    """Trả về đường dẫn file cấu hình .cdsapirc trong thư mục home người dùng."""
    return Path.home() / ".cdsapirc"


def is_cds_configured() -> bool:
    """Kiểm tra xem người dùng đã cấu hình file .cdsapirc hợp lệ chưa."""
    rc_path = get_cdsapirc_path()
    if not rc_path.exists():
        return False
    try:
        content = rc_path.read_text(encoding="utf-8")
        has_url = "url:" in content
        has_key = "key:" in content
        return has_url and has_key
    except Exception:
        return False


def setup_cdsapirc(api_url: str, api_key: str, overwrite: bool = True) -> bool:
    """
    Tự động tạo hoặc ghi đè file %USERPROFILE%/.cdsapirc cho Windows / Linux / macOS.
    
    Args:
        api_url: URL của CDS API (ví dụ: 'https://cds.climate.copernicus.eu/api')
        api_key: Khóa API / Personal Access Token của người dùng
        overwrite: Có ghi đè file cũ không
    """
    rc_path = get_cdsapirc_path()
    if rc_path.exists() and not overwrite:
        return False

    content = f"url: {api_url.strip()}\nkey: {api_key.strip()}\n"
    rc_path.write_text(content, encoding="utf-8")
    return True


def extract_zip_if_needed(file_path: Path, extract_to: Optional[Path] = None) -> Path:
    """
    Kiểm tra nếu file tải về là định dạng .zip thì tự động giải nén tìm file .csv bên trong.
    Trả về đường dẫn file .csv kết quả.
    """
    if extract_to is None:
        extract_to = file_path.parent

    # Nếu là file zip
    if zipfile.is_zipfile(file_path):
        with zipfile.ZipFile(file_path, "r") as zip_ref:
            zip_ref.extractall(extract_to)
            csv_files = [f for f in zip_ref.namelist() if f.endswith(".csv")]
            if csv_files:
                return extract_to / csv_files[0]
    return file_path


def download_era5_timeseries(
    lat: float,
    lon: float,
    start_date: str,
    end_date: str,
    variables: Optional[List[str]] = None,
    output_filename: Optional[str] = None,
    dataset: str = config.ERA5_LAND_DATASET,
) -> Path:
    """
    Cào dữ liệu chuỗi thời gian ERA5-Land từ CDS API theo đúng cú pháp Slide 8.
    
    Args:
        lat: Vĩ độ (latitude)
        lon: Kinh độ (longitude)
        start_date: Ngày bắt đầu định dạng 'YYYY-MM-DD'
        end_date: Ngày kết thúc định dạng 'YYYY-MM-DD'
        variables: Danh sách các biến số cần lấy. Mặc định lấy theo config.DEFAULT_VARIABLES
        output_filename: Tên file xuất ra (tùy chọn)
        dataset: Tên dataset trên CDS, mặc định 'reanalysis-era5-land-timeseries'
        
    Returns:
        Path: Đường dẫn tới file dữ liệu .csv đã tải và giải nén
    """
    if not is_cds_configured():
        raise RuntimeError(
            f"Chưa cấu hình tài khoản CDS API! Vui lòng tạo file {get_cdsapirc_path()} "
            "với thông tin url và key theo hướng dẫn ở Slide 4 & 5."
        )

    import cdsapi

    if variables is None:
        variables = config.DEFAULT_VARIABLES

    # Chuẩn hóa khoảng thời gian: "YYYY-MM-DD/YYYY-MM-DD"
    date_range_str = f"{start_date}/{end_date}"

    # Định danh tên file lưu trữ
    if output_filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f"era5_lat{lat:.2f}_lon{lon:.2f}_{start_date}_{end_date}_{timestamp}.csv"

    raw_download_path = config.RAW_DATA_DIR / output_filename
    temp_zip_path = config.RAW_DATA_DIR / f"{raw_download_path.stem}.download"

    # Xây dựng Selection Request theo đúng Slide 8
    request_payload = {
        "variable": variables,
        "location": {
            "longitude": round(lon, 2),
            "latitude": round(lat, 2),
        },
        "date": [date_range_str],
        "data_format": "csv",
    }

    print(f"[CDS API] Đang kết nối và gửi yêu cầu tới ECMWF CDS...")
    print(f"  - Dataset: {dataset}")
    print(f"  - Tọa độ: lat={lat}, lon={lon}")
    print(f"  - Khoảng ngày: {date_range_str}")
    print(f"  - Biến số: {variables}")

    client = cdsapi.Client()
    # Tải file từ CDS
    client.retrieve(dataset, request_payload, str(temp_zip_path))

    # Xử lý giải nén nếu server trả về file nén zip
    result_csv_path = extract_zip_if_needed(temp_zip_path, extract_to=config.RAW_DATA_DIR)

    if result_csv_path != raw_download_path and result_csv_path.exists():
        # Đổi tên file CSV trích xuất thành tên chuẩn hóa
        if raw_download_path.exists():
            raw_download_path.unlink()
        result_csv_path.rename(raw_download_path)
    elif temp_zip_path.exists() and not zipfile.is_zipfile(temp_zip_path):
        temp_zip_path.rename(raw_download_path)

    print(f"[CDS API] Tải thành công và lưu tại: {raw_download_path}")
    return raw_download_path


def get_latest_era5_available_date() -> datetime.date:
    """
    Tính toán ngày mới nhất hiện tại mà cơ sở dữ liệu ERA5T phát hành.
    Do ERA5T có độ trễ khoảng 5 ngày so với thời gian thực.
    """
    now_utc = datetime.now(timezone.utc).date()
    return now_utc - timedelta(days=config.ERA5_REALTIME_LAG_DAYS)


def check_and_sync_realtime(
    existing_csv_path: Path,
    lat: float,
    lon: float,
    variables: Optional[List[str]] = None,
) -> Tuple[bool, Optional[Path], str]:
    """
    Module đồng bộ theo thời gian thực (Real-time Sync):
    Kiểm tra xem dữ liệu trong file đã đến ngày mới nhất của ERA5T chưa.
    Nếu chưa, tự động tải phần dữ liệu mới về và ghép nối.
    
    Returns:
        (has_new_data, updated_file_path, status_message)
    """
    import pandas as pd

    if not existing_csv_path.exists():
        return False, None, "File dữ liệu cũ không tồn tại."

    latest_available = get_latest_era5_available_date()

    try:
        df = pd.read_csv(existing_csv_path)
        # Tìm cột thời gian
        time_col = None
        for col in ["valid_time", "time", "date"]:
            if col in df.columns:
                time_col = col
                break

        if not time_col:
            return False, None, "Không tìm thấy cột mốc thời gian trong file dữ liệu."

        df[time_col] = pd.to_datetime(df[time_col])
        max_existing_date = df[time_col].max().date()

        if max_existing_date >= latest_available:
            msg = f"Dữ liệu đã cập nhật tới ngày mới nhất của ERA5T ({max_existing_date}). Không cần tải thêm."
            return False, existing_csv_path, msg

        # Có dữ liệu mới cần kéo về
        fetch_start = (max_existing_date + timedelta(days=1)).strftime("%Y-%m-%d")
        fetch_end = latest_available.strftime("%Y-%m-%d")

        msg = f"Phát hiện dữ liệu ERA5 mới: từ {fetch_start} đến {fetch_end}. Bắt đầu cào dữ liệu mới..."
        print(f"[Realtime Sync] {msg}")

        # Tải phần bù mới
        new_chunk_path = download_era5_timeseries(
            lat=lat,
            lon=lon,
            start_date=fetch_start,
            end_date=fetch_end,
            variables=variables,
        )

        # Ghép 2 dataframe lại
        df_new = pd.read_csv(new_chunk_path)
        combined_df = pd.concat([df, df_new], ignore_index=True)
        combined_df.drop_duplicates(subset=[time_col], keep="last", inplace=True)
        combined_df.sort_values(by=time_col, inplace=True)

        combined_df.to_csv(existing_csv_path, index=False)
        return True, existing_csv_path, f"Đã đồng bộ thành công dữ liệu mới đến ngày {fetch_end}."

    except Exception as e:
        return False, None, f"Lỗi trong quá trình kiểm tra đồng bộ: {str(e)}"
