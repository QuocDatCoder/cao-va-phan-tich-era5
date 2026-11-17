# Bàn giao API dữ liệu cho nhóm AI Agent

## Hợp đồng cho Agent đợt 5 — ưu tiên tài liệu này

Agent mới chỉ dùng **SQLite-only theo collection**, không dùng API `/api/v1/climate/...` hoặc synchronization để tự tải phần thiếu. Các phần legacy phía dưới mô tả API cũ và không phải danh sách tool được phép của Agent mới.

| GET | Công dụng |
|---|---|
| `/api/v1/collections` | Tìm bộ đã lưu (`search`, `limit`, `offset`) |
| `/api/v1/collections/{id}` | Metadata, nguồn, biến, điểm lưới, summary và artifact |
| `/api/v1/collections/{id}/quality` | Cảnh báo có phân trang |
| `/api/v1/collections/{id}/timeseries` | Chuỗi giờ đã lưu |
| `/api/v1/collections/{id}/statistics` | Thống kê phần đã lưu |
| `/api/v1/collections/{id}/aggregate` | Tổng hợp theo chu kỳ |
| `/api/v1/collections/{id}/trend` | Trend khi đủ điều kiện |
| `/api/v1/collections/{id}/heatmap` | Tổng hợp tháng kèm cờ kỳ chưa trọn |
| `/api/v1/collections/{id}/map` | Giá trị tại đúng điểm lưới được chọn |

Mỗi tool query nhận collection ID, biến thuộc bộ, điểm lưới thực và khoảng nằm trong yêu cầu gốc. Kiểm tra `coverage`, `complete`, `missing_ranges`, `undefined_count`, `collection_quality` và `partial_period` trước khi diễn giải. `statistics` của mưa là thống kê các quan sát, không tự hiểu mean thành tổng mưa. Tổng mưa/bức xạ cần aggregate, kiểm tra giờ biên và coverage. Dataset ERA5 hỗ trợ cả đất liền và biển, không phải bộ lọc chỉ biển. Không trộn các bộ vì trùng tọa độ.

Chưa có anomaly/compare DB-only theo collection. Không cho mô hình viết SQL, gọi command hoặc tạo/hủy/xóa job tự do. Khi thiếu dữ liệu, giải thích và dẫn người dùng sang Thu thập; không tự gọi CDS. Adapter mô hình và danh sách tool cụ thể triển khai ở đợt 5, hiện chưa có chatbot hoàn chỉnh.

## Phạm vi phiên bản tích hợp

Nhóm AI có thể bắt đầu tích hợp các chức năng nhiệt độ, điểm sương, lượng mưa, gió và bức xạ,
thống kê, tìm xu hướng tuyến tính trong khoảng chọn, anomaly và so sánh.
Đây là phiên bản API nền tảng; chưa phải toàn bộ tính năng đồ án.
Đã hỗ trợ tổng hợp giờ/ngày/tháng/năm và tự chia đợt tải cho truy vấn dài.
API cực trị kèm thời điểm vẫn chưa tách riêng; có thể xác định từ timeseries.

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

`mock` tạo dữ liệu giả lập cho các biến được liệt kê dưới đây để kiểm tra tích hợp.
Để dùng ERA5 thật, dừng backend và đặt trong cùng terminal:

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
- Thành phần gió: `wind_u`/`u10`/`10m_u_component_of_wind` và
  `wind_v`/`v10`/`10m_v_component_of_wind`, đơn vị m/s, có thể âm.
- `wind_speed`: tốc độ gió 10m, m/s; `wind_direction`: hướng gió khí tượng, độ (°).
- `solar_radiation`/`ssrd`/`surface_solar_radiation_downwards`: MJ/m² cho từng giờ,
  là năng lượng, không phải công suất W/m². File gốc ERA5 vẫn giữ J/m².
- Timestamp phản hồi: ISO 8601 có `+07:00`. Không cộng thêm 7 giờ hay trừ thêm 273.15.
- Truy vấn nên dùng datetime có offset. Nếu không có offset, backend hiểu là UTC.
- Khoảng truy vấn gồm cả hai đầu nếu có quan sát đúng mốc giờ.
- `location` trong phản hồi hiện là tọa độ yêu cầu; giá trị lấy tại điểm lưới ERA5 gần nhất.
- SQLite giữ UTC, nhiệt độ K và mưa mm; API chuyển đổi ở service layer.
- Giới hạn mặc định: tối đa 366 ngày đầy đủ theo giờ (bao gồm năm nhuận),
  cấu hình bằng `ERA5_MAX_QUERY_DAYS`; mỗi lần tải CDS vẫn tối đa
  `ERA5_MAX_DAYS_PER_REQUEST` (mặc định 7 ngày). Backend tự chia đợt và dùng lại SQLite.
- Ngày mới nhất có thể tải được ước tính từ cấu hình độ trễ, không phải dữ liệu quan trắc trực tiếp.

### Gió và bức xạ

Tốc độ gió được tính theo từng giờ bằng `sqrt(u²+v²)`. Hướng gió dùng
`(180 + atan2(u,v)*180/pi) mod 360`, chỉ hướng gió thổi từ đâu:
Bắc 0°, Đông 90°, Nam 180°, Tây 270°. Dữ liệu u/v phải khớp thời gian.
Giờ lặng gió có tốc độ 0 nhưng không có hướng; các giờ đó được bỏ qua trong
chuỗi hướng gió, nên số điểm hướng gió có thể ít hơn số điểm tốc độ gió.

Statistics cho hướng gió trả trung bình và độ lệch chuẩn vòng tròn không trọng số;
`min`/`max` là null. Với các hướng đối nhau không có trung bình xác định,
`mean` và `std` cũng là null. Agent phải xử lý null, không diễn giải thành 0°.
Trend/anomaly/compare tuyến tính cho `wind_direction` trả 422 có giải thích.
Heatmap/map dùng trung bình vòng tròn và trả 422 nếu không xác định được.

Tải vùng/đồng bộ với `wind_speed` hoặc `wind_direction` yêu cầu cả u và v trong
cùng file NetCDF, nhập các thành phần vào SQLite trước khi báo completed.
Hai đại lượng dẫn xuất dùng lại u/v đã lưu, không lưu bản tính trùng vào SQLite.
Mỗi thành phần được nhập bằng một transaction; nếu thành phần sau bị lỗi,
job vẫn failed nhưng thành phần trước đã nhập thành công được giữ lại.

Quy ước bức xạ theo giờ ở đây áp dụng cho dataset hiện tại
`reanalysis-era5-single-levels`; không tự dùng cùng quy tắc tích lũy cho mọi sản phẩm ERA5-Land.
Tham khảo [công thức gió ECMWF](https://confluence.ecmwf.int/pages/viewpage.action?pageId=690456538)
và [tài liệu dữ liệu ERA5](https://confluence.ecmwf.int/pages/viewpage.action?pageId=414588701).

## Các công cụ HTTP

| Method và đường dẫn | Công dụng |
|---|---|
| GET `/api/v1/climate/timeseries` | Chuỗi thời gian |
| GET `/api/v1/climate/aggregate` | Tổng hợp giờ/ngày/tháng/năm, kèm độ phủ dữ liệu |
| GET `/api/v1/climate/statistics` | Mean, min, max, std tổng thể và count |
| GET `/api/v1/climate/trend` | Hệ số dốc theo chỉ số quan sát và chiều hướng |
| GET `/api/v1/climate/anomaly` | Giá trị trừ trung bình khoảng nền |
| GET `/api/v1/climate/compare` | Trung bình hai giai đoạn, chênh lệch A−B |
| GET `/api/v1/climate/heatmap` | Tổng mưa/bức xạ hoặc trung bình biến khác theo tháng/năm |
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

## Tổng hợp theo thời gian

Gọi `/api/v1/climate/aggregate` với các tham số vị trí/thời gian như timeseries,
thêm `frequency=hourly|daily|monthly|yearly` và
`aggregation=auto|mean|min|max|sum` (mặc định daily/auto).

Ví dụ tổng mưa từng ngày của tháng 02/2025:

```python
params.update({
    "variable": "precipitation",
    "start": "2025-02-01T00:00:00+07:00",
    "end": "2025-02-28T23:00:00+07:00",
    "frequency": "daily",
    "aggregation": "auto",
})
response = requests.get("http://127.0.0.1:8000/api/v1/climate/aggregate", params=params, timeout=180)
response.raise_for_status()
daily = response.json()  # 28 kỳ nếu đủ dữ liệu
```

- Auto: mưa/bức xạ lấy tổng; nhiệt độ, điểm sương, tốc độ/thành phần gió lấy trung bình;
  hướng gió lấy trung bình vòng tròn.
- Sum chỉ áp dụng cho mưa và bức xạ. Hướng gió chỉ chấp nhận auto/mean (đều là circular_mean).
- Mỗi kỳ có `time` đầu kỳ và `period_end` cuối kỳ loại trừ, theo UTC+7;
  `value` là kết quả, `min`/`max` là cực trị quan sát giờ trong kỳ, không kèm thời điểm.
- `count` là số quan sát dùng; `expected_count` là số giờ yêu cầu nằm trong kỳ;
  `coverage=count/expected_count`; `partial_period=true` khi chỉ chọn một phần kỳ lịch.
- `complete=true` khi đủ quan sát và bao phủ trọn kỳ lịch. Giá trị tổng hợp vẫn có thể null
  nếu không xác định được hướng gió trung bình. Giờ lặng gió không có hướng nên làm giảm count.
- Nếu chọn hai ngày rồi tổng hợp theo tháng, kết quả là kỳ tháng chưa trọn,
  không được diễn giải thành tổng/trung bình cả tháng.
- Các kỳ theo lịch Việt Nam, có xử lý đúng tháng 28/29/30/31 ngày và năm nhuận.

**Căn thời gian mưa/bức xạ:** trong ERA5 single-levels, mỗi giá trị tích lũy tại T
thuộc giờ kết thúc ở T. Aggregate dùng `time_basis=hour_start`, tự lấy nguồn lệch +1 giờ
rồi gán về đầu giờ để cộng đúng ngày Việt Nam. Ví dụ ngày 01/02 cần các giờ kết thúc
01:00 ngày 01/02 đến 00:00 ngày 02/02. Timeseries cũ giữ nguyên thời điểm ERA5;
không tự cộng mưa theo ngày bằng cách nhóm các timestamp 00:00–23:00 của timeseries.

Tab Chuỗi thời gian trên dashboard áp dụng lựa chọn chu kỳ/phép tổng hợp.
Thống kê tổng quan, trend, anomaly và compare vẫn dùng quan sát giờ trong khoảng chọn.

## Tải trước cho khoảng dài

Khoảng dài có thể phải chờ nhiều yêu cầu CDS; timeout 180 giây không bảo đảm đủ
cho lần tải đầu. Nên dùng POST `/api/v1/downloads` để tải vùng nhỏ trước, đợi job
completed rồi gọi timeseries/aggregate. Dữ liệu đã lưu được giữ nếu một đợt sau bị lỗi.
Tải vùng dài tự chia thành nhiều NetCDF; `file_path` của job dài trỏ tới
file `.manifest.json` liệt kê các NetCDF bằng đường dẫn tương đối trong data_dir.
Job ngắn vẫn trả đường dẫn NetCDF như trước. Agent không cần đọc manifest để truy vấn API.
Đồng bộ định kỳ vẫn chạy từng đợt theo `ERA5_SYNC_MAX_DAYS_PER_RUN`, không tự tải
toàn bộ một năm chỉ trong một lần gọi sync.

## Cách Agent sử dụng

1. Xác định biến, vị trí, khoảng ngày và múi giờ từ câu hỏi.
2. Gọi API phù hợp, kiểm tra lỗi trước khi đọc kết quả.
3. Trả lời từ số liệu API; luôn nêu vị trí, khoảng ngày và đơn vị.
4. Không tự tạo số liệu khi API lỗi hoặc thiếu dữ liệu.

`trend` trong một ngày không chứng minh xu hướng khí hậu dài hạn.
`anomaly` hiện so với trung bình khoảng nền được chọn, không tự tính chuẩn khí hậu
theo từng tháng/ngày. Heatmap trả tổng mưa/bức xạ và trung bình các biến khác,
nhưng chỉ trong khoảng ngày được chọn; muốn biết kỳ có trọn tháng hay không,
dùng metadata của aggregate. Tìm cực trị kèm thời điểm có thể tạm lấy từ
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
