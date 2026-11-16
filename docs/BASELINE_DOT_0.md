# Mốc nền đợt 0

## 1 Phiên bản và phạm vi

- Ngày thực hiện: 16/11/2026, giờ Việt Nam.
- Nhánh gốc: `Tuan_Kiet`.
- Commit gốc: `a2faf1a9e21725ca08eef6795e6697a9affdfa4b` — `fix UI`.
- Nhánh làm việc: `work/phase-0-baseline-20261116`, tạo tại đúng commit gốc.
- Trước kiểm tra, không có thay đổi trong file Git đã theo dõi; có các mục chưa theo dõi `docs/KE_HOACH_HOAN_THIEN_DO_AN.md`, `output/`, `tmp/`.
- Không sửa mã nghiệp vụ, không sửa kiểm thử hiện có, không commit hoặc push trong đợt này.

Đã đọc README. README vẫn chủ yếu mô tả ERA5 single levels, trong khi nhánh hiện có thêm `era5_land_timeseries`. Cần cập nhật tài liệu khi hoàn thiện chức năng; chưa thay README ở đợt mốc nền.

## 2 Danh mục file liên quan

| Thành phần | File cần đọc khi thực hiện các đợt sau |
| --- | --- |
| Điểm chạy | `app.py`, `backend/app/main.py` |
| Cấu hình | `backend/app/core/config.py`, `.env.example`; không đọc `.env` thật |
| Hợp đồng dữ liệu | `backend/app/domain/schemas.py` |
| API thu thập | `backend/app/api/downloads.py` |
| API truy vấn và phân tích | `backend/app/api/climate.py` |
| API đồng bộ | `backend/app/api/synchronization.py` |
| Chọn provider | `backend/app/providers/dependencies.py` |
| ERA5 chung | `backend/app/providers/era5_provider.py` |
| ERA5-Land time-series | `backend/app/providers/era5_land_timeseries_provider.py` |
| Dữ liệu giả lập | `backend/app/providers/mock_provider.py` |
| Chuẩn hóa và tổng hợp | `backend/app/services/climate_service.py`, `aggregation.py` |
| Phân tích | `backend/app/services/climate_analysis_service.py` |
| Tác vụ tải | `backend/app/services/download_job_service.py` |
| Đồng bộ | `backend/app/services/synchronization_service.py`, `synchronization_scheduler.py` |
| Dependency các dịch vụ | `backend/app/services/dependencies.py` |
| Quan sát và tọa độ lưới | `backend/app/storage/climate_repository.py` |
| Metadata tác vụ | `backend/app/storage/job_repository.py`, `synchronization_repository.py` |
| Quy tắc thời gian và gió | `backend/app/domain/time_utils.py`, `wind.py` |
| Giao diện | `frontend/dashboard.py`, `frontend/components/controls.py` |
| Biểu đồ và HTTP client | `frontend/components/charts.py`, `frontend/api_client.py`, `frontend/live_updates.py` |
| Kiểm thử | `tests/`, `pytest.ini`; CDS trực tiếp tại `tests/integration/test_era5_live.py` |
| Tài liệu tích hợp | `docs/era5_provider.md`, `docs/AI_API_HANDOFF.md` |

Thư mục `core/` và các script cũ ở gốc không được chọn làm luồng triển khai mới thay cho backend/frontend hiện tại.

## 3 Cách kiểm tra an toàn và có thể tái hiện

Thêm `scripts/baseline_offline.py`, chạy từ gốc repository:

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/baseline_offline.py
```

Script tạo thư mục bằng chứng mới dưới `output/baseline/`, không ghi đè lần chạy trước. Mỗi lần chạy gồm:

1. Ghi commit, nhánh, trạng thái Git và phiên bản thư viện.
2. Chỉ ghi kích thước và thời gian sửa file `.env`/`data/` để so trước và sau; không đọc token hoặc nội dung database người dùng.
3. Tắt nguồn `.env` của Settings trong tiến trình kiểm tra; thêm audit hook ngăn mở `.env`/`.cdsapirc`.
4. Dùng mock, tắt scheduler và tích hợp CDS. Chặn CDS client thật và kết nối ra ngoài; cho phép loopback phục vụ asyncio Windows và HTTP kiểm tra cục bộ.
5. Trỏ đường dữ liệu và metadata của dịch vụ thử vào thư mục scratch riêng.
6. Chạy bộ pytest sẵn có, không dùng pytest cache chung; ghi log và JUnit XML.
7. Khởi động backend/frontend ở cổng loopback tạm, kiểm tra HTTP; chạy Streamlit AppTest trên script thật và lần lượt mở ba mục.
8. Dừng các tiến trình tạm do script tạo; không dừng dịch vụ khác đang chạy của người dùng.

Đây là cấu hình chỉ dành cho tiến trình kiểm tra. Không thay cấu hình ứng dụng thật và không gửi yêu cầu CDS.

## 4 Kết quả mốc nền hợp lệ

Thư mục bằng chứng: `output/baseline/20261116-143513/`.

```text
187 passed, 1 skipped, 1 warning in 11.62s
```

Tổng cộng 188 trường hợp được thu thập: 187 đạt và 1 bỏ qua. Thời gian trên là thời gian pytest trong phiên này, không phải tốc độ tải CDS hoặc benchmark ứng dụng.

| Kiểm tra | Kết quả |
| --- | --- |
| Pytest | Exit code 0, không failed/error |
| Tích hợp CDS trực tiếp | Bỏ qua do `RUN_CDS_INTEGRATION=0` |
| `/health` backend thử, cổng 62458 | HTTP 200, `{"status":"ok"}` |
| `/docs` backend thử | HTTP 200 |
| `/_stcore/health` frontend thử, cổng 62459 | HTTP 200, `ok` |
| Trang frontend `/` | HTTP 200 |
| Streamlit AppTest | Có tiêu đề dashboard, không exception/error |
| Các mục Tổng quan, Chuỗi thời gian, Không gian | AppTest chạy từng mục, không exception/error |
| Bộ chặn credential/CDS/kết nối ngoài | Không có lần vi phạm trong phiên pytest hợp lệ |
| Metadata file người dùng trước/sau | Không thay đổi |

Các cổng trên đã dừng sau kiểm tra. Muốn dùng ứng dụng, chạy backend/frontend bình thường theo README hoặc tự chạy một phiên kiểm tra mới. Không có kiểm tra bằng trình duyệt tương tác trong đợt này; AppTest và HTTP không chứng minh mọi chi tiết hiển thị ở mọi trình duyệt.

### Bằng chứng

- `baseline.json`: commit, nhánh, môi trường, trạng thái HTTP và inventory trước/sau.
- `pytest.log`: toàn bộ kết quả pytest.
- `pytest.xml`: kết quả máy đọc được theo từng trường hợp.
- `test_safety.json`: trạng thái chặn credential/CDS/network.
- `backend.log`, `frontend.log`: log khởi động thật của dịch vụ thử.
- `frontend_apptest.json`, `frontend_apptest.log`: kết quả script Streamlit và từng mục.

## 5 Phân loại vấn đề trước thay đổi

| Loại | Quan sát | Xử lý trong đợt 0 |
| --- | --- | --- |
| Công cụ kiểm tra | Phiên đầu `20261116-143433` chặn cả loopback của asyncio Windows, gây 32 failed; không phải bằng chứng 32 lỗi nghiệp vụ | Sửa guard của script mốc nền để cho loopback, giữ nguyên test/nghiệp vụ, chạy lại toàn bộ; giữ log cũ |
| Tương thích thư viện | 1 StarletteDeprecationWarning về dùng httpx trong TestClient | Ghi nhận; chưa đổi dependency hoặc sửa test để che cảnh báo |
| Kiểm thử lỗi thời theo giao diện | Không phát hiện test thất bại loại này trong suite hiện tại | Chưa sửa; không suy ra suite bao phủ toàn bộ giao diện |
| Nghiệp vụ | Không có test nghiệp vụ thất bại trong lần chạy offline hợp lệ | Chưa sửa nghiệp vụ |
| Tài liệu chưa theo kịp nhánh | README chưa mô tả đầy đủ provider Land và giao diện hiện tại | Ghi nhận để cập nhật đúng phạm vi sau này |
| Chưa kiểm chứng | Tải CDS thật, giấy phép, nguồn gốc toàn bộ dữ liệu, chất lượng thực tế của từng biến | Chưa chạy và không tuyên bố đạt |

## 6 Môi trường ghi nhận

Python 3.12.10; FastAPI 0.162.3; Uvicorn 0.54.0; Streamlit 1.67.0; pytest 9.2.0; cdsapi 0.7.7; xarray 2026.9.0. Phiên bản được đọc từ môi trường tại lúc chạy, không phải một bộ yêu cầu phiên bản mới cho người dùng.

## 7 Điểm khôi phục và bước tiếp theo

Nhánh làm việc hiện xuất phát từ commit `a2faf1a9e21725ca08eef6795e6697a9affdfa4b`. File nghiệp vụ đã theo dõi vẫn trùng commit này. Chỉ thêm script kiểm tra và tài liệu mốc nền; chưa commit chúng. Không đưa `output/`, `tmp/`, `.env` hay dữ liệu vào Git.

Đợt 1 chưa được triển khai. Mốc này dùng để so kết quả sau khi bổ sung lựa chọn nguồn theo request, truy vấn DB-only và thông tin coverage. Các lần chạy sau phải lưu log mới, không lấy lại số 187 hay 188 làm kết quả mới.
