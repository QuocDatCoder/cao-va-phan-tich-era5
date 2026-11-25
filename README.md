# Thu thập và phân tích dữ liệu khí tượng ERA5

Ứng dụng đồ án gồm backend FastAPI thu thập dữ liệu ERA5 từ Copernicus Climate Data Store (CDS), lưu trữ SQLite và cung cấp API phân tích; frontend Streamlit hiển thị biểu đồ và kết quả. Nhóm AI Agent tích hợp qua HTTP API của backend.

## Cập nhật đợt 1

**Luồng giao diện mới:** mỗi lần thu thập tạo một bộ dữ liệu riêng có địa điểm,
khoảng ngày gốc, nguồn, điểm lưới, số liệu và cảnh báo. Trong **Dữ liệu đã lưu**,
tìm và chọn bộ; trong **Biểu đồ**, chọn chính bộ đó để vẽ. Các màn hình này đọc
SQLite qua `/api/v1/collections/...`, không tự tải CDS. Các dòng đã lưu trước khi
có liên kết bộ dữ liệu vẫn nằm trong mục **Dữ liệu cũ**, không bị xóa hoặc gán
nguồn gốc không có chứng cứ. Xem [luồng và mô hình lưu trữ hiện hành](docs/DOT_3_GIAO_DIEN_QUY_TRINH.md).

Các mục dưới đây bao gồm API/cơ chế tương thích cũ; khả năng tự tải của API cũ
không áp dụng cho màn hình chọn bộ dữ liệu mới.

Dashboard hiện chọn **Đất liền (điểm)** / **ERA5 chung hỗ trợ cả biển (vùng)** / **Mock**, đọc dữ liệu qua `/api/v1/stored/climate/...`. Các API này chỉ đọc SQLite, trả độ phủ và khoảng thiếu; không tự tải CDS. Bấm **Bắt đầu thu thập** để tạo tác vụ riêng. Nguồn/dataset được lưu cùng tác vụ; đổi nguồn không sửa `.env`.

API `/api/v1/climate/...` và synchronization bên dưới là luồng tương thích cũ, vẫn có thể tự tải. Dashboard mới không bật theo dõi/tự đồng bộ; hướng dẫn đồng bộ phía sau áp dụng cho API/scheduler cũ. Xem [hợp đồng, giới hạn và cách chạy thử đợt 1](docs/DOT_1_THU_THAP_VA_TRUY_VAN.md).

Dataset hiện tại: `reanalysis-era5-single-levels`. Chế độ `mock` cung cấp dữ liệu giả lập để chạy thử khi chưa cấu hình CDS. Phần AI Agent do nhóm AI triển khai và tích hợp riêng.

## Chức năng hiện có

- Truy vấn theo biến, tọa độ và khoảng thời gian; dùng lại dữ liệu đã lưu và tải các khoảng giờ còn thiếu.
- Tải dữ liệu theo vùng bằng tác vụ nền, theo dõi trạng thái và nhập SQLite.
- Hỗ trợ nhiệt độ, điểm sương, lượng mưa, thành phần gió u/v, tốc độ/hướng gió và bức xạ mặt trời.
- Thống kê, xu hướng tuyến tính, độ lệch so với khoảng nền và so sánh hai giai đoạn.
- Tổng hợp giờ/ngày/tháng/năm, kèm số quan sát, độ phủ và thông tin kỳ chưa trọn.
- Dashboard chuỗi thời gian, heatmap tháng/năm và hiển thị vị trí truy vấn trên bản đồ.
- Đồng bộ với dữ liệu ERA5 mới khả dụng, làm mới dashboard khi tác vụ hoàn tất và tùy chọn theo dõi khoảng ngày mới nhất.

ERA5 có độ trễ công bố. Đồng bộ cập nhật theo dữ liệu mới khả dụng trên CDS, không cung cấp quan trắc trực tiếp từng giây.

## Kiến trúc và lưu trữ

```text
Streamlit → HTTP API FastAPI → Service phân tích → Provider ERA5 / Mock
                                                   ↓
                                            CDS + SQLite + NetCDF
```

```text
backend/app/
  api/                    Endpoint HTTP
  core/                   Cấu hình backend
  domain/                 Schema và quy tắc dữ liệu
  providers/              Kết nối ERA5 và dữ liệu giả lập
  services/               Phân tích, tải dữ liệu và đồng bộ
  storage/                Dữ liệu và metadata SQLite
frontend/                 Dashboard, API client và biểu đồ
docs/                     Tài liệu provider và bàn giao API cho AI
tests/                    Bộ kiểm thử backend/frontend
data/
  cache/auth.sqlite3      Tài khoản và phiên đăng nhập
  users/<id>/raw/era5/    NetCDF riêng của từng tài khoản
  users/<id>/cache/       SQLite khí hậu và tác vụ riêng của từng tài khoản
app.py                    Điểm chạy dashboard Streamlit
requirements.txt          Thư viện Python
.env.example              Mẫu cấu hình, không chứa key thật
```

Thư mục `core/` ở gốc, `test_modules.py` và các script tạo dữ liệu mẫu thuộc pipeline cũ. Luồng tích hợp hiện tại dùng `backend/`, `frontend/` và HTTP API; nhóm AI sử dụng `docs/AI_API_HANDOFF.md`.

## Cài đặt trên Windows PowerShell

Yêu cầu Python **3.11 trở lên**. Mở terminal tại thư mục gốc repository:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Nếu đã có `.venv`, chỉ cần cập nhật thư viện bằng lệnh thứ hai. Các lệnh dưới đây sử dụng trực tiếp Python trong `.venv`, không cần kích hoạt môi trường.

### Chạy thử bằng dữ liệu giả lập

Terminal 1 — backend:

```powershell
$env:CLIMATE_PROVIDER="mock"
$env:ERA5_SYNC_ENABLED="false"
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
```

Terminal 2 — frontend, cũng mở tại thư mục gốc repository:

```powershell
$env:API_BASE_URL="http://127.0.0.1:8000"
.\.venv\Scripts\python.exe -m streamlit run app.py
```

### Đăng ký/đăng nhập bằng username và mật khẩu

Để tạo admin cố định, thiết lập `ADMIN_USERNAME` (mặc định `admin`) và `ADMIN_PASSWORD` (ít nhất 8 ký tự) trong `.env` trước khi khởi động backend. Tài khoản được tạo ở lần chạy đầu; mật khẩu đã đặt được giữ nguyên khi khởi động lại. Để đổi mật khẩu, cập nhật `ADMIN_PASSWORD` rồi khởi động lại. Đăng nhập bằng admin để mở mục **Quản trị** trên thanh bên, nơi có thể cấp/thu hồi quyền quản trị và khóa/mở các tài khoản khác.

Trang quản trị độc lập chạy tại `http://localhost:8502` khi khởi động bằng `run_all.bat` ở chế độ headless, không tự bật tab trình duyệt. Đăng nhập admin tại trang chính `http://localhost:8501` sẽ tự chuyển tới admin site bằng mã bàn giao một lần, không cần đăng nhập lần hai. Phiên admin được khôi phục sau F5 từ `sessionStorage` của tab và được xác thực lại với backend. Thanh bên admin có hai mục **Tổng quan** và **Quản lý tài khoản**; Tổng quan hiển thị số liệu hệ thống và lịch sử tác vụ thu thập, lọc theo tài khoản. Quản lý tài khoản cho phép khóa, phân quyền hoặc xóa tài khoản; xóa sẽ xóa cả dữ liệu riêng của tài khoản đó.

Tài khoản được lưu trong SQLite riêng tại `data/cache/auth.sqlite3`; mật khẩu
được băm Argon2id, không lưu dạng rõ. Phiên đăng nhập dùng bearer token ngẫu
nhiên, chỉ lưu bản băm trong DB và hết hạn sau 12 giờ. Các API dữ liệu yêu cầu
token hợp lệ. Đăng ký đang mở cho mọi người dùng. Mỗi tài khoản có thư mục dữ
liệu riêng tại `data/users/<id>/`, gồm cache khí hậu, metadata tác vụ và tệp
NetCDF; API, AI truy vấn dữ liệu, tải nền và đồng bộ đều sử dụng vùng này.

Các DB dùng chung cũ `data/cache/climate.sqlite3` và `data/cache/jobs.sqlite3`
được giữ nguyên làm lưu trữ, không tự xóa, chuyển đổi hay hiển thị cho tài khoản
nào. Tài khoản mới bắt đầu với vùng dữ liệu trống. Việc khôi phục dữ liệu cũ vào
một tài khoản chưa được thực hiện tự động.

1. Cài dependencies nếu chưa làm sau khi cập nhật `requirements.txt`:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
2. Tạo `.env` từ `.env.example` nếu chưa có; bật `AUTH_ENABLED=true`.
3. Chạy backend và frontend như hướng dẫn ở trên, mở dashboard, tạo username
   (3–32 ký tự chữ/số/`_`/`.`/`-`) và mật khẩu (8–128 ký tự), rồi đăng ký.
   Đăng ký thành công sẽ tự đăng nhập. Đăng xuất rồi thử đăng nhập lại để kiểm
   tra.

Test tự động cho API và giao diện đăng nhập:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_auth_api.py tests/test_frontend_auth.py -q
```

Khi triển khai công khai, cần HTTPS và cân nhắc giới hạn tốc độ đăng nhập/đăng
ký hoặc chỉ cho phép người dùng được mời.

Trợ lý AI mở bằng pet đám mây **ARE** ở góc phải dưới; menu ba chấm cho đổi diện mạo mây/nắng/mưa và tạo trò chuyện mới. Backend đọc `AI_API_KEY` từ `.env` cục bộ; cấu hình mẫu ở `.env.example`, mặc định `gpt-6-luna`/`low`. Chỉ gửi câu hỏi hoặc bấm gợi ý mới gọi API AI (có thể tính phí); Agent chỉ truy vấn dữ liệu đã lưu, không tự tải CDS. Xem [phạm vi, kiểm thử và so sánh model](docs/DOT_5_AGENT_VA_CHAT.md).

ARE hỗ trợ hội thoại phổ thông và giải thích dự án. Số liệu khí tượng lấy qua service DB-only.

Khi đang xem biểu đồ, có thể hỏi “Phân tích chuyên sâu biểu đồ đang xem”, “Đỉnh này ở thời điểm nào?” hoặc “Biến động và xu hướng thế nào?”. ARE đọc lại đúng chuỗi DB theo bộ lọc đã áp dụng, tính thống kê, cực trị kèm thời điểm, chênh lệch và hồi quy mô tả. Phân tích theo ngày/tháng dùng giá trị từng kỳ đang vẽ; không nhầm với cực trị từng giờ. Không hồi quy qua dữ liệu thiếu/kỳ chưa trọn, không hồi quy tuyến tính hướng gió, không suy diễn nguyên nhân/dự báo từ một chuỗi. Hiện hỗ trợ chuỗi thời gian, chuỗi tháng sau heatmap và thống kê tại một điểm; chưa phân tích phân bố bản đồ hoặc ảnh tải lên.

ARE có thể chuẩn bị thao tác tìm dữ liệu, xem dữ liệu và mở biểu đồ. Ví dụ: “Tìm dữ liệu Hà Nội”, hoặc khi đang xem một bộ: “Vẽ nhiệt độ theo ngày trong khoảng đang xem”. Bạn bấm nút trong chat để mở kết quả; thiếu tham số sẽ có form lựa chọn. ARE chỉ dùng bộ lọc đã áp dụng, không lấy các thay đổi chưa bấm vẽ. Xem [logic tương tác và kiểm thử](docs/DOT_5_ARE_TUONG_TAC_GIAO_DIEN.md).

F5 giữ lịch sử chat trên cùng trình duyệt. Chỉ nút **Làm mới** trong menu chat chủ động xóa lịch sử; nút làm mới dữ liệu ở trang chính không xóa chat. Tin cũ vẫn xem được qua **Xem tin trước**. F5 lúc đang chờ giữ câu hỏi và không tự gửi lại API.

| Địa chỉ | Công dụng |
|---|---|
| http://127.0.0.1:8501 | Dashboard |
| http://127.0.0.1:8000/docs | Swagger: tham số, schema và thử API |
| http://127.0.0.1:8000/health | Kiểm tra backend hoạt động |

Giữ cả hai terminal chạy. Dừng từng dịch vụ bằng `Ctrl+C`. Những lần sau chỉ cần chạy lại hai dịch vụ. `run_app.bat` hiện chỉ khởi động frontend; cần chạy backend riêng.

### Chạy với dữ liệu ERA5 thật

1. Có tài khoản CDS, lấy personal access token và chấp nhận điều khoản dataset `reanalysis-era5-single-levels` trên CDS.
2. Tạo `.env` tại thư mục gốc từ mẫu `.env.example` nếu chưa có. Điền các giá trị sau trong file cấu hình cục bộ:

```dotenv
CLIMATE_PROVIDER=era5
CDS_URL=https://cds.climate.copernicus.eu/api
CDS_API_KEY=<YOUR_CDS_PERSONAL_ACCESS_TOKEN>
ERA5_SYNC_ENABLED=false
```

3. Dừng backend. Nếu terminal đã đặt biến môi trường khi chạy mock, xóa các giá trị ghi đè trước khi khởi động lại:

```powershell
Remove-Item Env:CLIMATE_PROVIDER -ErrorAction SilentlyContinue
Remove-Item Env:CDS_URL -ErrorAction SilentlyContinue
Remove-Item Env:CDS_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:ERA5_SYNC_ENABLED -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
```

Backend đọc `.env` khi khởi động từ thư mục gốc; biến môi trường tiến trình được ưu tiên hơn file. Khi đã lưu cấu hình cục bộ, không cần nhập lại key mỗi lần chạy. Sau khi đổi cấu hình, khởi động lại backend. Frontend vẫn chạy theo lệnh ở terminal 2 phía trên.

Không commit `.env`, token thật hoặc dữ liệu tải về. `.env.example` chỉ chứa giá trị mẫu. Cấu hình `.cdsapirc` của các script cũ không thay thế yêu cầu `CDS_URL` và `CDS_API_KEY` của backend hiện tại.

## Quy ước dữ liệu

| Biến API thông dụng | Đơn vị phản hồi |
|---|---|
| `temperature`, `dewpoint` | °C |
| `precipitation` | mm mỗi quan sát giờ |
| `wind_u`, `wind_v`, `wind_speed` | m/s |
| `wind_direction` | Độ, hướng gió khí tượng |
| `solar_radiation` | MJ/m² mỗi giờ |

- Timestamp phản hồi có offset `+07:00`. Nên gửi thời gian có offset; thời gian không có offset được backend hiểu là UTC.
- Khoảng truy vấn gồm cả hai đầu nếu có quan sát đúng mốc giờ. Một ngày Việt Nam đầy đủ: `00:00:00+07:00` đến `23:00:00+07:00`.
- `location` trong phản hồi là tọa độ yêu cầu; dữ liệu lấy tại điểm lưới ERA5 gần nhất.
- Giới hạn truy vấn mặc định là 366 ngày đầy đủ theo giờ (`ERA5_MAX_QUERY_DAYS`). Mỗi lần tải CDS mặc định tối đa 7 ngày (`ERA5_MAX_DAYS_PER_REQUEST`); backend tự chia đợt.
- Tổng hợp `auto` lấy tổng mưa/bức xạ, trung bình các biến vô hướng và trung bình vòng tròn cho hướng gió. Kiểm tra `coverage`, `partial_period`, `complete` trước khi diễn giải kết quả cả tháng/năm.
- Aggregate mưa/bức xạ căn theo đầu giờ để cộng đúng kỳ lịch Việt Nam; timeseries giữ thời điểm hiệu lực ERA5. Xem chi tiết quy ước tích lũy trong tài liệu bàn giao.
- Hướng gió khi lặng gió không có giá trị. Trung bình vòng tròn có thể không xác định; Agent phải xử lý `null`. Trend/anomaly/compare tuyến tính không áp dụng cho hướng gió.

Ví dụ gọi API bằng Python, dùng `params` để mã hóa đúng dấu `+`:

```python
import requests

response = requests.get(
    "http://127.0.0.1:8000/api/v1/climate/timeseries",
    params={
        "variable": "temperature",
        "latitude": 10.8231,
        "longitude": 106.6297,
        "start": "2025-02-01T00:00:00+07:00",
        "end": "2025-02-01T23:00:00+07:00",
    },
    timeout=180,
)
response.raise_for_status()
payload = response.json()
```

Lần đầu tải CDS có thể chờ lâu hơn timeout ví dụ. Với khoảng dài, nên tạo tác vụ tải vùng trước rồi chờ `completed` để truy vấn dữ liệu đã lưu. Ngày tải vùng là ngày UTC; để bao phủ trọn ngày Việt Nam cần bao phủ cả ngày UTC trước đó.

## API và bàn giao cho nhóm AI

| Method | Đường dẫn | Chức năng |
|---|---|---|
| GET | `/api/v1/climate/timeseries` | Chuỗi quan sát giờ |
| GET | `/api/v1/climate/aggregate` | Tổng hợp giờ/ngày/tháng/năm |
| GET | `/api/v1/climate/statistics` | Thống kê |
| GET | `/api/v1/climate/trend` | Xu hướng tuyến tính trong khoảng chọn |
| GET | `/api/v1/climate/anomaly` | Độ lệch so với trung bình khoảng nền |
| GET | `/api/v1/climate/compare` | So sánh trung bình hai giai đoạn |
| GET | `/api/v1/climate/heatmap` | Tổng hoặc trung bình theo tháng/năm |
| GET | `/api/v1/climate/map` | Trung bình tại vị trí truy vấn |
| POST | `/api/v1/downloads` | Tạo tác vụ tải vùng |
| GET | `/api/v1/jobs/{job_id}` | Theo dõi tác vụ |
| POST | `/api/v1/synchronization/run` | Yêu cầu đồng bộ |
| GET | `/api/v1/synchronization/status/{sync_id}` | Theo dõi đồng bộ |
| GET | `/api/v1/synchronization` | Danh sách trạng thái đồng bộ |
| GET | `/api/v1/synchronization/config` | Cấu hình đồng bộ công khai |

Nhóm AI gọi HTTP API và xử lý lỗi, thiếu dữ liệu, đơn vị, múi giờ theo [tài liệu bàn giao AI](docs/AI_API_HANDOFF.md). Tham số và schema đầy đủ có trong Swagger. Địa chỉ `127.0.0.1` luôn trỏ tới máy đang chạy client; khi mỗi thành viên chạy trên máy riêng, cần chạy backend cục bộ hoặc thống nhất địa chỉ máy phục vụ backend.

## Đồng bộ và cập nhật dashboard

Scheduler backend mặc định tắt. Để chạy định kỳ, cấu hình trong `.env` rồi khởi động lại backend:

```dotenv
ERA5_SYNC_ENABLED=true
ERA5_SYNC_INTERVAL_HOURS=6
ERA5_SYNC_MAX_DAYS_PER_RUN=7
ERA5_SYNC_AVAILABILITY_LAG_DAYS=5
```

Biến, vùng và ngày bắt đầu của scheduler được cấu hình bằng nhóm `ERA5_SYNC_VARIABLE`, `ERA5_SYNC_AREA_*`, `ERA5_SYNC_START_DATE`. Kiểm tra cấu hình phù hợp trước khi bật; xem [tài liệu provider](docs/era5_provider.md). Ngày mới nhất hiển thị là ước tính từ độ trễ cấu hình.

Dashboard kiểm tra trạng thái tác vụ khoảng 15 giây khi trang đang mở và làm mới dữ liệu khi tác vụ hoàn tất. Tùy chọn theo dõi ngày mới nhất và đồng bộ tự động trên dashboard cần được bật riêng; khoảng ngày lịch sử đang chọn không tự chuyển nếu chưa bật theo dõi. HTTP `202` chỉ xác nhận tiếp nhận tác vụ, cần theo dõi đến `completed`.

## Kiểm thử và kiểm tra bàn giao

Chạy bộ kiểm thử offline từ thư mục gốc:

```powershell
$env:CLIMATE_PROVIDER="mock"
$env:ERA5_SYNC_ENABLED="false"
$env:RUN_CDS_INTEGRATION="0"
.\.venv\Scripts\python.exe -m pytest tests -q
```

Các kiểm thử offline dùng dữ liệu giả lập/file thử và không tải CDS thật. Kiểm thử tích hợp CDS được bỏ qua mặc định. Khi đã cấu hình tài khoản và muốn kiểm tra tải thật:

```powershell
$env:CLIMATE_PROVIDER="era5"
$env:RUN_CDS_INTEGRATION="1"
.\.venv\Scripts\python.exe -m pytest tests/integration -q
```

Kiểm thử tích hợp có thể tải dữ liệu và cần chờ CDS. Kết quả offline không thay thế việc kiểm tra các biến và luồng sử dụng với dữ liệu thật. Các lệnh trên chỉ đặt biến môi trường cho terminal hiện tại; khi chạy lại ứng dụng, chọn lại provider và cấu hình đồng bộ mong muốn.

Trước khi bàn giao, kiểm tra backend/frontend khởi động được trên máy thành viên khác, gọi thử API với múi giờ và đơn vị đúng, theo dõi tác vụ đến hoàn tất, và xác nhận không đưa key thật vào commit. Nhóm AI nên thử tích hợp bằng mock trước rồi xác nhận với ERA5 thật.

## Xử lý lỗi thường gặp và giới hạn

| Hiện tượng | Cách kiểm tra |
|---|---|
| Frontend không kết nối được API | Kiểm tra backend đang chạy và `API_BASE_URL` đúng địa chỉ/cổng. |
| HTTP 503 thiếu cấu hình CDS | Kiểm tra cấu hình cục bộ `CDS_URL`, `CDS_API_KEY`, biến môi trường ghi đè và khởi động lại backend. |
| HTTP 400 hoặc 422 | Đọc `detail`: biến, tọa độ, thời gian hoặc phép phân tích có thể không hợp lệ. |
| HTTP 502 | Đọc `detail` và log backend; kiểm tra CDS, file tải hoặc dữ liệu còn thiếu. |
| Tác vụ đang chạy lâu | Theo dõi trạng thái job/sync; CDS có thể xếp hàng yêu cầu. |

Trend trong một ngày chỉ mô tả hồi quy trong khoảng chọn, không chứng minh xu hướng khí hậu dài hạn. Anomaly hiện so với trung bình khoảng nền được chọn. Map hiển thị vị trí truy vấn, chưa phải bản đồ trường dữ liệu phủ toàn cầu. API cực trị kèm thời điểm chưa tách riêng; nhóm AI có thể xác định từ timeseries bằng xử lý số liệu.

Tài liệu chi tiết: [API cho AI Agent](docs/AI_API_HANDOFF.md) · [ERA5 provider, lưu trữ và đồng bộ](docs/era5_provider.md).

### Đợt 4: ổn định tải và kiểm tra demo

Đã bổ sung chạy job tuần tự trong một tiến trình, retry có giới hạn theo loại lỗi và phục hồi job gián đoạn mà không xóa phần đã nhập. Scheduler vẫn mặc định tắt. Xem [checklist và giới hạn đợt 4](docs/DOT_4_ON_DINH_TAI_VA_DEMO.md).

Kiểm tra dữ liệu đang có, không gửi CDS:

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/check_demo_data.py --require-source land
```

Hiện đã kiểm chứng bốn bộ ERA5-Land trên máy phát triển. **Chưa có bộ ERA5 thật đạt kiểm tra**, vì vậy chưa công bố nghiệm thu dữ liệu biển hoặc hoàn thành toàn bộ minh chứng đợt 4. Các bộ dữ liệu thật không được đưa vào Git.

Kiểm thử toàn bộ và khởi động backend/frontend trên DB riêng, chặn CDS và credential files:

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/baseline_offline.py
.\.venv\Scripts\python.exe -X utf8 scripts/browser_review.py
```

Lệnh Chromium cần Playwright và trình duyệt Chromium đã cài. Script dùng mock qua backend thử riêng, không đưa lại lựa chọn Demo vào giao diện sản phẩm. Log, CSV và ảnh nằm trong `output/`, được Git bỏ qua.

Chế độ tải thật của script audit đòi hỏi đồng thời `--allow-live` và `--live-request`. Nhận HTTP 202 chỉ xác nhận tiếp nhận job, không phải xác nhận tải thành công.
