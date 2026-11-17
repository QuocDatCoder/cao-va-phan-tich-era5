# Đợt 3 — Thu thập → bộ dữ liệu đã lưu → biểu đồ

## Luồng hiện hành

Mỗi lần thu thập mới tạo một **bộ dữ liệu độc lập**, liên kết với tác vụ bằng `collection_id`. Tên bộ dữ liệu gồm biến, địa điểm và khoảng ngày gốc. Không còn chọn chuỗi bằng tọa độ gần nhất hoặc gộp tất cả các lần tải làm một bộ dữ liệu.

1. **Thu thập:** tìm thành phố hoặc nhập tọa độ, chọn biến và ngày Việt Nam, bấm Bắt đầu thu thập. Sửa tọa độ khác kết quả thành phố thì tên thành phố không được gán cho điểm mới.
2. **Dữ liệu đã lưu:** tìm trong các bộ dữ liệu bằng tên thành phố, biến, nguồn hoặc ngày. Chọn bộ rồi bấm Xem chi tiết. Bảng chính có thời gian UTC+7 và giá trị với đơn vị; thông tin kỹ thuật, file nguồn và biến bổ sung nằm trong phần mở rộng.
3. **Biểu đồ:** chọn bộ dữ liệu vừa thu thập hoặc bộ cũ trong danh sách, chọn biến có trong bộ và điểm lưới thực. Chọn giờ/ngày/tháng/năm và loại biểu đồ, rồi bấm Vẽ dữ liệu đã chọn. Nút Vẽ biểu đồ từ dữ liệu này giữ mã bộ, biến và điểm lưới đang xem.
4. **Dữ liệu cũ:** kho tổng hợp từ cơ chế trước, có thể gồm nhiều lần nhập. Chỉ tra cứu bằng thông tin thật đã có trong SQLite. Không gán ngược tên thành phố, ngày yêu cầu hay job cho các dòng không có chứng cứ liên kết. Kho này được giữ để xem/CSV/vẽ theo cách cũ, không chuyển thành bộ dữ liệu giả.

Điều hướng ngang; chỉ render mục đang chọn. Giao diện sáng/tối dùng menu gốc Streamlit ở góc phải. Không có chatbot giả. Các form chưa submit không tạo tác vụ hoặc truy vấn số liệu mới. Danh sách bộ dữ liệu là truy vấn metadata, không gọi CDS.

## Ví dụ chính xác về thời gian và số lượng

Nhiệt độ ở Hồ Chí Minh, 01/01/2025–02/01/2025 UTC+7:

- Khoảng người dùng chọn: 01/01 00:00 đến 02/01 23:00, **48 giờ**.
- Khoảng tương ứng UTC: 31/12/2024 17:00 đến 02/01/2025 16:00.
- Provider tải các ngày UTC bao phủ: 31/12, 01/01, 02/01.
- Land có thể trả 72 giờ × 6 biến = 432 giá trị. Đây là số giá trị nhập của toàn file, **không phải 432 giá trị nhiệt độ được yêu cầu**.
- Độ phủ biến chính tính từ các quan sát thuộc bộ dữ liệu và đúng 48 giờ gốc. Có 47 giờ thì partial; không có nhiệt độ hợp lệ thì failed, dù có các biến khác.
- Nhiệt độ lưu K, hiển thị/xuất °C. CSV giữ mã bộ, nguồn, dataset, biến, đơn vị, điểm lưới và timestamp có múi giờ.

Đây là ví dụ về quy tắc và fixture kiểm thử; không phải tuyên bố kết quả tải CDS thật mới.

## Dữ liệu lưu trong SQLite

Migration bổ sung trong climate.sqlite3, không xóa/đổi dữ liệu cũ:

| Bảng | Nội dung |
|---|---|
| collections | ID/job, trạng thái, ngày tạo/kết thúc; metadata chứa tên, địa điểm/tọa độ yêu cầu, khoảng giờ gốc và UTC, nguồn/dataset, biến chính; summary chứa độ phủ và thống kê nhập |
| collection_variables | Biến được tải; phân biệt thành phần của biến yêu cầu với biến bổ sung |
| collection_observations | Giá trị, đơn vị, giờ UTC, dataset, biến, điểm lưới; khóa có collection_id |
| collection_grids | Các điểm lưới của bộ; ERA5 vùng phải chọn một điểm để xem chuỗi |
| collection_artifacts | Đường dẫn file đã nhập, SHA-256, kích thước, thời điểm ghi nhận, phiên bản parser |
| collection_quality_issues | Cảnh báo của bộ dữ liệu, lưu bền vững |

`climate_points` vẫn là cache chung phục vụ API tương thích cũ. Bản sao quan sát của bộ dữ liệu được ghi **trong cùng transaction với lần nhập**, không dựng lại từ cache sau đó. Lần thu thập tiếp theo không cập nhật quan sát của bộ trước. Các giờ/biến bổ sung vẫn được lưu, tách khỏi số lượng biến chính. API xem mặc định giới hạn trong khoảng yêu cầu gốc; file nguồn và tổng số giá trị nhập cung cấp thông tin phần dư.

Snapshot không cho sửa quan sát qua API; thao tác xóa riêng có xác nhận được bổ sung trong phần cập nhật cuối tài liệu. Tác vụ trùng đang chờ/chạy trả cùng job/bộ. Tác vụ đã kết thúc có thể tạo bộ mới. Lần nhập lặp cùng giờ trong một bộ giữ giá trị hợp lệ đầu tiên và có warning. Không thay thiếu bằng 0.

Backend kiểm tra danh tính tiến trình của tác vụ đang chạy khi khởi động. Nếu xác nhận worker cũ đã thoát, bộ được đánh dấu interrupted, job failed và phần dữ liệu hợp lệ được giữ; không tự gửi lại CDS. Nếu hệ điều hành không cho đọc danh tính tiến trình thì không đoán tác vụ đã chết. Đây không phải hàng đợi phân tán hay tự động tiếp tục tải sau crash.

## API

- `POST /api/v1/downloads`: bổ sung place_name, selected_place, requested_start/end có offset, request_timezone. Trả collection_id. Client cũ chỉ gửi start_date/end_date vẫn được hiểu là ngày UTC và ghi rõ UTC.
- `GET /api/v1/collections?search=...&limit=...&offset=...`: tìm bộ đã lưu.
- `GET /api/v1/collections/{id}`: metadata, chất lượng tổng hợp, các biến, điểm lưới, file nguồn.
- `GET /api/v1/collections/{id}/quality`: warning phân trang.
- `GET /api/v1/collections/{id}/timeseries`, `timeseries.csv`, `aggregate`, `statistics`, `trend`, `heatmap`, `map`: chỉ đọc snapshot của ID đã chọn. Không fallback sang cache, không gọi CDS. ERA5 nhiều điểm phải gửi latitude/longitude thuộc bộ.

API `/api/v1/stored/climate/...` giữ cho kho tổng hợp cũ. API `/api/v1/climate/...` là luồng tương thích có thể tải; thư viện/biểu đồ mới không sử dụng luồng đó.

Độ phủ thống kê yêu cầu được tính lại từ DB. Độ phủ parser toàn file và cảnh báo được trình bày riêng; một bộ đủ giờ vẫn có thể có warning. Đồng bộ không nâng mốc khi báo cáo chất lượng chưa đầy đủ.

## Chạy thử

Khởi động lại backend/frontend bằng lệnh README. Thu thập một khoảng ngắn, mở Xem dữ liệu vừa thu thập, kiểm tra địa điểm/ngày/48 giờ nếu chọn hai ngày đầy đủ, rồi mở biểu đồ. Sau khi khởi động lại vẫn tìm được bộ bằng tên thành phố. Dữ liệu đã có trước bản sửa nằm trong Dữ liệu cũ; không phải bị mất.

Kiểm thử offline: `.\.venv\Scripts\python.exe -X utf8 scripts/baseline_offline.py`. Runner chặn CDS thật, chặn đọc .env/.cdsapirc và dùng DB/file riêng. Log nằm trong output/baseline; không commit thư mục output hoặc tmp.

Các kiểm thử mới kiểm tra 48 giờ UTC+7, metadata sau restart, số liệu riêng giữa các lần tải/dataset, cache thiếu không tự tải, file/hash, warning và số lượng khớp DB, failed khi thiếu toàn biến chính, phát hiện worker chết, migration giữ dữ liệu cũ, CSV, bốn chu kỳ và luồng Streamlit từ chọn thành phố đến chọn đúng bộ/biến để vẽ.

Chưa gửi yêu cầu CDS thật trong lượt sửa này. Kiểm thử giao diện dùng Streamlit AppTest và HTTP startup; chưa kiểm tra trực quan bằng trình duyệt người dùng. Không tự commit/push.

## Kết quả kiểm tra bản sửa bộ dữ liệu

Log cuối: `output/baseline/20261116-202543/`.

- **289 passed, 1 skipped, 1 warning**. Test CDS thật bị bỏ qua có chủ đích; warning là deprecation của Starlette/httpx.
- Backend health/docs, frontend health/page HTTP 200; Streamlit AppTest đạt.
- `test_safety.json`: không đọc credentials, không mở CDS client, không kết nối ra ngoài; không có sự kiện bị chặn.
- Runner tổng thể trả mã 1 vì inventory phát hiện mtime của `data/cache/climate.sqlite3`, `-shm` và `-wal` thay đổi. SQLite chính vẫn cùng kích thước; không kết luận file DB không thay đổi. Backend/frontend người dùng vẫn đang chạy tại 8000/8501 trong lúc kiểm tra.
- Kiểm tra chỉ đọc sau đó: SQLite `quick_check=ok`, còn 2.414 quan sát trong cache cũ; các bảng collection đã có, chưa có bộ dữ liệu mới (`collections=0`). Vì vậy thư viện bộ mới rỗng là đúng; dữ liệu trước bản sửa nằm ở Dữ liệu cũ. Không đủ bằng chứng từ inventory metadata để khẳng định mọi byte của DB trước/sau giống nhau.
- Không tự thu thập CDS thật, không xóa DB/file tải, không commit/push. Kiểm thử migration và giữ bản snapshot dùng DB fixture riêng.

Có thể thu hẹp khoảng xem trong bộ đã chọn. Giới hạn độ dài truy vấn vẫn theo `era5_max_query_days` của backend (mặc định 366 ngày); với bộ dài, giao diện mặc định 30 ngày đầu. Không tự gọi CDS để bù khoảng trống.

## Thư viện dạng danh sách

Thư viện hiện mặc định liệt kê các lần thu thập mới nhất bằng thẻ: tên biến/địa điểm, khoảng dữ liệu, nguồn, trạng thái, số giá trị và thời điểm thu thập UTC+7. Mỗi thẻ có Xem/Vẽ trực tiếp, không phải tìm UUID trong hộp chọn dài. Bộ vừa tạo trong phiên được đánh dấu Vừa thu thập. Nút sau thu thập mở đúng ID; Xem mở chi tiết, Vẽ chuyển sang biểu đồ giữ ID. Dữ liệu từ phiên bản trước nằm trong phần mở rộng cuối danh sách, vẫn truy cập được khi chưa có bộ mới. Tìm kiếm là tùy chọn; mặc định không cần nhập bộ lọc.

## Kiểm tra frontend bằng Chromium thực — 16/11/2026

Đã phát hiện và sửa nhãn bị biến thành dấu hỏi trong frontend/library.py; thêm file này vào kiểm tra chuỗi tiếng Việt. Sửa nút Xem từ màn Biểu đồ bằng callback điều hướng để tránh sửa session state của radio sau khi widget được render. Bảng dưới biểu đồ chỉ hiện các cột số liệu dễ đọc, CSV vẫn giữ thông tin nguồn.

Chạy `scripts/browser_review.py` bằng Playwright/Chromium headless đã cài trên máy; trình duyệt thử nghiệm và backend/frontend dùng DB/file mock riêng, không dùng token hay DB người dùng. Đã bấm thực tế: thư viện rỗng, Xem, xuất CSV (kiểm tra 48 dòng và collection_id), Vẽ biểu đồ có Plotly render, Xem từ biểu đồ, tìm không có kết quả rồi tìm lại thành phố. Chụp và xem ảnh desktop/mobile tại `output/browser_review/20261116-205136/`; kết quả `result.json`. Đây là Chromium thử nghiệm, không phải Chrome đang mở của người dùng. Công cụ native điều khiển Chrome đang lỗi kết nối.

Bộ offline sau sửa điều hướng/nhãn: `output/baseline/20261116-205058/`: 289 passed, 1 skipped (CDS live), 1 warning; startup HTTP 200, AppTest đạt, inventory người dùng không thay đổi. Lần rút gọn bảng sau đó được kiểm tra lại bằng Chromium ở log trên. Chưa gửi CDS thật, chưa commit/push.

## Sửa trùng widget khi mở kho cũ

Tách khóa widget/form của legacy_library thành legacy_stored/legacy_chart; chu kỳ của biểu đồ cũ có khóa riêng. Khi kho cũ được mở, dừng render chi tiết của bộ mới để tránh trùng nút CSV và các widget tự sinh ID. Chuyển từ bảng cũ sang biểu đồ giữ chế độ kho cũ.

Kiểm tra bốn trường hợp kho mới rỗng/có bộ dữ liệu × Dữ liệu đã lưu/Biểu đồ: mở kho cũ, làm mới, xem số liệu, không tạo POST tải mới. `output/baseline/20261116-205336`: 293 passed, 1 skipped, 1 warning; startup/AppTest đạt. Runner mã 1 do file SQLite tạm -shm/-wal thay đổi; inventory không ghi nhận thay đổi DB chính. Chromium đã mở kho cũ thành công tại `output/browser_review/20261116-205443/08-legacy.png` và hoàn thành luồng kiểm tra. Không tải CDS thật, không commit/push.

## Rà toàn luồng và chỉnh bố cục — 16/11/2026

- Xem/Vẽ mở ngay kết quả mặc định của bộ đã chọn, không phải bấm Xem hai lần. Chỉnh bộ lọc vẫn phải submit; rerun không truy vấn số liệu lặp.
- Thu gọn danh sách khi đã chọn bộ, có mục Chọn bộ dữ liệu khác. Tìm kiếm gọn theo hàng, có Xóa tìm kiếm. Tách trạng thái bằng badge; không trộn số giá trị chính với biến bổ sung.
- Đóng chế độ kho cũ khi người dùng chọn bộ mới. Giới hạn mặc định khoảng xem 30 ngày đầu đúng với lời hướng dẫn.
- Theme xanh ngọc dùng `.streamlit/config.toml`, hỗ trợ light/dark bản địa, không thêm công tắc giao diện thứ hai. Chú thích rõ chu kỳ chỉ áp dụng chuỗi thời gian, thống kê dùng toàn khoảng xem.

Kiểm tra bằng tab trong Chrome kết nối CDP (cửa sổ hồ sơ thử nghiệm): `output/browser_review/20261116-214235/`. Backend/frontend kiểm thử riêng, dữ liệu mock, geocoder fixture Hà Nội. Luồng bấm thật tìm địa điểm → 01–02/01/2025 → thu thập một job → mở bộ → tìm Hà Nội → CSV 48 dòng đúng collection_id → biểu đồ giờ/ngày/tháng/năm → thống kê → Xem lại → kho cũ → tìm không kết quả/tìm lại. Đã xem ảnh bảng, biểu đồ, thống kê và viewport hẹp. Không gọi CDS thật.

Offline: `output/baseline/20261116-214321/`: **295 passed, 1 skipped, 1 warning**, startup và AppTest đạt. Runner tổng thể mã 1 do mtime của hai DB người dùng climate.sqlite3/jobs.sqlite3 thay đổi khi backend thật vẫn chạy; kích thước giữ nguyên. Không tuyên bố inventory bất biến. Backend thật cổng 8000 đã không phản hồi /health và các GET metadata trong lượt kiểm tra; chưa dừng/restart vì có job ERA5 đang running. Cần xác minh lại trạng thái tiến trình thật trước demo CDS. Bản sửa frontend không giải quyết hay che tình trạng backend này. Không commit/push.

Cập nhật cuối lượt: backend thật /health đã trả 200 trở lại. Đã thực hiện lại trên cửa sổ Chrome CDP ở localhost:8501: mở bộ Land có sẵn 96/96 giờ, khoảng 31/05/2026 07:00–04/06/2026 06:00 UTC+7; xem bảng và chuyển vẽ biểu đồ thành công, điểm lưới 21.0,105.8, không có stException. Ảnh ở `output/chrome-audit/library.png`, `detail.png`, `chart.png`. Không tạo job CDS mới. Tình trạng không phản hồi trước đó là tạm thời quan sát được, chưa xác định nguyên nhân gốc nên chưa tuyên bố đã sửa backend.

## Xóa màn hình cũ ngay khi chuyển mục

Mỗi mục có một placeholder riêng ở vị trí ổn định. Các placeholder không được chọn được xóa trước mọi lời gọi API, tránh form Thu thập cũ còn mờ trên Dữ liệu đã lưu. Khi đọc thư viện, hiện spinner rõ ràng. Không dùng CSS che nội dung hoặc tăng tốc animation để giấu lỗi.

Chromium kiểm tra với lần đọc catalog đầu tiên cố ý chậm 3 giây: trong khi spinner hiện, nút Bắt đầu thu thập và form cũ không còn visible. Ảnh bằng chứng: `output/browser_review/20261116-215003/loading-no-old-form.png`; luồng browser đầy đủ cũng đạt. Offline `output/baseline/20261116-214930/`: pytest và startup/AppTest đạt; runner inventory không đạt do DB người dùng có thay đổi trong lúc tiến trình thật đang chạy. Không gọi CDS thật trong các kiểm thử này.

## Kiểm thử lại chuyển mục và vị trí biểu đồ
- Dùng chung cache đọc API theo phiên cho thư viện, chi tiết và truy vấn bộ dữ liệu (60 giây, tối đa 128 mục). Nút Làm mới thư viện xóa cache để đọc lại ngay.
- Hai mục cùng khôi phục bộ dữ liệu đã chọn; biểu đồ được đặt ngay dưới tiêu đề, trước thư viện và thông tin chi tiết.
- Kiểm tra bằng Streamlit AppTest và các bài kiểm thử biểu đồ/dashboard; chưa xác nhận trực quan trên trình duyệt người dùng.

## Điều chỉnh thao tác xem chi tiết
Nút Xem trong thư viện mở bộ và tải bảng mặc định. Nút trong form đổi thành Áp dụng bộ lọc: áp dụng biến, điểm lưới và khoảng ngày cho bảng/CSV. Form ghi rõ thay đổi chưa áp dụng; kết quả ghi biến và khoảng thời gian thực tế. Khi chuyển mục dùng bộ dữ liệu đang chọn chung, tự tải nếu mục đó chưa có kết quả của bộ này. Khoảng ngày sai không truy vấn và không ghi đè kết quả đã áp dụng. Truy vấn giống nhau tái sử dụng cache trong phiên.

## Bố cục biểu đồ thu gọn
Thứ tự màn hình: tìm kiếm/thư viện → tên bộ và khoảng thu thập → chi tiết/chất lượng (đóng mặc định) → cấu hình vẽ → kết quả biểu đồ. Bảng số liệu và CSV đặt trong mục đóng/mở dưới biểu đồ, tránh kéo dài trang. Các cảnh báo của kết quả đang vẽ vẫn hiển thị ngoài mục chi tiết.

## Giữ trạng thái khi F5
Cache đọc API chuyển từ session_state sang st.cache_data (TTL 60 giây, tối đa 128 mục), dùng lại giữa các phiên trình duyệt trong cùng tiến trình frontend. Mục đang xem và bộ lọc đã áp dụng được lưu trên URL để khôi phục bảng/biểu đồ sau F5. Không lưu bản nháp chưa áp dụng. Làm mới xóa cache; tạo tác vụ mới cũng vô hiệu cache. Cache hết hạn hoặc frontend khởi động lại sẽ đọc API lại; không gọi CDS để phục hồi màn hình. Kiểm thử: 21 bài đạt, có test phiên mới không tăng số lần gọi API và nút Làm mới thực sự gọi lại; Chromium xác nhận reload giữ bộ dữ liệu/kết quả.


## Cập nhật sau mốc đợt 3

### Xóa bộ dữ liệu

Mỗi thẻ trong Kho dữ liệu/Biểu đồ có nút Xóa và xác nhận ghi rõ bộ được chọn. Bộ queued/running bị khóa; API cũng kiểm tra trạng thái cả job lẫn snapshot và trả 409 nếu tác vụ còn hoạt động. `DELETE /api/v1/collections/{id}` xóa snapshot, biến, điểm lưới, liên kết artifact, cảnh báo và lịch sử job tương ứng trong transaction SQLite; lỗi giữa chừng rollback. Bộ không tồn tại trả 404.

File nguồn vật lý và cache tương thích dùng chung được giữ để tránh ảnh hưởng bộ khác. Xóa bộ dữ liệu không hủy yêu cầu phía CDS. Snapshot vẫn không cho sửa quan sát qua API; DELETE là thao tác riêng do người dùng xác nhận.

Danh sách/phân trang chạy trong fragment. Xóa xong tự đóng popover và chỉ cập nhật danh sách. Nếu xóa bộ đang xem thì dọn đúng vùng chi tiết; kết quả của bộ khác giữ nguyên. Cache và URL đã lưu của bộ bị xóa được dọn để không hiện lại sau F5. API lỗi thì giữ nguyên dữ liệu và hiển thị lỗi.

### Điều hướng và tải dữ liệu

- Điều hướng/nội dung có fragment riêng, phần thương hiệu không dựng lại khi đổi mục.
- Danh sách dùng một kết nối SQLite và đọc các bảng liên quan theo lô thay vì mở kết nối cho từng thẻ; payload không đổi.
- Job lịch sử đã kết thúc dùng cache. Job đang chờ/chạy vẫn kiểm tra trực tiếp; không rerun toàn trang chỉ vì mở một job completed cũ.
- Báo cáo chất lượng chỉ gọi API chi tiết khi mở. Bảng dưới biểu đồ và CSV chỉ tạo khi mở, trong fragment riêng, không dựng lại Plotly.
- Khi đổi mục: giữ tiêu đề/điều hướng, ẩn nội dung cũ và phần đang dựng của mục mới, hiện vòng xoay “Đang tải nội dung…”. Khi render kết thúc thì gỡ loading; luôn dọn loading cả khi có lỗi. Không thêm thời gian chờ giả, không bật lại loading khi chỉ cập nhật bảng/danh sách trong cùng mục.
- Nút Làm mới giữ icon và nằm cùng hàng với tiêu đề cả ở màn hình dọc. Bỏ hai nút mở nhanh dữ liệu/biểu đồ cuối phần Tác vụ thu thập theo yêu cầu người dùng.

### Đo tốc độ và kiểm chứng

Chromium trên dịch vụ/DB thử riêng, hai snapshot mock 24 quan sát mỗi bộ; không phải số đo CDS hoặc DB lớn. Khi đã chọn bộ, trung vị 5 lượt: Thu thập 138/134 ms, Kho dữ liệu 158/150 ms, Biểu đồ 195/211 ms ở 1280px/390px. Mở bảng CSV khoảng 125/139 ms. Mở biểu đồ lần đầu qua các lượt đo riêng khoảng 1,29–2,28 giây. Đo render hoàn tất bằng thông điệp `script_finished` và hai animation frame, không chỉ thời điểm hiện tiêu đề.

Thử API danh sách cố tình chậm 2,5 giây: khung mới hiện sau 0,500 giây, danh sách sẵn sàng 3,328 giây; mục cũ không còn nhìn thấy. Sau khi bổ sung loading theo xác nhận người dùng, nội dung mới cũng được ẩn trong thời gian chờ. Ảnh kiểm tra loading ở `output/loading-browser-ok/`, số đo JSON ở `output/speed-run3/`, `output/speed-selected/`, `output/speed-delayed/`. Đây là artifact local, không commit thư mục output.

Mốc kiểm thử bàn giao cuối và tình trạng dữ liệu thật xem `docs/DOT_4_ON_DINH_TAI_VA_DEMO.md`.

## Sửa cập nhật vùng tác vụ thu thập (17/11/2026)

### Ghi nhớ từng tab trong phiên — quy tắc điều hướng hiện hành

Thay quy tắc trước đây luôn về danh sách khi bấm tab: lần đầu vào mỗi tab vẫn là danh sách; sau khi chủ động xem/vẽ, chuyển tab rồi quay lại giữ bộ, kết quả và cấu hình đã áp dụng của riêng tab đó. Quay lại danh sách là thao tác tường minh và được nhớ qua lần chuyển tab tiếp theo. Khi widget bị Streamlit dọn do tab không hiển thị, khôi phục variable/grid/ngày/loại/chu kỳ từ cấu hình đã áp dụng trước khi dựng lại widget. Không dùng bộ của tab kia, không truy vấn lại chuỗi dữ liệu nếu kết quả còn trong phiên. F5 phục hồi tab hiện tại theo URL, đường dẫn gốc không tự mở bộ cũ. Xóa bộ đang xem đưa tab liên quan về danh sách như trước.

Kiểm chứng: **35 test workflow đạt**, gồm hai tab xem hai bộ khác nhau, giữ chu kỳ daily khi quay lại, không tăng lời gọi API trong vòng chuyển tab và nhớ chế độ danh sách. Chromium chuyển Kho dữ liệu ↔ Biểu đồ giữ bảng/đồ thị, F5/CSV/mobile/xóa đạt (`output/browser_review/20261117-173212/`).

### Thu gọn bộ lọc sau khi vẽ

Bộ lọc Biểu đồ nằm trong expander: chọn bộ mở sẵn; submit thành công tự đóng và cuộn đến Plotly (thống kê dùng khối kết quả). Ngày sai/API lỗi không đóng bộ lọc và giữ kết quả đã áp dụng. F5 kết quả đã vẽ mặc định thu gọn; mở bộ lại vẫn thấy bộ lọc để chỉnh. Tự cuộn theo bố cục dựng trong khoảng tối đa 1,5 giây để tránh nhắm DOM cũ lúc rerun; dừng ngay khi người dùng chạm/bấm/cuộn. Nếu đã tới cuối trang, giữ biểu đồ trọn trong khung nhìn thay vì thêm khoảng trắng chỉ để ép nó lên sát đầu. Chromium kiểm tra ba lần đổi ngày/tháng/năm: bộ lọc đóng sau thành công và Plotly hiển thị đúng vị trí, cùng F5/CSV/mobile/xóa đạt (`output/browser_review/20261117-172215/`). Test workflow: 32 đạt (`output/chart-collapse-final/`).

### Luồng Biểu đồ sau rà soát

Thứ tự mới: danh sách/tìm kiếm → Quay lại danh sách + tên bộ → chi tiết/chất lượng thu gọn → loại biểu đồ → form biến/lưới/ngày/chu kỳ + nút Vẽ → cấu hình đã áp dụng/cảnh báo → biểu đồ/bảng CSV. Ngày hiển thị trực tiếp; chu kỳ chỉ xuất hiện cho chuỗi thời gian, heatmap dùng tháng. Đổi loại biểu đồ chỉ đổi cấu hình, chưa truy vấn.

Vào tab Biểu đồ mở danh sách, không tự dùng lựa chọn Kho dữ liệu. Nút Vẽ từ Kho dữ liệu chuyển bộ/biến/lưới/khoảng ngày tường minh. Hai mục có collection/preferred/mode/visible riêng. Chọn bộ đóng danh sách, cuộn đến bộ lọc; Vẽ thành công cuộn đến kết quả. Quay lại giữ tìm kiếm/phân trang. URL chart_mode + chart_applied phục hồi bộ/cấu hình đã vẽ khi F5, không phục hồi bản nháp chưa submit. Đổi bộ đặt lại loại/chu kỳ mặc định để không dùng nhầm cấu hình toàn cục.

Kết quả nằm sau form, spinner cập nhật đặt dưới form; không có overlay che toàn trang Biểu đồ. Ngày sai hoặc API lỗi giữ kết quả trước và URL đã áp dụng; không tự retry chỉ vì đổi lựa chọn. Làm mới đọc lại cấu hình đã vẽ, không áp dụng bản nháp loại biểu đồ. Xóa bộ đang xem đưa về danh sách mở.

Kiểm chứng: nền **348 passed, 1 skipped**, startup/AppTest đạt (`output/chart-workflow-final/`); test F5, danh sách trước khi chọn, lỗi truy vấn giữ kết quả/URL. Chromium xác nhận form ở trên Plotly, ngày hiển thị trực tiếp, giờ/ngày/tháng/năm, F5, CSV, mobile và xóa (`output/browser_review/20261117-171111/`). Không gọi CDS thật hoặc thay dữ liệu người dùng.

### Điều hướng Kho dữ liệu và F5

Bấm tab Kho dữ liệu mở danh sách trước, không tự hiện bộ cũ. Bấm Xem dữ liệu đóng danh sách và cuộn tới chi tiết. URL lưu `stored_mode=details` cùng bộ lọc đã áp dụng để F5 giữ đúng bộ/bộ lọc đang xem; vào đường dẫn gốc không tự mở dữ liệu cũ. Nút Quay lại danh sách đóng chi tiết, mở/cuộn về danh sách, giữ từ khóa và trang tìm kiếm; URL chuyển về chế độ list nên F5 lúc này vẫn ở danh sách. Query params cũ có stored_applied nhưng thiếu stored_mode không tự mở chi tiết. Kiểm chứng: 30 test workflow đạt; Chromium xác nhận reload chi tiết, quay về danh sách, mở lại, chuyển tab, cuộn đúng vị trí và luồng CSV/biểu đồ/xóa (`output/browser_review/20261117-170244/`).

### Form Thu thập dùng được trước khi lịch sử tải xong

Bỏ lớp loading che toàn mục Thu thập. Phần nguồn/tìm địa điểm/form dựng trước; việc đọc GET jobs chạy trên executor riêng, worker chỉ làm HTTP I/O, không truy cập Streamlit/session state. Vùng tác vụ kiểm tra Future mỗi giây, chỉ gửi GET mỗi 15 giây khi còn job active, hoặc lúc thiếu danh sách/Làm mới. Lúc đang chờ chỉ vùng này hiện thông báo; không chặn form phía trên. Giữ một request đang chờ cho mỗi revision, bỏ kết quả cũ sau tạo/hủy/xóa/Làm mới để tránh phản hồi trễ ghi đè danh sách mới. Báo cáo quality đang mở đọc tối đa mỗi 15 giây hoặc khi summary thay đổi; giờ kiểm tra hiển thị theo lần nhận kết quả API, không cập nhật giả mỗi nhịp render.

Test offline nền: **344 passed, 1 skipped**, startup/AppTest đạt (`output/nonblocking-collection/`). Test Future chưa hoàn tất xác nhận form vẫn dùng được và đổi nguồn không tạo thêm request lịch sử. Chromium với GET jobs cố tình chậm **7 giây**: form hiện và xử lý submit tìm kiếm rỗng sau **1,45 giây**, trước khi GET jobs hoàn tất. Cả hủy, trạng thái, chuyển mục, API lỗi/phục hồi và mobile đạt; artifact `output/job-loading-browser/`. Đây là fixture cục bộ, không phải thời gian phản hồi CDS thật.

### Tránh đọc lại thư viện sau xóa và chuyển mục

Danh sách trang/từ khóa được giữ trong phiên (tối đa 128 mục), dùng chung giữa Kho dữ liệu và Biểu đồ. Hết TTL 60 giây của cache dùng chung không làm mất danh sách trong phiên. Sau DELETE thành công, loại ID đã xóa khỏi các trang đầu đã đọc và giữ kết quả bộ khác; không gọi lại list API cho thư viện nhỏ. Trang sau bị vô hiệu do offset thay đổi; trang đầy 51 mục cần đọc lại để bù dòng và xác định phân trang đúng. Nút Làm mới, tạo/hủy job và quan sát job kết thúc vẫn vô hiệu danh sách để đọc thay đổi thật. Thay đổi từ phiên/máy khác cần bấm Làm mới. Spinner chỉ tạo khi thiếu trang trong cache phiên, không xuất hiện ở mỗi lần render danh sách đã có.

Kiểm chứng: test giả lập cache chung hết hiệu lực, xóa một trong hai bộ, giữ kết quả bộ còn lại, đổi Kho dữ liệu/Biểu đồ không tăng GET list; Làm mới tăng đúng một lượt GET. Bộ offline **343 passed, 1 skipped**, startup/AppTest đạt (`output/library-cache-fix/`); Chromium luồng CSV/biểu đồ/F5/xóa đạt (`output/browser_review/20261117-164302/`).

Trạng thái được làm nổi bật bằng thẻ có viền màu, biểu tượng và mô tả: xanh dương đang chạy, vàng đang chờ/có cảnh báo, xanh lá hoàn tất, đỏ thất bại. Queued/running có vòng xoay CSS, nhãn tự cập nhật 15 giây và giờ kiểm tra; chuyển động tắt theo prefers-reduced-motion. Không giả phần trăm hoặc tiến độ phía CDS. Thẻ hỗ trợ thông báo trạng thái cho trình đọc màn hình; CSS chỉ áp dụng cho thẻ. Bộ offline sau đổi giao diện: 337 passed, 1 skipped; startup/AppTest đạt (`output/job-status-highlight/`). Chromium kiểm tra làm mới, tự chuyển trạng thái, điều hướng, lỗi/phục hồi và mobile đạt (`output/job-loading-browser/`).

- Gom lịch sử, chọn tác vụ và trạng thái/chi tiết vào cùng fragment 15 giây. Chi tiết dùng hàm render thường bên trong fragment này; không đăng ký thêm fragment poll con.
- Khi còn job queued/running, đọc lại danh sách trực tiếp và dùng cùng payload cho lịch sử, nhãn lựa chọn và chi tiết. Job đã kết thúc dùng danh sách trong phiên; Làm mới chủ động đọc lại API. Không gọi thêm get_job cho cùng lần render.
- Khi job kết thúc, vô hiệu cache đọc bộ dữ liệu, không rerun toàn trang hoặc dựng lại form thu thập.
- Tách ID tác vụ đang chọn khỏi widget hiển thị. Khi nhãn/trạng thái đổi, dựng lại riêng ô chọn để tránh giữ chữ running cũ; giữ nguyên ID đang chọn nếu còn trong danh sách.
- Kiểm tra Chromium trên API fixture có độ trễ: Làm mới ba lần, chỉ một trạng thái/khung chi tiết, tự chuyển running → completed và cập nhật nhãn, chuyển mục hai vòng, API lỗi rồi phục hồi, màn hình 390px. Đạt; log/ảnh ở `output/job-loading-browser/`. Không gọi CDS hoặc thao tác trên dữ liệu thật.
- Regression browser luồng lưu/CSV/biểu đồ/F5/xóa đạt tại `output/browser_review/20261117-141501/`. Bộ offline cuối: **337 passed, 1 skipped**, startup và AppTest đạt, dữ liệu người dùng không đổi; log ở `output/fix-job-loading-final/`. Agent API kiểm tra phiên chạy thật đạt trạng thái ready, snapshot ở `output/job-loading-browser/agent-snapshot.json`.
