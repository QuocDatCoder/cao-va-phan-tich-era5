# Bàn giao API dữ liệu cho nhóm AI Agent

## Phạm vi phiên bản tích hợp

Nhóm AI có thể bắt đầu tích hợp các chức năng nhiệt độ, điểm sương, lượng mưa,
thống kê, tìm xu hướng tuyến tính trong khoảng chọn, anomaly và so sánh.
Đây là phiên bản API nền tảng; chưa phải toàn bộ tính năng đồ án.
Gió, bức xạ, tổng hợp ngày/tháng/năm, API cực trị kèm thời điểm và truy vấn
dài hơn giới hạn hiện tại vẫn là công việc tiếp theo.

Không truy cập trực tiếp SQLite. Agent gọi HTTP API để giữ độc lập với cách lưu trữ.
Không cần huấn luyện mô hình riêng để sử dụng các API này làm công cụ của Agent.

## Chạy backend

Python 3.11 trở lên. Sau khi nhận branch/commit đã thống nhất:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:CLIMATE_PROVIDER="mock"
$env:ERA5_SYNC_ENABLED="false"
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
```

Nếu đã có `.venv` thì chỉ cần cập nhật dependencies và chạy backend.
Swagger: http://127.0.0.1:8000/docs. Health: http://127.0.0.1:8000/health.
Địa chỉ localhost là máy đang chạy ứng dụng, không phải máy của thành viên khác.

`mock` tạo dữ liệu giả lập để kiểm tra tích hợp. Dùng `temperature` hoặc
`precipitation` khi thử mock. Để dùng ERA5 thật, dừng backend và đặt trong cùng terminal:

```powershell
$env:CLIMATE_PROVIDER="era5"
$env:CDS_URL="https://cds.climate.copernicus.eu/api"
$env:CDS_API_KEY="<personal-access-token>"
```

Sau đó chạy lại lệnh uvicorn. Cần chấp nhận điều khoản dataset
`reanalysis-era5-single-levels` trên CDS. Không đưa API key vào Git hoặc prompt của Agent.

## Quy ước dữ liệu

- Nhiệt độ (`temperature`/`2m_temperature`) và điểm sương
  (`dewpoint`/`2m_dewpoint_temperature`): °C.
- Lượng mưa (`precipitation`/`total_precipitation`): mm cho mỗi quan sát giờ.
- Timestamp phản hồi: ISO 8601 có `+07:00`. Không cộng thêm 7 giờ hay trừ thêm 273.15.
- Truy vấn nên dùng datetime có offset. Nếu không có offset, backend hiểu là UTC.
- Khoảng truy vấn gồm cả hai đầu nếu có quan sát đúng mốc giờ.
- `location` trong phản hồi hiện là tọa độ yêu cầu; giá trị lấy tại điểm lưới ERA5 gần nhất.
- SQLite giữ UTC, nhiệt độ K và mưa mm; API chuyển đổi ở service layer.
- Giới hạn mặc định: một truy vấn ngắn hơn 7 ngày (ngày Việt Nam đầy đủ có tối đa 168 quan sát).
- Ngày mới nhất có thể tải được ước tính từ cấu hình độ trễ, không phải dữ liệu quan trắc trực tiếp.

## Các công cụ HTTP

| Method và đường dẫn | Công dụng |
|---|---|
| GET `/api/v1/climate/timeseries` | Chuỗi thời gian |
| GET `/api/v1/climate/statistics` | Mean, min, max, std tổng thể và count |
| GET `/api/v1/climate/trend` | Hệ số dốc theo chỉ số quan sát và chiều hướng |
| GET `/api/v1/climate/anomaly` | Giá trị trừ trung bình khoảng nền |
| GET `/api/v1/climate/compare` | Trung bình hai giai đoạn, chênh lệch A−B |
| GET `/api/v1/climate/heatmap` | Trung bình nhóm theo tháng/năm Việt Nam |
| GET `/api/v1/climate/map` | Trung bình tại một điểm, không phải bản đồ phủ toàn cầu |
| POST `/api/v1/downloads` | Tạo tác vụ tải vùng và nhập SQLite |
| GET `/api/v1/jobs/{job_id}` | Trạng thái tác vụ |
| POST `/api/v1/synchronization/run` | Đồng bộ một biến/vùng theo từng đợt |
| GET `/api/v1/synchronization/status/{sync_id}` | Trạng thái đồng bộ |
| GET `/api/v1/synchronization` | Tối đa 100 trạng thái gần nhất |
| GET `/api/v1/synchronization/config` | Cấu hình công khai, ngày khả dụng ước tính |

Tham số đầy đủ và schema phản hồi xem trong Swagger. Các API chuỗi thời gian,
thống kê, trend, heatmap và map dùng `variable`, `latitude`, `longitude`, `start`, `end`.
Anomaly thêm `baseline_start` và `baseline_end`. Compare dùng
`period_a_start`, `period_a_end`, `period_b_start`, `period_b_end` thay cho start/end.

Ví dụ Python (dùng `params` để encode dấu `+` đúng cách):

```python
import requests

params = {
    "variable": "temperature",
    "latitude": 10.8231,
    "longitude": 106.6297,
    "start": "2025-02-01T00:00:00+07:00",
    "end": "2025-02-02T23:00:00+07:00",
}
response = requests.get(
    "http://127.0.0.1:8000/api/v1/climate/timeseries",
    params=params, timeout=180,
)
response.raise_for_status()
payload = response.json()  # unit=°C, 48 hourly points if complete
```

## Cách Agent sử dụng

1. Xác định biến, vị trí, khoảng ngày và múi giờ từ câu hỏi.
2. Gọi API phù hợp, kiểm tra lỗi trước khi đọc kết quả.
3. Trả lời từ số liệu API; luôn nêu vị trí, khoảng ngày và đơn vị.
4. Không tự tạo số liệu khi API lỗi hoặc thiếu dữ liệu.

`trend` trong một ngày không chứng minh xu hướng khí hậu dài hạn.
`anomaly` hiện so với trung bình khoảng nền được chọn, không tự tính chuẩn khí hậu
theo từng tháng/ngày. Heatmap hiện dùng trung bình, kể cả mưa; không diễn giải
giá trị đó thành tổng lượng mưa tháng. Tìm cực trị kèm thời điểm có thể tạm lấy từ
timeseries và xử lý xác định trong công cụ của Agent, không để LLM đoán.

## Thời gian chờ và lỗi

Lần đầu thiếu dữ liệu có thể chờ CDS lâu. Có thể tạo job tải trước rồi theo dõi trạng thái.
HTTP 202 chỉ là nhận tác vụ; phải chờ `completed` mới coi là thành công.
Ngày tải vùng/đồng bộ là ngày UTC; muốn trọn ngày Việt Nam phải bao phủ ngày UTC trước đó.

- 400: biến không được hỗ trợ.
- 422: tham số/ngày không hợp lệ hoặc phân tích không đủ dữ liệu.
- 503: thiếu cấu hình CDS.
- 502: lỗi CDS, đọc file, nhập SQLite hoặc dữ liệu còn thiếu.

Đọc trường `detail`; không trả lỗi kỹ thuật như một kết luận thời tiết.
Không retry lỗi cấu hình/validation vô hạn. Các bản ghi SQLite đã lưu thành công
được tái sử dụng cho truy vấn sau và chỉ tải các khoảng giờ còn thiếu.
