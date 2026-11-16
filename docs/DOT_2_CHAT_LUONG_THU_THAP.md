# Đợt 2 — chất lượng thu thập Land và ERA5

## Phạm vi và trạng thái

Áp dụng cho tác vụ thu thập chủ động chạy qua `DownloadJobService`. Truy vấn DB-only của đợt 1 không gọi parser hoặc CDS. Không kiểm tra CDS trực tiếp trong kiểm thử offline. Không bổ sung ngưỡng nhiệt độ, lượng mưa hoặc bức xạ để loại dữ liệu.

- Land: đọc các CSV trong file tải, kiểm tra sáu biến gốc mà provider yêu cầu, lưu phần hợp lệ tại điểm lưới.
- ERA5: đọc NetCDF theo biến và ô lưới; giữ các ô hữu hạn, đúng giờ và trong khoảng yêu cầu. Thiếu một biến không làm mất biến khác đã nhập.
- Một job có dữ liệu hợp lệ đã nhập có thể `completed` với `quality.status=warnings`, `quality.complete=false`. Đây không phải xác nhận đầy đủ dữ liệu.
- Không nhập được giá trị hợp lệ nào: job `failed`, kể cả file rỗng hoặc mọi ô đều NaN. File nguồn được giữ lại để kiểm tra.
- Đồng bộ có báo cáo chưa hoàn chỉnh chuyển sang `failed`, giữ nguyên `last_synced_date`; cả chạy bình thường và đối soát job sau khởi động lại đều kiểm tra điều này. Quy tắc hiện tại bảo thủ: mọi cảnh báo chặn nâng mốc.

## Báo cáo và cách đọc bộ đếm

`GET /api/v1/jobs/{job_id}` có trường `quality`. Chi tiết: `GET /api/v1/jobs/{job_id}/quality?offset=0&limit=100`, tối đa 500 mục mỗi trang. Dashboard hiển thị tổng hợp và 100 cảnh báo đầu tiên.

| Trường | Ý nghĩa |
|---|---|
| rows_read | Land: số bản ghi CSV (không gồm header/comment). ERA5: số ô lưới-thời gian của biến đọc được, gồm bản trùng và thời gian sai. Không thể đọc cấu trúc/biến thì không cộng dòng. |
| valid_values | Số giá trị hữu hạn, đúng giờ, trong phạm vi, sau loại trùng được chọn để nhập. |
| imported_values | Số giá trị đã ghi thành công qua repository; gồm cả upsert, không phải số dòng mới tăng thêm trong DB. Lỗi transaction có thể khiến số này nhỏ hơn valid_values. |
| missing_values | Ô CSV rỗng/không có giá trị; không được thay bằng 0. |
| invalid_values | Ô không phải số, NaN/Infinity hoặc kết quả chuyển đơn vị không hữu hạn. NetCDF giải mã ô thiếu thành NaN nên không phân biệt nguồn ô trống với NaN. |
| invalid_time_rows | Bản ghi CSV hoặc lát thời gian NetCDF sai timestamp/không đúng giờ. |
| duplicate_values | Ô trùng được bỏ qua, gồm cùng giá trị và xung đột; giữ giá trị hợp lệ đầu tiên. |
| missing_hours | Land: số cặp biến-giờ không có giá trị tốt. ERA5: số lát giờ không có trong tọa độ thời gian của từng biến (toàn bộ lưới); ô NaN riêng lẻ được tính ở invalid_values. |
| issue_count / issue_counts | Tổng số mục cảnh báo / số mục theo mã; không cộng các bộ đếm con để suy ra số ô lỗi vì một ô có thể vừa có lỗi giá trị vừa tạo lỗ hổng giờ. |

Mỗi mục có mã, thông báo, file; số dòng, biến, giờ UTC và tọa độ được ghi khi xác định được. Các mã gồm `missing_value`, `invalid_number`, `nonfinite_value`, `invalid_time`, `missing_column`, `missing_time_column`, `missing_hour`, `duplicate`, `duplicate_conflict`, `empty_file`, `unreadable_file`, `outside_request`, `invalid_coordinates`, `missing_coordinates`, `invalid_dimensions`, `no_valid_values`.

## Lưu trữ và tương thích

Migration tự động, chỉ thêm `download_jobs.quality_json`, bảng `download_quality_issues` và index theo job. Không xóa/sửa bảng quan sát, file tải hoặc cấu hình `.env`. Job cũ có `quality=null`: không được mô tả như đã kiểm tra chất lượng. Mở repository nhiều lần không mất job cũ hay cảnh báo. Chi tiết ghi theo lô 200 mục, đọc phân trang; tổng hợp ghi khi job kết thúc, không cam kết khôi phục job bị tắt cưỡng bức giữa chừng.

Legacy API tự tải vẫn được giữ tương thích; không hồi tố báo cáo chất lượng cho dữ liệu đã lưu trước đây. Cơ chế dò đơn vị nhiệt độ bằng giá trị của đường Land legacy được giữ nguyên; tác vụ có báo cáo mới không dùng ngưỡng suy đoán đó. Đơn vị nhập theo hợp đồng dataset hiện có, không tự suy luận đơn vị từ độ lớn số liệu.

File hỏng được giữ, do đó chạy lại cùng yêu cầu có thể gặp lại file đó. Chưa thêm giao diện thay thế/quản lý phiên bản file hỏng; không tự xóa file người dùng.

## Kiểm thử và chạy thử

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/baseline_offline.py
```

Runner chặn CDS thật và đọc credentials, dùng DB/file thử riêng trong `output/baseline`, lưu log và kiểm tra mở backend/frontend. Các fixture bao gồm ô rỗng, NaN, timestamp sai, cột thiếu, giờ thiếu, xung đột trùng, dòng tốt sau NaN, file rỗng, toàn NaN; kiểm tra số giá trị được giữ, cảnh báo sau mở lại DB, migration, mốc đồng bộ và truy vấn không lẫn dataset.

Kết quả thực chạy ngày 16/11/2026 trên working tree từ commit `e2566c0`: **253 passed, 1 skipped, 1 warning trong 17,25 giây**. Log: `output/baseline/20261116-154845/pytest.log`; báo cáo an toàn: `test_safety.json` cùng thư mục. Bài bị bỏ qua là tích hợp CDS thật; cảnh báo là deprecation Starlette/httpx. Backend health/docs và frontend health/page trả HTTP 200; Streamlit AppTest không có lỗi, gồm kiểm thử riêng phần hiển thị cảnh báo chất lượng. Runner xác nhận file cấu hình/dữ liệu người dùng không thay đổi. Các dịch vụ do runner mở đã được dừng sau kiểm tra.

Chạy ứng dụng bằng hai terminal ở thư mục gốc dự án:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
```

```powershell
$env:API_BASE_URL="http://127.0.0.1:8000"
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Mở `http://127.0.0.1:8501`, chọn nguồn và khoảng nhỏ, bấm thu thập chủ động rồi mở báo cáo chất lượng. Việc tải thật là thao tác người dùng tự thực hiện; fixture kiểm thử không chứng minh tính sẵn sàng hoặc chất lượng toàn bộ dữ liệu CDS thật.

## Xác minh CDS thật sau kiểm thử offline

Ngày 16/11/2026, sau khi người dùng cho phép, đã tạo **một** yêu cầu Land thật qua API cục bộ mà dashboard sử dụng. Không đọc/in token, không thay `.env`, không xóa DB/file, không commit/push. Thao tác bấm trên Chrome không hoàn tất do cửa sổ có thao tác người dùng khác; vì vậy không mô tả đây là kiểm thử trọn luồng bấm nút trên trình duyệt.

- Job: `81f0467f-3c3e-4d7d-816c-dcde1adf4fca`.
- Dataset: `reanalysis-era5-land-timeseries`; điểm yêu cầu `(10.8230, 106.6296)`, điểm lưới nhận được `(10.8, 106.6)`.
- Thời gian tải: ngày `04/01/2025 UTC`, 00:00–23:00, một ngày UTC.
- Kết quả: `completed`, chất lượng `passed`, `complete=true`, **144 giá trị được nhập**, 0 cảnh báo. File nguồn mới có mtime `2026-11-16T08:59:03.708903+00:00`, được giữ trong `data/raw/era5/reanalysis-era5-land-timeseries/all_variables/`.
- ZIP thật chứa 4 CSV, mỗi CSV 24 dòng, tổng `rows_read=96`; hai CSV chứa hai biến, hai CSV chứa một biến, nên tổng số giá trị là `24 × 6 = 144`, không mâu thuẫn với số dòng.
- Đối chiếu SQLite bằng kết nối chỉ đọc: sáu biến, mỗi biến đúng 24 giá trị. Sáu truy vấn DB-only tương ứng đều trả `complete=true`, đúng dataset và điểm lưới.
- Đọc lại báo cáo lưu trong SQLite khớp kết quả API. Streamlit AppTest chạy component báo cáo với API thật hiển thị `passed · Đã lưu: 144 giá trị · Cảnh báo: 0`, không có exception/error. Đây là kiểm tra component dùng dữ liệu thật, không phải ảnh chụp xác nhận báo cáo trên Chrome.

Bằng chứng: `output/live_checks/20261116-155834/`, gồm `request.json`, `job.json`, `quality.json`, `verification.json`, `raw_structure.json`, `dashboard_component.json` và kết quả truy vấn sáu biến. Lượt này xác minh Land; **chưa tải ERA5 chung thật**, không suy rộng kết quả một mẫu thành chứng nhận chất lượng mọi dữ liệu CDS.

### Kiểm tra lại trực tiếp trên Chrome

Theo yêu cầu tiếp theo của người dùng, đã thao tác thành công trên Chrome: chọn bộ lọc, bấm **Bắt đầu thu thập**, thấy trạng thái chuyển sang **Hoàn tất**, mở **Báo cáo chất lượng của tác vụ** và đọc các bộ đếm. Sử dụng file Land đã tồn tại cho khoảng `2024-12-31`–`2025-01-01 UTC` tại `(10.8230, 106.6296)` để không gửi yêu cầu CDS mới.

Job kiểm tra giao diện: `ea5bf810-9348-499c-a45d-c9108cc81f75`. Trên Chrome hiển thị `passed`, `complete=true`, `rows_read=192`, `valid_values=288`, `imported_values=288`, `issue_count=0`; đọc SQLite độc lập xác nhận trạng thái và báo cáo khớp. Đây là job nhập lại file sẵn có qua nút giao diện, không phải một lượt tải CDS mới. Dữ liệu truy vấn ngày `01/01/2025 UTC+7` có 24 quan sát; 288 giá trị nhập bao gồm 6 biến × 48 giờ UTC. Văn bản accessibility của trình duyệt được lưu tại `output/live_checks/browser-recheck/accessibility.txt`.

Trước khi kiểm tra nút với file sẵn có, cũng đã chọn ngày `04/01/2025 UTC+7` trên Chrome và quan sát dashboard trả 24 quan sát, trung bình nhiệt độ 26,08°C. Giá trị này cho ngày Việt Nam, khác khoảng một ngày UTC của lượt tải thật ở trên.
