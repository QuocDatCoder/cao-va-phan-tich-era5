# ERA5 · Thu thập và phân tích dữ liệu khí tượng

Ứng dụng web phục vụ thu thập và phân tích dữ liệu khí tượng lịch sử từ Copernicus Climate Data Store (CDS). Người dùng có thể chọn địa điểm và thời gian, theo dõi tác vụ tải, kiểm tra bộ dữ liệu đã lưu, vẽ biểu đồ và xuất CSV để tiếp tục phân tích.

Ví dụ, để khảo sát nhiệt độ tại Hà Nội trong một tháng, người dùng tạo tác vụ ERA5-Land cho một điểm, kiểm tra độ phủ sau khi tác vụ hoàn tất, rồi vẽ chuỗi nhiệt độ theo ngày. Có thể tải kết quả đã lọc về để dùng lại trong báo cáo.

**Chạy thử trên Windows:** cài môi trường theo mục 3, chạy `.\run_all.bat mock` và mở [dashboard](http://localhost:8501). Chế độ mock dùng dữ liệu giả lập để thử luồng thu thập, xem biểu đồ và xuất CSV.

- [Sản phẩm](#1-sản-phẩm)
- [Mã nguồn](#2-mã-nguồn)
- [Hướng dẫn cho nhà phát triển](#3-hướng-dẫn-cho-nhà-phát-triển)
- [Hướng dẫn sử dụng](#4-hướng-dẫn-sử-dụng)

## 1. Sản phẩm

| Chức năng | Kết quả |
| --- | --- |
| Thu thập | Tạo tác vụ ERA5-Land theo điểm hoặc ERA5 theo vùng, theo dõi tiến độ và lịch sử. |
| Kho dữ liệu | Tìm bộ đã lưu, xem nguồn, điểm lưới, độ phủ và cảnh báo, tải CSV. |
| Biểu đồ | Phân tích bộ đã lưu qua sáu chế độ trình bày. |
| Trợ lý ARE | Hướng dẫn thao tác, tra cứu bộ đã lưu và phân tích kết quả. Người dùng xác nhận trước khi gửi tác vụ thu thập. |
| Quản trị | Xem thống kê hệ thống, lọc lịch sử theo người dùng, cấp quyền và quản lý tài khoản. |

Sáu biến khí tượng gồm nhiệt độ, điểm sương, lượng mưa, tốc độ gió, hướng gió và bức xạ mặt trời. Giao diện hiển thị giờ Việt Nam (UTC+7). Kiểm tra độ phủ và cảnh báo trước khi dùng kết quả.

### Kịch bản demo

| Thao tác | Kết quả cần quan sát |
| --- | --- |
| Khởi động bằng chế độ mock và đăng nhập | Dashboard mở được và có thể tạo tác vụ bằng dữ liệu giả lập. |
| Tạo một tác vụ cho một điểm và khoảng ngày ngắn | Lịch sử hiển thị trạng thái xử lý. Khi tác vụ hoàn tất, bộ dữ liệu xuất hiện trong Kho dữ liệu. |
| Mở bộ dữ liệu và chọn nhiệt độ | Giao diện hiển thị điểm lưới, khoảng thời gian, đơn vị và độ phủ. |
| Vẽ chuỗi thời gian và tải CSV | Đối chiếu biến, khoảng xem và các giá trị giữa bảng kết quả, biểu đồ và tệp xuất. |
| Hỏi ARE về bộ đang xem, nếu đã cấu hình AI | ARE dùng ngữ cảnh của bộ dữ liệu và có thể dẫn người dùng tới màn hình liên quan. |

Dữ liệu ERA5 là dữ liệu tái phân tích. Với các ngày gần hiện tại, khả năng thu thập phụ thuộc vào thời điểm CDS công bố dữ liệu. Bản demo mock dùng để kiểm tra quy trình ứng dụng.

## 2. Mã nguồn

```
Dashboard và Admin (Streamlit) → FastAPI → Dịch vụ thu thập và phân tích
                                              ├─ Copernicus CDS
                                              └─ SQLite và tệp nguồn cục bộ
```

| Đường dẫn | Vai trò |
| --- | --- |
| [app.py](app.py), [admin_app.py](admin_app.py) | Điểm chạy dashboard và trang quản trị |
| [frontend/](frontend/) | Giao diện Streamlit, HTTP client và biểu đồ Plotly |
| [backend/app/api/](backend/app/api/) | Tuyến FastAPI và xác thực |
| [backend/app/domain/](backend/app/domain/) | Kiểu dữ liệu và quy tắc khí tượng |
| [backend/app/services/](backend/app/services/) | Tác vụ tải, truy vấn và phân tích |
| [backend/app/providers/](backend/app/providers/) | Provider ERA5, ERA5-Land và mock |
| [backend/app/storage/](backend/app/storage/) | Lưu trữ SQLite |
| [backend/app/ai/](backend/app/ai/) | Điều phối ARE và công cụ đọc dữ liệu |
| [tests/](tests/), [scripts/](scripts/) | Kiểm thử và công cụ kiểm tra |

Luồng chính là **Thu thập → kiểm tra và lưu → chọn bộ dữ liệu → phân tích**. Kho dữ liệu, Biểu đồ và công cụ tra cứu của ARE đọc dữ liệu đã lưu. Khi bật xác thực, dữ liệu được lưu riêng theo từng tài khoản.

### Luồng xử lý và các API chính

1. Dashboard gửi yêu cầu thu thập, gồm nguồn, tọa độ và khoảng thời gian. FastAPI tạo tác vụ và trả mã để giao diện tra cứu trạng thái.
2. Dịch vụ tải gọi provider phù hợp. Dữ liệu nguồn được xử lý, kiểm tra độ phủ và lưu cùng siêu dữ liệu bộ sưu tập.
3. Khi người dùng mở bộ sưu tập hoặc vẽ biểu đồ, ứng dụng truy vấn dữ liệu đã lưu theo biến, điểm lưới và khoảng thời gian. Kết quả có thể kèm cảnh báo thiếu dữ liệu.
4. ARE dùng các công cụ đọc dữ liệu đã lưu để phân tích. Khi cần thu thập thêm, người dùng kiểm tra thông tin và xác nhận tác vụ trên giao diện.

| API | Vai trò |
| --- | --- |
| `POST /api/v1/downloads` | Tạo tác vụ thu thập. |
| `GET /api/v1/jobs/{job_id}` | Xem trạng thái và kết quả của tác vụ. |
| `GET /api/v1/collections` | Liệt kê các bộ dữ liệu đã lưu. |
| `GET /api/v1/collections/{collection_id}/quality` | Đọc thông tin chất lượng của bộ dữ liệu. |
| `GET /api/v1/collections/{collection_id}/{operation}` | Truy vấn phân tích trên bộ đã lưu. |
| `POST /api/v1/agent/chat` | Gửi câu hỏi cho ARE. |

## 3. Hướng dẫn cho nhà phát triển

### Cài đặt và cấu hình

Cần Git và Python 3.11 trở lên. Mở PowerShell tại thư mục dự án.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

| Nhóm cấu hình | Biến cần xem |
| --- | --- |
| CDS | CLIMATE_PROVIDER, CDS_URL, CDS_API_KEY |
| Đăng nhập và quản trị | AUTH_ENABLED, ADMIN_USERNAME, ADMIN_PASSWORD |
| ARE | AI_BASE_URL, AI_MODEL, AI_API_KEY |
| Địa chỉ backend và lưu trữ | API_BASE_URL, DATA_DIR, AUTH_DB_PATH, JOB_METADATA_DB_PATH |

Điền thông tin xác thực trong tệp .env cục bộ. Mẫu cấu hình nằm trong [.env.example](.env.example).

### Chạy ứng dụng

```powershell
.\run_all.bat
```

| Dịch vụ | Địa chỉ |
| --- | --- |
| Dashboard | [localhost:8501](http://localhost:8501) |
| Quản trị | [localhost:8502](http://localhost:8502) |
| Tài liệu API | [localhost:8000/docs](http://localhost:8000/docs) |
| Trạng thái backend | [localhost:8000/health](http://localhost:8000/health) |

### Chạy từng dịch vụ để gỡ lỗi

Mở các cửa sổ PowerShell tại thư mục dự án. Khởi động backend trước và kiểm tra `/health`, sau đó chạy dashboard. Chạy thêm trang quản trị khi cần kiểm tra luồng admin.

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py --server.port 8501
```

```powershell
.\.venv\Scripts\python.exe -m streamlit run admin_app.py --server.port 8502
```

Dùng chế độ mock khi phát triển giao diện mà chưa có khóa CDS. Để tải dữ liệu thật, điền khóa CDS vào `.env` và kiểm tra quyền truy cập bộ dữ liệu bằng tài khoản CDS. Khởi động lại các dịch vụ sau khi đổi cấu hình. Với trang quản trị, đặt `ADMIN_PASSWORD` trong `.env` trước khi khởi tạo tài khoản admin.

### Sửa mã và kiểm thử

| Khi cần sửa | Bắt đầu từ |
| --- | --- |
| Form thu thập và trạng thái tác vụ | [frontend/workflow.py](frontend/workflow.py) và [dịch vụ tải dữ liệu](backend/app/services/download_job_service.py) |
| Bộ lọc, biểu đồ và xuất CSV | [frontend/library.py](frontend/library.py), [charts.py](frontend/components/charts.py) và [climate_views.py](frontend/components/climate_views.py) |
| Hành vi của ARE | [frontend/ai_chat.py](frontend/ai_chat.py) và [backend/app/ai/](backend/app/ai/) |
| Trang quản trị | [frontend/admin_site.py](frontend/admin_site.py) |

1. Tạo nhánh từ main đã cập nhật.
2. Khi đổi một luồng giao diện, kiểm tra cả HTTP client và tuyến API tương ứng. Khi bổ sung phân tích, giữ đúng đơn vị, múi giờ, điểm lưới và độ phủ.
3. Chạy kiểm thử offline rồi mở pull request vào main. Ghi rõ cách tái hiện và kết quả kiểm thử trong PR.

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q -m 'not integration'
.\.venv\Scripts\python.exe -X utf8 scripts/baseline_offline.py
```

Tài liệu chi tiết về [luồng dữ liệu](docs/DOT_3_GIAO_DIEN_QUY_TRINH.md), [provider ERA5](docs/era5_provider.md) và [API cho ARE](docs/AI_API_HANDOFF.md).

## 4. Hướng dẫn sử dụng

### Đăng nhập và thu thập

1. Mở dashboard và đăng ký hoặc đăng nhập.
2. Vào **Thu thập**. Chọn **ERA5-Land** cho một điểm trên đất liền hoặc **ERA5** cho một vùng tọa độ.
3. Nhập địa điểm hoặc tọa độ, chọn khoảng ngày và bấm **Bắt đầu thu thập**. Mỗi tác vụ lấy cả sáu biến khí tượng.
4. Theo dõi **Tác vụ thu thập**. Khi tác vụ hoàn tất, mở **Kho dữ liệu** để xem dữ liệu đã lưu và cảnh báo. CDS có thể xếp hàng hoặc chưa công bố dữ liệu cho ngày gần nhất.

### Xem, vẽ và tải dữ liệu

Trong **Kho dữ liệu**, tìm theo địa điểm và bấm **Xem dữ liệu**. Mở **Bộ lọc dữ liệu** để chọn biến, điểm lưới và khoảng thời gian. Bấm **Tải CSV** để xuất kết quả hoặc **Vẽ biểu đồ** để phân tích.

| Biểu đồ | Cách sử dụng |
| --- | --- |
| Chuỗi thời gian | Xem biến theo giờ, ngày, tháng hoặc năm. |
| Hoa gió | Xem hướng gió thổi từ đâu và phân bố theo nhóm tốc độ. Cần hai biến gió cùng thời điểm. |
| Heatmap dị thường nhiệt độ | Chọn giai đoạn nền tách biệt với khoảng xem. Cần dữ liệu đầy đủ ở cùng tháng của hai giai đoạn. |
| Khí hậu tháng | So sánh tổng mưa và nhiệt độ trung bình trong các tháng đủ dữ liệu. |
| Bản đồ thời tiết | Chọn một biến để xem trung bình theo thời gian tại điểm hoặc các điểm lưới. Với vùng lớn, bản đồ lấy mẫu tối đa 64 điểm. |
| Thống kê và xu hướng | Xem số quan sát, thống kê và hệ số dốc khi chuỗi đủ điều kiện. |

### Đọc kết quả phân tích

- Kiểm tra tọa độ điểm lưới thực tế, đơn vị của biến và khoảng thời gian sau khi áp dụng bộ lọc.
- Khi độ phủ thấp, mở báo cáo chất lượng để xem phần thiếu trước khi diễn giải giá trị trung bình hoặc xu hướng. Đối với những biểu đồ yêu cầu đủ tháng, chọn khoảng thời gian bao trọn các tháng cần phân tích.
- Khi so sánh dị thường nhiệt độ, ghi lại cả khoảng đang xem và khoảng nền để người đọc có thể tái hiện kết quả.

### Dùng ARE và trang quản trị

Bấm **ARE** trên thanh đầu trang để hỏi về bộ đã lưu, cảnh báo hoặc biểu đồ. Tính năng dùng mô hình cần cấu hình AI ở backend. Ví dụ, sau khi mở biểu đồ nhiệt độ, có thể hỏi “Biểu đồ này cho thấy nhiệt độ thay đổi như thế nào?” hoặc “Bộ dữ liệu này có thiếu thời điểm nào không?”. Nếu ARE đề nghị gửi tác vụ mới, kiểm tra nguồn, vị trí và khoảng ngày trước khi xác nhận.

### Xử lý sự cố khi sử dụng

| Hiện tượng | Cách kiểm tra |
| --- | --- |
| Dashboard báo lỗi kết nối | Mở `/health` trên backend, sau đó kiểm tra `API_BASE_URL` và tiến trình Uvicorn. |
| Tác vụ chờ lâu hoặc thất bại | Xem chi tiết tác vụ trong mục **Thu thập**. Với dữ liệu thật, đối chiếu khóa CDS, phạm vi tọa độ và ngày đã được công bố. |
| Bộ dữ liệu có nhưng biểu đồ trống | Kiểm tra biến, điểm lưới và khoảng thời gian đã lưu. Với hoa gió, cần cả tốc độ và hướng gió ở các thời điểm trùng nhau. |
| ARE không phản hồi | Kiểm tra cấu hình AI và trạng thái backend. Có thể tiếp tục thu thập, xem và xuất dữ liệu qua dashboard. |

### Trang quản trị

Đăng nhập tài khoản admin tại dashboard để chuyển sang trang quản trị. Dùng **Tổng quan** để lọc lịch sử thu thập theo người dùng và **Quản lý tài khoản** để cấp quyền, khóa hoặc xóa tài khoản. Xóa tài khoản cũng xóa dữ liệu riêng của tài khoản đó.
