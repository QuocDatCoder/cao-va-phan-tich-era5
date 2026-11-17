# Kiểm tra trước push và chuyển sang đợt 5

Ngày 17/11/2026, nhánh `NDuong`, nền `c3bfa25620bc8d5af01e09b94b252243543ce49c`. Kiểm tra working tree sau các thay đổi chất lượng nhiệt độ, hủy tác vụ, tải lịch sử nền, cache, chuyển tab/F5 và biểu đồ. Chưa commit hoặc push trong lượt này.

## Kết quả thực chạy cuối

| Kiểm tra | Kết quả | Bằng chứng local |
|---|---|---|
| Offline toàn bộ | **360 passed, 1 skipped**, một deprecation warning TestClient | `output/prepush-verified/pytest.log`, `pytest.xml` |
| Chặn credentials/CDS/outbound trong runner | Đạt, không có blocked event | `output/prepush-verified/test_safety.json` |
| Backend health/docs, frontend health/page | HTTP 200 | `output/prepush-verified/baseline.json` |
| Streamlit AppTest tích hợp với backend thử riêng | Đạt | `output/prepush-verified/frontend_apptest.json` |
| Dữ liệu người dùng trước/sau runner | Inventory không đổi | `output/prepush-verified/baseline.json` |
| Chromium | Đạt: xem/CSV, bộ lọc đóng sau vẽ, cuộn, giờ/ngày/tháng/năm, chuyển tab, F5, mobile, xóa | `output/browser_review/20261117-173908/result.json` và ảnh |
| Dependency môi trường hiện tại | `pip check`: No broken requirements found | Thực chạy trong terminal |
| Compile backend/frontend/scripts/app | Đạt | `python -m compileall -q backend frontend scripts app.py` |
| Diff whitespace | `git diff --check` đạt | Có cảnh báo Git chuyển LF/CRLF, không phải lỗi diff |
| Dữ liệu demo thật local | 6 snapshot Land đạt; **chưa đủ ERA5** | `output/prepush-demo-audit.json`, exit 1 đúng lý do |

Các kiểm tra không tải CDS thật. Browser/AppTest sử dụng dữ liệu mock và DB riêng, không chứng minh kết nối CDS thật thành công.

## Lỗi logic được sửa trong lượt rà soát này

1. **Callback job đã hủy rồi xóa:** worker gọi lại có thể gặp None/JobNotFound trong finalizer. Worker giờ bỏ qua callback đã hết hiệu lực, không tải lại và không truy cập `.status` của None. Test hủy → xóa → chạy callback muộn đạt.
2. **URL phục hồi không hợp lệ:** JSON sai cấu trúc, loại biểu đồ lạ hoặc thiếu tham số có thể lọt tới API/widget. Thêm kiểm tra cấu trúc, collection ID, loại/operation, biến, tọa độ, chu kỳ, tổng hợp và khoảng thời gian có timezone. URL không hợp lệ đưa về danh sách có thông báo; không truy vấn chuỗi dữ liệu.
3. **Bộ đang xem bị xóa từ phiên khác:** làm mới gặp 404 từng để lại màn hình lỗi/selection cũ. API client giữ status code; frontend dọn selection/result/URL liên quan và đưa về danh sách, không tự tuyên bố người dùng vừa xóa bộ đó. Test hai phiên/xóa trực tiếp qua API đạt.
4. **Môi trường cài mới:** requirements trước chấp nhận Streamlit 1.37 trong khi giao diện dùng API mới hơn. Chốt `streamlit>=1.67.0,<2` theo phiên bản đã kiểm chứng; không tự cài/nâng thư viện.
5. **Cảnh báo điều hướng:** tránh truyền đồng thời default và Session State cho pills khi đã có lựa chọn.

Bổ sung test phản hồi lịch sử nền cũ không được ghi đè lần Làm mới mới hơn; ngày biểu đồ đảo ngược không tạo query, giữ đồ thị và mở bộ lọc để sửa. Chỉnh browser runner bỏ thao tác ArrowDown dư có thể tự chọn/đóng menu trước khi click option; không sửa sản phẩm để che lỗi test.

## Điểm cần giữ rõ khi push

- Phạm vi tải tuần tự/hủy hợp tác vẫn là một backend một worker. Network call đang chạy có thể phải trả về/timeout mới dừng; không cam kết đã xóa job phía CDS.
- Audit dữ liệu hiện có chưa có ERA5 thật đạt. Push mốc code được, chưa tuyên bố nghiệm thu đầy đủ dữ liệu biển.
- Với mưa/bức xạ, aggregate căn hour_start có thể cần thêm timestamp biên cuối so với timeseries. Kết quả thiếu giờ được báo partial; không cộng bù hoặc tải ngầm. Chưa thay quy ước khoa học hoặc mở rộng thu thập trong lượt này. Chi tiết ca tái hiện nằm trong `RA_SOAT_TRUOC_DOT_5.md`.
- Không benchmark DB nhiều năm/nhiều điểm. Bước chốt quality vẫn có thể gom toàn bộ issue vào RAM; danh sách metadata backend vẫn lọc bằng Python. Đây là giới hạn quy mô cần xử lý nếu mở rộng, không phải kết quả benchmark đã đạt.
- Một warning deprecation FastAPI/TestClient còn tồn tại; tests không thất bại.
- Không có DB/raw file/credentials trong tập file đang chuẩn bị push: `.env`, `data/`, `output/` được ignore; `.env.example` là template đã tracked. Không có file được staged bởi lượt rà soát này.

## Điểm bàn giao AI Agent

Đã thêm hợp đồng **collection DB-only** ở đầu `docs/AI_API_HANDOFF.md`, tách rõ khỏi API climate/sync legacy có thể tự tải CDS. Agent mới dùng inventory/detail/quality và query collection có danh sách operation cố định; chưa có chatbot hoặc model adapter trong commit này. Anomaly/compare collection DB-only chưa sẵn sàng. Chốt mô hình/tool calling và cấu hình ở đợt 5; không dùng SQL tự do hoặc tự tải từ câu hỏi.

## Commit đề xuất

`fix: harden collection jobs and dashboard workflows`

Nội dung nên gồm: cảnh báo nhiệt độ nghi vấn; cancellation API/worker và bảo toàn snapshot; tải lịch sử không chặn form; trạng thái rõ ràng; cache, tìm kiếm, chọn bộ, chuyển tab/F5, vị trí bộ lọc/thu gọn/cuộn; các sửa ca biên và test/tài liệu tương ứng. Đọc diff trước khi stage; không thêm output, DB hoặc credentials. Chạy `git diff --cached --check` và đọc `git diff --cached --stat` trước khi commit. Không force push.
