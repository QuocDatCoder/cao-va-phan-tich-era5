# Đợt 5 — công cụ DB-only cho Agent

Đã xây dispatcher và schema công cụ, tái sử dụng CollectionService/StoredClimateService và snapshot theo collection_id. Đây là tài liệu tầng công cụ. Vòng điều phối và giao diện chat đã được bổ sung sau bước này: xem [Agent và chat](DOT_5_AGENT_VA_CHAT.md). Model mặc định giữ gpt-6-luna, reasoning low như bước kết nối.

## Công cụ công bố

| Công cụ | Câu hỏi hỗ trợ | Giới hạn |
| --- | --- | --- |
| search_collections | Có dữ liệu nào đã lưu ở địa điểm/thời gian này? | Tìm metadata trong kho, tối đa 10 bộ/trang; không phải tìm toàn bộ CDS |
| describe_collection | Bộ này có nguồn, biến, thời gian và lưới nào? | Đúng collection_id; tối đa 20 lựa chọn lưới, báo nếu bị cắt |
| read_collection_quality | Bộ này có cảnh báo gì? | Tối đa 25 cảnh báo/trang, giữ tổng số và trang tiếp theo |
| analyze_collection | Thiếu gì, thống kê hoặc tổng hợp trong khoảng nào? | coverage/statistics/aggregate; một bộ, biến và lưới; khoảng dưới 366 ngày chênh lệch |
| usage_guide | Làm sao thu thập, xem dữ liệu, vẽ biểu đồ, đọc quality? | Hướng dẫn cố định theo chủ đề, không thực thi thao tác |

Ví dụ gọi từ bộ điều phối tương lai:

```python
from backend.app.ai.db_tools import build_database_tools

tools = build_database_tools(settings)
definitions = tools.definitions()
result = tools.execute('search_collections', {'search': 'Hà Nội', 'limit': 5})
```

## Quy tắc xử lý

- Dispatcher chỉ có năm tên công cụ cố định, schema strict cấm trường lạ; không nhận SQL, lệnh shell hoặc tên phương thức tùy ý. Repository mở read_only, không khởi tạo provider/CDS.
- Thiếu collection_id, biến hoặc khoảng giờ trả needs_clarification. Nhiều lưới phải chọn tọa độ thực; chỉ bộ có đúng một lưới mới suy ra được lưới đó. Không tự chọn bộ mới nhất thay người dùng. Tìm kiếm nhiều kết quả có selection_required.
- Ngày giờ ISO phải có múi giờ và tròn giờ; truy vấn phải nằm trong yêu cầu gốc. Không tự mở rộng hoặc cắt khoảng. Phân biệt tọa độ yêu cầu với lưới thực.
- Thống kê và tổng hợp do service tính. coverage không gửi chuỗi quan trắc cho model; aggregate chỉ trả tối đa 48 kỳ/trang. Danh sách thiếu trả tối đa 24 khoảng và công bố tổng số nếu bị cắt.
- Kết quả luôn có status và query_mode. Phân tích kèm nguồn, dataset, đơn vị, khoảng giờ, lưới, độ phủ và số lượng. Giữ null; no_data không được diễn giải thành 0. undefined hỗ trợ hướng gió không xác định.
- partial thể hiện thiếu quan trắc trong khoảng yêu cầu; partial_period của kỳ lịch được giữ riêng. Không coi một ngày trong tháng là số liệu cả tháng. Giới hạn quy ước giờ kết thúc tích lũy của service hiện tại vẫn áp dụng.
- Khi bộ có suspicious_value, thống kê/tổng hợp nhiệt độ và điểm sương trả quality_review_required, không trả số có thể sai đơn vị. Đây là chặn bảo thủ cho cả bộ, chưa lọc cảnh báo theo từng giờ/lưới. coverage và quality vẫn xem được; không tự đoán sửa đơn vị.
- Mock được đánh dấu synthetic, bộ đang chạy được đánh dấu provisional. Tên địa điểm và nội dung cảnh báo là dữ liệu không đáng tin (untrusted_text), không phải chỉ dẫn cho Agent. Không trả đường dẫn file nguồn đầy đủ, chỉ basename cảnh báo.
- Lỗi DB/tham số trả trạng thái có kiểm soát, không trả SQL, traceback hoặc nội dung lỗi upstream. Không có công cụ tải, tạo/hủy/xóa job hay xóa dữ liệu.

## Kiểm chứng

34 test riêng trong tests/test_ai_db_tools.py đã đạt: phân biệt snapshot ERA5/Land; cấm gọi provider/CDS; thiếu tham số; grid mơ hồ; giới hạn/trang; datetime sai; bộ không tồn tại; dữ liệu thiếu/rỗng; nhiệt độ nghi vấn đơn vị; hướng gió không xác định; công cụ ngoài danh sách và yêu cầu SQL.

Các test chạy offline bằng fixture. Chúng xác nhận tầng công cụ, chưa chứng minh model luôn chọn đúng công cụ hoặc chống mọi prompt injection. Bước tiếp theo cần bộ điều phối với giới hạn lượt gọi, xử lý needs_clarification và kiểm tra câu trả lời dựa trên kết quả công cụ trước khi nối UI. Không gọi API AI trả phí trong bước này.

Kiểm tra toàn dự án tại output/ai-db-tools-baseline: 427 passed, 1 skipped (live CDS chưa bật), một cảnh báo deprecation của TestClient. Backend/frontend trả HTTP 200; Streamlit AppTest đạt, không có exception/error; user_inputs_unchanged=true. git diff --check đạt. Chưa commit/push.
