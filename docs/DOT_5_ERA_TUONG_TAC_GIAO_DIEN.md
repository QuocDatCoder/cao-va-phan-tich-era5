# ERA tương tác với giao diện

Đã nối ba luồng đọc dữ liệu: tìm trong kho, mở thông tin/bảng dữ liệu và mở Biểu đồ theo bộ lọc. Model đề xuất hành động; chỉ khi người dùng bấm nút ứng dụng mới chuyển trang. Không có công cụ tạo job, tải CDS, xóa dữ liệu, thực thi SQL/command hoặc chạy JavaScript/URL từ model.

## Ngữ cảnh và công cụ

POST /api/v1/agent/chat nhận thêm view_context (AppliedView): trang hiện tại, collection_id, loại biểu đồ và parameters đã áp dụng. Frontend chỉ lấy parameters khi kết quả đang hiển thị và tuple *_applied khớp collection_id; không đọc biến/ngày/giờ đang sửa dở trong form. Ở danh sách không có ngữ cảnh bộ lọc của tab ẩn. Chọn bộ khác không được kế thừa ngày/biến/lưới từ bộ cũ.

prepare_view có target open_data/open_chart, collection_id, variable, latitude/longitude, start/end, frequency, view_kind. ID/biến/lưới/khoảng giờ được xác minh qua service DB-only. Timestamp phải tròn giờ quan trắc UTC, có timezone; không tự cắt hay mở rộng khoảng yêu cầu. Chỉ bộ có một lưới mới suy ra được lưới đó; tọa độ thiếu một thành phần phải hỏi lại. Dữ liệu gió suy dẫn được hỗ trợ khi bộ có cả u và v.

view_kind hỗ trợ timeseries/heatmap/map/statistics theo các kiểu có sẵn của ứng dụng. Biểu đồ nhiệt độ theo ngày/tháng/năm là timeseries. Heatmap chỉ theo tháng; nếu model ghép heatmap với chu kỳ ngày, hệ thống trả route_required để model sửa lựa chọn, không tự đổi ngày thành tháng. Số liệu null, thiếu giờ và kỳ lịch chưa trọn vẫn theo service cũ. Chặn sẵn biểu đồ nhiệt độ/điểm sương có suspicious_value hoặc khoảng không có giá trị xác định.

## Nút và form trong chat

- Kết quả tìm kiếm có Xem dữ liệu, Biểu đồ và Chọn và tiếp tục. Hai nút mở UI là đọc dữ liệu, không gọi AI. Chọn và tiếp tục gửi yêu cầu tiếp nối có chủ ý với ID vừa chọn; chống gửi trùng trong lúc đang xử lý.
- Mở kết quả trong Kho dữ liệu chuyển sang danh sách, điền từ khóa, mở danh sách và cuộn đến đó.
- Nút chung trên một bộ khi chưa có bộ lọc cụ thể mở metadata và form chọn, không tự truy vấn/vẽ lưới đầu tiên. Người dùng áp dụng bộ lọc theo luồng thông thường. Nếu bộ đã có kết quả được áp dụng trong tab đó, kết quả cũ vẫn có thể được xem.
- Yêu cầu đã đủ tham số có nút mở kết quả chính xác. Bấm sẽ đọc dữ liệu, đặt đúng bộ/biến/lưới/ngày/giờ/chu kỳ/loại, thu danh sách và bộ lọc, thu gọn chat, cuộn đến bảng hoặc biểu đồ.
- Thiếu biến, lưới, ngày/giờ hoặc chu kỳ có form ngay trong chat. Mở kết quả từ form chỉ kiểm tra/truy vấn DB, không cần gọi lại AI. Ngày bắt buộc thuộc bộ; không mặc định tự lấy toàn khoảng khi người dùng chưa nói rõ.

## Xử lý phía ứng dụng

DTO tại backend/app/domain/ui_actions.py chỉ cho find_data/open_data/open_chart, cấm trường thừa và chỉ cho kiểu view/chu kỳ cố định. Phản hồi Agent có actions là dữ liệu, không phải code thực thi.

POST /api/v1/agent/resolve-action không phụ thuộc key/model AI, dùng service DB-only và kiểm tra lại metadata lúc bấm. frontend/chat_actions.py lấy metadata/kết quả trước rồi mới thay trạng thái UI; timeout đọc tối đa 15 giây mỗi request, không retry. ID bị xóa, lưới/biến sai, khoảng ngoài bộ, cảnh báo đơn vị hoặc query lỗi giữ nguyên trang/kết quả/bộ lọc đã áp dụng trước đó, báo lỗi trong chat. Tab còn lại giữ kết quả và URL applied riêng. Snapshot/read không phải một giao dịch phân tán khóa nhiều request; thay đổi đồng thời sau kiểm tra vẫn có thể khiến lần đọc sau lỗi, khi đó không commit UI.

Đã thêm giờ bắt đầu/kết thúc vào bộ lọc để giữ chính xác các yêu cầu trong ngày (ví dụ 09:00–15:00); không chỉ điền ngày rồi mở rộng thành cả ngày. Khi chỉ có một điểm/kỳ tổng hợp, trục biểu đồ có một nhãn thời gian đọc được, tránh autoscale thành các mốc mili-giây.

Hành động và dữ liệu hiển thị được giữ trong lịch sử localStorage. Bấm lại nút cũ sau F5 vẫn phải kiểm tra DB mới; không tự áp dụng lại hành động khi khôi phục lịch sử.

## Kiểm thử và giới hạn

Bộ chọn Kho dữ liệu, Biểu đồ và form ERA chỉ hiển thị tối đa 6 biến chính có sẵn: nhiệt độ, lượng mưa, điểm sương, tốc độ gió, hướng gió, bức xạ mặt trời. Alias được gộp, gió u/v vẫn lưu và xuất hiện trong thông tin biến bổ sung, không nằm trong bộ chọn chính. Chỉ bổ sung tốc độ/hướng gió khi có cả hai thành phần. Hành động mở kết quả ERA từ chối biến nội bộ; dữ liệu và API phân tích nền không bị xóa.

Test kiểm tra DTO chặn script/URL/SQL, scope ngày/biến/lưới, không kế thừa bộ khác, nhiều lưới cần lựa chọn, null/rỗng/đơn vị nghi vấn, đúng khoảng 7 giờ, chỉ query một lần, lỗi sau validate giữ kết quả cũ và mở tab khác không làm mất applied của tab trước. Test UI kiểm tra form trong chat không gọi AI, search mở danh sách và generic view không tự query.

Baseline output/era-actions-complete-baseline: 495 passed, 1 skipped (live CDS), backend/frontend HTTP 200, AppTest đạt và user_inputs_unchanged=true. Các kiểm tra bổ sung cho thao tác atomic/UI/history tại output/era-actions-idle-tests: 39 passed. Running-app agent API/Chromium kiểm tra tìm kiếm, mở metadata, áp dụng chu kỳ ngày từ context, form thiếu tham số và các luồng F5/làm mới/không replay; ảnh và log trong output/era-actions-idle-browser-final. Kiểm tra UI này không gọi API AI trả phí/CDS, không có browser error. Chat chỉ phát sự kiện kiểm tra mỗi giây khi có request đang chạy; đã kiểm tra bộ chọn còn mở sau 1,8 giây lúc rảnh. Timer được dọn khi component unmount.

Thử thật Luna/low: output/era-actions-live-verified/report.json đạt 3/3 (context → biểu đồ theo ngày, tìm bộ, hỏi lại khi thiếu thời gian/chu kỳ), upstream báo 9.986 token. Lượt đầu 7.005 token phát hiện model nhầm heatmap và fixture không giữ tên vì chưa có selected_place; đã sửa cả rule/validation và fixture, rồi chạy lại. Không tự retry hoặc tăng reasoning/output budget. Đây chỉ là tập thử nhỏ, không bảo đảm mọi cách diễn đạt đều chọn đúng kiểu biểu đồ; nút và tham số vẫn để người dùng kiểm tra trước khi mở.

Tìm kiếm hiện dùng từ khóa metadata, không phải tìm dữ liệu toàn cầu trên CDS hay bộ lọc ngôn ngữ tự nhiên nhiều điều kiện hoàn chỉnh. Hỗ trợ phân tích tương tác bằng các operation có sẵn; chưa hỗ trợ click đỉnh biểu đồ để chat tự lấy điểm, tạo job từ chat hoặc tự thay biểu đồ khi chưa bấm hành động.
