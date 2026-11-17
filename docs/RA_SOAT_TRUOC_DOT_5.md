# Rà soát trước đợt 5 — 17/11/2026

## Mốc và phạm vi

Nhánh `NDuong`, commit `c3bfa25620bc8d5af01e09b94b252243543ce49c`. Working tree sạch trước rà soát. Chỉ bổ sung tài liệu rà soát, chưa triển khai chatbot hoặc sửa nghiệp vụ. Không đọc nội dung credentials, không gọi CDS thật, không thay dữ liệu người dùng. Không commit/push.

Đã đọc kế hoạch đợt 0–6, tài liệu đợt 3–4, README và bàn giao AI; rà các đường thu thập, parser, snapshot, đọc DB, tổng hợp, điều phối/restart và frontend hiện hành cùng các test liên quan. Đây là đánh giá mã và hành vi đã kiểm tra, không phải bảo đảm mọi trường hợp đều đúng.

## Bản đồ code để tiếp tục làm bài

| Phần | File chính | Vai trò |
|---|---|---|
| Điểm chạy | `app.py`, `frontend/dashboard.py`, `frontend/workflow.py` | Dashboard hiện hành; ba mục Thu thập/Kho dữ liệu/Biểu đồ |
| Thư viện | `frontend/library.py`, `frontend/api_client.py` | Chọn snapshot, lọc, cache, URL khôi phục, CSV, xóa có xác nhận |
| API | `backend/app/main.py`, `backend/app/api/` | Collections, stored, downloads/jobs, API climate và synchronization tương thích |
| Thu thập | `services/download_job_service.py`, `storage/job_repository.py` | Fingerprint chống trùng active, claim, khóa tuần tự trong một tiến trình, trạng thái và chất lượng |
| Nguồn | `providers/era5_provider.py`, `era5_land_timeseries_provider.py`, `cds_retry.py` | ERA5 vùng NetCDF; Land một điểm CSV/ZIP và sáu biến gốc; retry giới hạn |
| Chất lượng | `domain/quality.py`, `providers/quality_import.py` | Ghi issue, giữ phần hợp lệ, không thay thiếu bằng 0 |
| Cache cũ | `storage/climate_repository.py` | Quan sát mutable theo dataset/biến/lưới/UTC, lookup điểm/vùng |
| Snapshot | `storage/collection_repository.py` | Mỗi lần thu thập có ID, metadata, quan sát, grid, artifact/hash và quality riêng |
| Đọc/phân tích | `services/collection_service.py`, `stored_climate_service.py`, `aggregation.py`, `climate_analysis_service.py` | Chọn đúng bộ và grid; DB-only; thống kê, tổng hợp, trend, heatmap, map |
| Đồng bộ cũ | `services/synchronization_service.py`, `synchronization_scheduler.py` | Có thể tải mới; scheduler mặc định tắt |
| Pipeline cũ | `core/`, `config.py`, script test ở gốc | Không phải luồng dashboard hiện hành; không dùng SQL tự do của pipeline cũ cho chatbot |

Luồng hiện hành: form thu thập → POST downloads → job + snapshot → provider tải/import → SQLite + artifact + quality → chọn collection → API collections DB-only → bảng/CSV/biểu đồ. Snapshot được capture cùng transaction ghi quan sát, khác cache dùng chung có thể bị cập nhật sau này.

## Kiểm chứng thực chạy

- `scripts/baseline_offline.py --output-dir output/review-before-phase5`: **333 passed, 1 skipped**, một warning deprecation TestClient. Test live được skip đúng cấu hình. HTTP backend health/docs và frontend health/page đều 200; AppTest đạt; inventory dữ liệu người dùng trước/sau không đổi.
- Runner chặn credential files, CDS client và kết nối mạng ngoài; dùng DB thử riêng. Log: `output/review-before-phase5/pytest.log`, `pytest.xml`, `baseline.json`, `test_safety.json`, `frontend_apptest.json`.
- `scripts/browser_review.py`: **passed**. CSV 48 giờ đúng collection, biểu đồ giờ/ngày/tháng/năm, F5, màn hình 390px và xác nhận xóa trên DB thử. Artifact: `output/browser_review/20261117-140337/`.
- Audit SQLite read-only yêu cầu Land + ERA5: **exit 1, ready=false**. Bốn bộ Land không có lỗi audit; chưa có bộ ERA5 đạt. Log: `output/review-before-phase5/local-audit.json`.
- Thử ca biên riêng: `output/review-before-phase5/probe_edges.py`, kết quả `edge-results.json`. Không gọi CDS, không dùng DB thật.

Môi trường được kiểm tra: Python 3.12.10, Streamlit 1.67.0, FastAPI 0.162.3, pytest 9.2.0, cdsapi 0.7.7. Kết quả này không thay thế tải CDS thật hoặc chạy trên máy khác.

## Thiếu sót và rủi ro cần xử lý

### 1. Ưu tiên cao: bảo vệ đơn vị/nhiệt độ khi thu thập Land

`era5_land_timeseries_provider.py:241` chỉ kiểm tra nhiệt độ có vẻ là Celsius khi **không có QualityCollector**. Luồng thu thập hiện hành luôn có collector, nên bỏ qua kiểm tra này. Parser cũng cắt phần đơn vị trong tên cột tại `_normalize_column`, chưa xác minh đơn vị khai báo.

Tái hiện: CSV fixture đủ các cột và tọa độ, 24 giờ, t2m=25 và d2m=20. Trong quality context, parser giữ t2m=25 dưới quy ước Kelvin và không tạo issue; nếu nhập thì API chuyển thành −248,15 °C. Đây là ca file sai quy ước để kiểm tra khả năng phát hiện lỗi, không phải kết luận dữ liệu CDS thật đang sai.

Đề xuất: xác minh đơn vị được khai báo nếu có; gắn cảnh báo có tên/quy tắc/lý do khi đáng ngờ, không tự đoán đổi C/K hoặc xóa dữ liệu. Giữ cùng mức bảo vệ giữa đường thu thập và đường cũ. Test toàn luồng job → quality → snapshot → API, gồm file khai báo đơn vị không khớp. Kế hoạch đợt 2 có yêu cầu suspicious_value nhưng hiện chưa có quy tắc phát hiện tương ứng.

Cập nhật sửa lỗi sau rà soát: luồng có QualityCollector đã ghi `suspicious_value` cho từng ô nhiệt độ/điểm sương dưới 100 K, dùng ngưỡng heuristic có sẵn của parser. Issue nêu tên quy tắc, đơn vị kỳ vọng, giá trị, file/dòng/biến/thời gian và yêu cầu xác minh đơn vị; không khẳng định chắc chắn là Celsius. Giá trị gốc vẫn được giữ, job hoàn tất có cảnh báo, quality lưu trong job và snapshot. Cả cột và một ô lẻ đều được kiểm tra. Luồng cũ không có collector giữ hành vi từ chối cột có toàn bộ giá trị dưới 100 K. Đây chưa phải xác minh mọi đơn vị khai báo ở header hoặc bộ ngưỡng chất lượng khí tượng tổng quát.

### 2. Ưu tiên cao: giờ biên khi tổng hợp mưa/bức xạ

`stored_climate_service.py:98–101` dịch khoảng đọc +1 giờ cho tổng hợp đại lượng tích lũy. Vì vậy độ phủ snapshot theo timestamp quan sát và độ phủ tổng hợp có thể khác nhau.

Tái hiện: snapshot precipitation chứa đủ 24 timestamp của ngày UTC, coverage=100%, timeseries=24. Aggregate cùng khoảng cần timestamp 00:00 UTC ngày kế tiếp nên chỉ available=23/24. Kết quả hiện báo thiếu trung thực, không bịa giá trị, nhưng danh sách bộ ghi đủ trong khi biểu đồ ghi thiếu. Với tải ngày UTC có thể xuất hiện ở giờ cuối; tải ngày Việt Nam thường có giờ UTC dư nên không phải mọi bộ đều gặp.

Đề xuất: định nghĩa rõ coverage theo observation_time và hour_start, rồi bổ sung giờ biên trong tác vụ thu thập do người dùng tạo nếu cần. Giữ khoảng gốc và phần giờ hỗ trợ riêng; không tải ngầm từ truy vấn. Bổ sung test snapshot cho precipitation và radiation, cả hai nguồn, UTC/UTC+7, ngày cuối và heatmap.

### 3. Ưu tiên cao trước chatbot: tài liệu AI trỏ vào API tự tải

`docs/AI_API_HANDOFF.md` vẫn dùng `/api/v1/climate/...`, hướng dẫn thiếu dữ liệu thì tải. README cũng còn xen mô tả dashboard/sync cũ với mới. Nếu dùng trực tiếp làm hợp đồng tool đợt 5 sẽ trái yêu cầu DB-only của kế hoạch.

Đề xuất cập nhật hợp đồng AI sang inventory/detail/quality và query của `/api/v1/collections/...`; khóa danh sách operation được phép. Chưa có anomaly/compare tương ứng trên API collection; không quảng bá là tool DB-only đã sẵn sàng. Không tái sử dụng `core/database.py::execute_custom_sql_for_ai`.

### 4. Minh chứng đợt 4 còn thiếu ERA5/biển

Code và kiểm thử ổn định tải đã có, minh chứng thật mới đủ Land. Cần một bộ ERA5 thật phạm vi nhỏ có file/hash/coverage/CSV/ảnh UI đạt. Chưa chạy crash trên backend thật; restart mới được kiểm tra bằng fixture. Đây là việc nghiệm thu còn lại, không cản thiết kế chatbot DB-only trên Land.

### 5. Môi trường cài mới chưa được ràng buộc tương xứng UI

`requirements.txt` chấp nhận `streamlit>=1.37.0`, trong khi UI dùng các API/tham số mới như pills, horizontal container, wrap, width, JavaScript trong st.html. Môi trường hiện tại 1.67 chạy đạt; chưa xác định phiên bản tối thiểu thực sự hỗ trợ toàn bộ UI. Cài trên môi trường cũ đã thỏa requirements vẫn có nguy cơ lỗi.

Đề xuất chốt phiên bản đã kiểm chứng hoặc khoảng hỗ trợ có kiểm tra; tách dependency kiểm thử trình duyệt (Playwright + Chromium). Chưa thay requirements trong lượt review.

### 6. Rủi ro khi mở rộng dữ liệu

`DownloadJobService._finish_collection` và `recover_abandoned` đọc toàn bộ quality issues vào list trước khi chốt snapshot; API phân trang không giải quyết bộ nhớ bước này. `CollectionRepository.list` đọc tất cả metadata rồi mới lọc/phân trang bằng Python. Chưa đo tải lớn nên không kết luận hiện gây chậm/OOM. Với demo nhỏ có thể giữ; cần xử lý theo lô/SQL trước khi chạy nhiều năm, nhiều điểm hoặc dữ liệu lỗi hàng loạt.

## Đối chiếu yêu cầu trong ảnh

- Thu thập khí tượng toàn cầu: ERA5 hỗ trợ vùng, Land hỗ trợ điểm; chưa có minh chứng chạy thực tế toàn cầu. Không gọi dataset ERA5 là chỉ chứa biển.
- Biểu đồ biến thiên sau tải: đã có giờ/ngày/tháng/năm, cần xử lý ca biên tích lũy nêu trên.
- Cập nhật theo thời gian thực ERA5: có cơ chế sync cũ, nhưng UI hiện hành đọc snapshot và scheduler tắt theo kế hoạch. F5/poll job/cache refresh không đồng nghĩa tự thu thập dữ liệu ERA5 mới. Cần trình bày đúng độ trễ công bố và chốt yêu cầu cập nhật với giảng viên; nếu yêu cầu tự cập nhật là bắt buộc thì còn việc sau đợt 5.
- AI Agent: chưa có backend/frontend chatbot. Ảnh ghi AI Agent; chatbot theo luật chỉ nên chọn khi được chấp nhận, không đặt tên thành LLM Agent.

## Điểm bắt đầu đợt 5

1. Ưu tiên sửa bảo vệ đơn vị/chất lượng và chốt quy ước giờ biên; cập nhật hợp đồng tool DB-only.
2. Chốt chatbot theo luật hay LLM tool calling. Nếu yêu cầu đúng AI Agent, cần adapter mô hình, cấu hình/chi phí riêng; chưa chọn hoặc cài provider trong review này.
3. Tools tối thiểu: liệt kê/tìm collection; lấy metadata/quality; truy vấn timeseries/statistics/aggregate có schema tham số cố định. Tool chọn collection/grid/variable/khoảng ngày hợp lệ; giới hạn truy vấn và số điểm trả cho mô hình.
4. Dùng lại `CollectionService` và các service DB-only, không provider, API climate tự tải, SQL hoặc command từ văn bản. Nếu thiếu vị trí/ngày hoặc có nhiều bộ phù hợp thì hỏi lại. Nêu collection/dataset/grid/đơn vị/UTC+7/coverage/warning trong câu trả lời.
5. Giao diện mục Chatbot chỉ xuất hiện khi có chức năng thật. Không tự tạo job; dẫn sang Thu thập khi thiếu dữ liệu.
6. Test hỏi đủ/thiếu tham số, nhiều bộ trùng vị trí, DB rỗng, partial/warning, calm wind/null, ngoài phạm vi, prompt injection và chặn tuyệt đối CDS/SQL tự do. Số liệu do công cụ tính, mô hình chỉ diễn giải.

Có thể bắt đầu đợt 5 với nền hiện tại sau khi xử lý các ưu tiên trên; chưa nên tuyên bố toàn bộ đợt 4 đã nghiệm thu đủ dữ liệu thật hoặc yêu cầu cập nhật tự động đã hoàn tất.
