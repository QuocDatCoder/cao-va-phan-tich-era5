# Đợt 1 Thu thập chủ động và truy vấn SQLite

## Các luồng riêng biệt

Dashboard gọi `/api/v1/stored/climate/…` để đọc SQLite. Luồng mới không tạo provider, không gọi CDS, không nhập lại file raw và không tự sinh mock. Database chưa tồn tại trả dữ liệu thiếu mà không tạo database.

Các API cũ `/api/v1/climate/…` và synchronization được giữ để tương thích, vẫn có thể tự tải khi thiếu. Không dùng các API cũ này cho chatbot hoặc luồng DB-only sau này. Dashboard mới không gọi chúng và không tự chạy đồng bộ; tải dữ liệu cần bấm **Bắt đầu thu thập**.

## Nguồn và tác vụ tải

| source | Dataset mặc định | Phạm vi tải mới |
| --- | --- | --- |
| land | reanalysis-era5-land-timeseries | Một location |
| era5 | reanalysis-era5-single-levels | Toàn bộ lưới trong area |
| mock | mock | Dữ liệu giả lập lưu riêng; giao diện dùng điểm |

ERA5 chung hỗ trợ cả đất liền và biển, không tự lọc chỉ còn biển. Chưa nhận diện đất/biển bằng mask. Đổi nguồn không sửa `.env` hoặc provider mặc định toàn cục. Tên source API là `land`, khác giá trị cấu hình cũ `era5_land_timeseries`.

`POST /api/v1/downloads` giữ URL và HTTP 202. Ví dụ Land:

```json
{
  "source": "land",
  "variable": "temperature",
  "location": {"latitude": 10.8231, "longitude": 106.6297},
  "start_date": "2025-01-01",
  "end_date": "2025-01-01"
}
```

Ví dụ ERA5 vùng:

```json
{
  "source": "era5",
  "variable": "temperature",
  "area": {"north": 11, "south": 10, "west": 106, "east": 107},
  "start_date": "2025-01-01",
  "end_date": "2025-01-01"
}
```

Các tham số này là ví dụ, không phải bằng chứng tải CDS thật đã thành công. Ngày tải là ngày UTC. Form xuất phát từ khoảng ngày Việt Nam và bao phủ các ngày UTC cần thiết.

- Cần đúng một trong `location` hoặc `area`. Land tường minh chỉ nhận điểm; ERA5 tường minh nhận vùng.
- Caller cũ không gửi source vẫn chọn nguồn từ cấu hình. Nếu nguồn mặc định là Land, area cũ được chuyển thành điểm tâm và lưu đúng `spatial_mode=point`, `area=null`, `location=…`.
- `GET /api/v1/jobs/{job_id}` trả thêm source, spatial_mode, location và retrieval_variables.
- Source/dataset được lưu từ queued. Đổi provider mặc định không đổi tác vụ đã tạo. Đổi tên dataset cấu hình sẽ làm tác vụ cũ thất bại có giải thích, không tải nhầm nguồn.
- Land giữ tối ưu xin sáu biến gốc; gió dẫn xuất cần cả u và v. `variable` là biến yêu cầu, `retrieval_variables` là biến gửi trong yêu cầu CDS, không chứng nhận mọi giá trị nhận được đều hợp lệ. UI công bố tải thêm biến.
- Thu thập mock mới lưu quan sát dưới dataset mock. Truy vấn mock khi chưa thu thập không tự sinh dữ liệu.
- Migration download_jobs chỉ thêm cột, giữ dữ liệu/file/trạng thái cũ. Job lịch sử không rõ nguồn giữ source=null, không đoán từ cấu hình hiện tại. Job queued cũ không rõ nguồn cần tạo lại thay vì chạy dưới một nguồn tùy ý.

## Truy vấn dữ liệu đã lưu

Các endpoint mới gồm timeseries, timeseries.csv, aggregate, statistics, trend, heatmap, map dưới `/api/v1/stored/climate/`. Tham số bắt buộc: source, variable, latitude, longitude, start, end. Aggregate thêm frequency và aggregation như API cũ.

Ví dụ PowerShell:

```powershell
$query = @{
  source = "land"
  variable = "temperature"
  latitude = 10.8231
  longitude = 106.6297
  start = "2025-01-01T00:00:00+07:00"
  end = "2025-01-01T23:00:00+07:00"
}
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/stored/climate/timeseries" -Method Get -Body $query
```

| Metadata | Ý nghĩa |
| --- | --- |
| source, dataset, query_mode | Định danh nguồn thực và db_only |
| location, grid_location | Vị trí yêu cầu và điểm lưới thực đã lưu; lưới có thể null |
| expected_count | Số mốc giờ trong khoảng yêu cầu |
| available_count | Giờ đủ dữ liệu nguồn; gió cần cả u/v |
| count | Số giá trị đầu ra hữu hạn |
| coverage | available_count / expected_count |
| complete, status | Đủ dữ liệu hay partial/missing |
| missing_ranges | Biến, start/end, count và time_basis của từng khoảng thiếu |
| undefined_count | Giờ đủ u/v nhưng hướng gió không xác định do lặng gió |

HTTP 200 chỉ nói truy vấn thực hiện được, không đồng nghĩa đủ dữ liệu. Luôn đọc complete/status. Không điền 0 vào ô thiếu, không nội suy hoặc tự tải. Khoảng thiếu ghi timestamp nguồn với offset +07:00, có thể tách theo nửa đêm UTC. Thiếu u và v được ghi riêng; không cộng số thiếu hai cột để suy ra số giờ thiếu duy nhất.

Statistics trả kết quả trên phần đang có, kèm coverage; không có quan sát thì chỉ số null. Trend không fit khi thiếu giờ hoặc ít hơn hai điểm. Lặng gió không phải lỗi thiếu nguồn. Hướng gió không dùng hồi quy tuyến tính.

Aggregate dùng lịch Việt Nam và quy ước căn giờ tích lũy của service hiện hành; đợt này không sửa quy tắc khoa học chung. Với aggregate tích lũy, missing_ranges ghi giờ nguồn, còn bucket đầu ra dùng hour_start. Heatmap có cờ kỳ chưa trọn, tránh diễn giải vài ngày thành cả tháng.

CSV có source/dataset, variable, unit, vị trí yêu cầu/lưới và coverage/complete. API CSV có header X-Data-Source, X-Dataset, X-Data-Coverage, X-Data-Complete kể cả khi không có dòng dữ liệu. Cache Streamlit có nguồn và query mode trong khóa. Không dùng số liệu nguồn này làm cache hit cho nguồn khác.

## Chạy thử không cần CDS

1. Khởi động backend/frontend theo lệnh cũ, scheduler tắt.
2. Chọn **Demo — dữ liệu giả lập đã thu thập**, khoảng một ngày.
3. Trước thu thập, xem thông báo thiếu; bấm **Bắt đầu thu thập**, đợi completed.
4. Xem biểu đồ/CSV, xác nhận nguồn mock. Khởi động lại backend vẫn đọc được dữ liệu.
5. Đổi sang Land hoặc ERA5: không được dùng số mock thay số thật; nếu chưa có dữ liệu phải báo thiếu.
6. Nguồn thật chỉ tải khi bấm thu thập với cấu hình CDS/giấy phép hợp lệ; chưa chạy CDS trực tiếp trong kiểm thử đợt này.

## Giới hạn và phần tiếp theo

Chưa thêm warning chi tiết theo ô/hàng, nhận diện đất/biển tự động, retry, chống job trùng toàn cục, worker nhiều tiến trình hay chatbot. API anomaly/compare cũ vẫn có thể tải; chưa có phiên bản DB-only mới cho hai phép này. Luồng synchronization cũ chỉ phục vụ tương thích và không được gọi từ giao diện mới.

## Kiểm thử

Kiểm thử mới khóa CDS client/provider/import raw để phát hiện mọi lời gọi tải từ endpoint stored. Kiểm tra đủ, thiếu giờ, chưa có dữ liệu, ba nguồn cùng tọa độ/biến/giờ, đọc sau restart, migration additive, tác vụ điểm/vùng và cache giao diện. Streamlit AppTest thực hiện thu thập mock chủ động rồi mở ba mục ở cả ba nguồn mà không tải nguồn thật.

Chạy `scripts/baseline_offline.py` tạo log mới, không đọc .env, chặn kết nối ngoài và dùng dữ liệu thử riêng. Kết quả cuối được ghi sau khi chạy, không lấy lại con số kiểm thử cũ.

Kết quả ngày 16/11/2026 tại `output/baseline/20261116-151841/`:

- 228 passed, 1 skipped, 1 warning in 19.61s. So với mốc nền 187 đạt, thêm 41 trường hợp kiểm thử; các test cũ vẫn đạt.
- Kiểm thử CDS trực tiếp bỏ qua; cảnh báo là Starlette TestClient deprecation như mốc nền.
- Backend health và Swagger, frontend health và trang gốc đều HTTP 200.
- Streamlit AppTest thu thập mock chủ động thành công, kiểm tra Tổng quan/Chuỗi thời gian/Không gian ở cả mock, land, era5 không exception/error. Không bấm thu thập nguồn thật.
- Guard không ghi nhận mở credential, gọi CDS client hoặc kết nối ra ngoài. Metadata file `.env` và `data/` trước/sau không thay đổi.
- `git diff --check` đạt. Chưa commit hoặc push, chưa kiểm chứng tải CDS thật.

Log các lần trước được giữ nguyên, gồm lần phát hiện lỗi response model bản đồ thiếu location. Lỗi này đã sửa và chạy lại toàn bộ suite cùng AppTest; không dùng log lỗi làm kết quả cuối.
