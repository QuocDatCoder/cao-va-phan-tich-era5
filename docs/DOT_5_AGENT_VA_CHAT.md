# Đợt 5 — Agent và khung chat

## Thiết kế đã triển khai

Agent dùng Responses API và năm công cụ DB-only, cùng ask_user/finish để điều phối. Theo yêu cầu mở rộng ARE, đã thêm project_knowledge để tra cứu code/tài liệu và answer_question cho hội thoại phổ thông/giải thích dự án. Model chọn công cụ/tham số. Số liệu quan trắc vẫn được dựng trực tiếp từ service; chỉ nhánh phổ thông/dự án mới nhận câu trả lời ngôn ngữ từ model. Không có quyền thực thi SQL/command, CDS, tạo/hủy/xóa job hoặc tự tải dữ liệu.

## Mở rộng hội thoại và kiến thức dự án

ARE giới hạn vào dự án và khí tượng liên quan. Xã giao ngắn dùng câu do server quản lý, không hỏi ngược kéo dài. Bộ lọc phạm vi bảo thủ chạy trước model: câu ngoài phạm vi nhận thông báo cố định, không gọi AI hoặc DB. Nhánh answer_question có category unsupported và kiểm tra lại phạm vi trước khi hiển thị văn bản; model không được dùng category project để hợp thức hóa câu ngoài phạm vi. Câu nối tiếp ngày/chu kỳ/tọa độ và câu trả lời địa điểm khi đang được hỏi có thể kế thừa ngữ cảnh liên quan. Đây là bộ lọc quy tắc kết hợp hướng dẫn model, không phải bộ phân loại ngữ nghĩa hoàn hảo: cách diễn đạt chưa được nhận diện có thể bị từ chối. Không tìm web trực tiếp hoặc hứa biết thời tiết hiện tại.

Kiểm tra khóa phạm vi: baseline output/are-scope-final-baseline đạt 504 test, 1 test CDS bỏ qua; sau chỉnh cuối và bổ sung kiểm tra ngữ cảnh, output/scope-final-targeted-2 đạt 67 test. Chromium output/are-scope-refusal-browser xác nhận câu “Tôi muốn ăn cơm tấm” bị từ chối trong panel, sau đó vẫn giải thích dự án và mở dữ liệu/biểu đồ bình thường. Không có browser error, không gọi AI trả phí/CDS. Lịch sử cũ được giữ theo yêu cầu, không viết lại hoặc xóa câu trả lời đã lưu.

Nhánh dự án tra chỉ mục 12 chủ đề: kiến trúc, snapshot/DB-only, schema, job/hủy, quality/đơn vị, tổng hợp/thời gian, gió, thống kê/trend, đồng bộ, ARE, UI và kế hoạch/nghiệm thu. Mỗi chủ đề có tóm tắt thiết kế và trích đoạn hiện tại từ file được cho phép, kèm số dòng. Không nhận đường dẫn do model/người dùng cung cấp, không đọc .env/auth hoặc quét toàn repo. Mỗi lượt tối đa ba chủ đề, mỗi trích đoạn tối đa 1.500 ký tự; file quá lớn, mất hoặc không đọc được được bỏ qua. Đây là truy hồi cục bộ bằng chỉ mục từ khóa, không thêm embedding/provider/vector DB; phải cập nhật tóm tắt khi kiến trúc đổi.

Giải thích dự án phải có source_ids lấy từ lượt tra cứu hiện tại; nguồn giả hoặc trả lời dự án dưới nhãn phổ thông bị chặn và yêu cầu chọn lại nhánh. Giao diện hiển thị Markdown an toàn và Nguồn trong dự án; không bật HTML, không nhúng ảnh ngoài hay hiển thị JSON/debug. Phân biệt điều code thể hiện với nhận xét/suy luận; không tuyên bố đọc hết repo hoặc đã chạy test mới từ tài liệu cũ.

Truy vấn bộ cụ thể/giá trị đã lưu vẫn dùng DB tools. Khi đã đọc DB trong lượt, answer_question không được viết lại các số đó bằng văn bản model. Có bộ lọc bảo thủ tiếng Việt/Anh để chặn câu dữ liệu thực tế đi vào nhánh phổ thông, đồng thời cho phép khái niệm như “độ phủ là gì”. Đây là lớp bổ sung cho prompt và allowlist, không phải chứng minh mọi cách diễn đạt/injection đều được phân loại đúng. Giới hạn: model vẫn có thể hiểu sai ý định hoặc giải thích sai nguồn; citation cho phép người dùng đối chiếu. Nhánh phổ thông không có xác minh kiến thức độc lập.

Phản hồi bổ sung answer_kind (general/project/data/social/unsupported), grounding (model_knowledge/project_sources/db_only/scope_policy), completed cho việc hoàn tất lượt. verified chỉ áp dụng nhánh dữ liệu; không gán cờ xác minh DB cho lời giải thích của model. Pet dùng completed để biểu thị lượt trả lời xong. Xã giao và câu bị chặn trước model có request_count=0. Giữ model/reasoning/budget theo cấu hình, không tự tăng output hoặc retry.

Kiểm chứng: output/are-expanded-final-baseline có 461 passed, 1 skipped (live CDS), backend/frontend HTTP 200, AppTest đạt và user_inputs_unchanged=true. Sau bổ sung hai chủ đề thống kê/đồng bộ và xử lý file nguồn không đọc được, chạy lại test chỉ mục/hội thoại. Chromium + agent API ở output/are-expanded-browser đã thử cả chào hỏi và lời giải thích dự án, Markdown, nguồn, không JSON; luồng DB/biểu đồ và desktop/mobile/màn hình thấp vẫn đạt. Các lượt UI này hoàn toàn offline, không gọi AI trả phí/CDS.

Lượt thử thật tại output/are-expanded-live-1/report.json: Luna/low đạt 6/6 gồm chào hỏi, câu phổ thông, kiến trúc, lỗi Kelvin/Celsius, công thức gió và yêu cầu bịa số liệu. Tổng 10 request, upstream báo 27.802 token, tên model trả về khớp cấu hình (không phải chứng thực model nội bộ). Đã đọc câu trả lời thực tế: giải thích dự án có nguồn, câu bịa nhiệt độ quay về service với 26,85°C trên fixture 300 K. Không dùng DB thật, không gọi CDS, không tự retry. Đây là tập thử nhỏ, không phải bảo đảm mọi câu hỏi đều chính xác.

Mỗi câu hỏi tối đa 4 request AI, một function call/request, không retry tự động. Kiểm tra ngân sách thời gian trước mỗi request sau 95 giây; request đang chạy có timeout riêng (mặc định 30 giây), nên đây không phải deadline cứng 95 giây. Lịch sử gửi tối đa 8 tin, mỗi tin 2.000 ký tự; input hiện tại cũng tối đa 2.000 ký tự. Chỉ vai trò user/assistant được nhận từ client. Chỉ kết quả công cụ của lượt hiện tại được coi là bằng chứng số liệu; lịch sử không phải cache số liệu đáng tin.

Endpoint GET /api/v1/agent/status chỉ đọc cấu hình, không gọi model. POST /api/v1/agent/chat mới gọi AI. Key chỉ ở backend, không gửi xuống browser. Mở trang, mở/đóng chat hay chuyển tab không tự gọi AI. Chỉ gửi tin hoặc bấm câu gợi ý mới phát sinh lượt AI. Câu hỏi, lịch sử giới hạn và metadata/kết quả công cụ có thể được gửi đến gateway đã cấu hình; giao diện công bố việc này.

## Giao diện

Cập nhật bố cục laptop: panel rộng 460px, cao tối đa 640px và tự giới hạn theo viewport; vùng hội thoại dùng phần chiều cao còn lại, ô nhập tách ở đáy. Pet/panel desktop cách mép phải 52px, pet cách đáy 52px; dịch trái 28px và lên 32px so với bản trước. Mobile giữ lề 12px cho panel, pet lùi nhẹ vào trong; màn hình thấp giữ ô nhập trong viewport. Kiểm tra 5 test UI đạt ở output/are-laptop-ui-tests; running-app agent API và Chromium tại output/are-laptop-layout-verified đạt trên 1366×768, 1280×900, 390×844 và 700×500, gồm chat nền, nguồn/độ phủ, mở biểu đồ và hội thoại dự án. Đã xem ảnh thực tế; không có browser error hay cảnh báo frontend mới, không gọi API AI trả phí/CDS trong lần chỉnh bố cục này.

- Nút AI chat cố định góc phải dưới, mở khung chat phía trên; không chiếm một tab mới hay đẩy bố cục trang.
- Dùng native Streamlit chat/input/container; CSS chỉ để đặt khung nổi, giới hạn kích thước và thích ứng mobile, không thêm thư viện UI.
- Request chạy trong worker; chỉ khi đang chờ, component phát sự kiện kiểm tra hoàn tất mỗi giây. Lúc rảnh không tự render lại chat để tránh đóng bộ chọn đang mở. Có trạng thái chờ, chặn gửi trùng khi đang chạy. Thu gọn chat vẫn tiếp tục xử lý, người dùng thao tác trang bình thường.
- Lịch sử và bộ được chọn giữ qua đóng/mở, chuyển tab và F5 bằng localStorage của cùng trình duyệt/địa chỉ API. Không đưa chat vào URL hay cache dùng chung trên server. Chỉ hiển thị 40 tin gần nhất để nhẹ UI, có Xem tin trước; không tự xóa các tin cũ. Context gửi model vẫn giới hạn 8 tin như trước.
- Kết quả tìm kiếm có Dùng bộ này; cũng có Hỏi về bộ đang xem khi đang ở Kho dữ liệu/Biểu đồ. Không tự trộn hai bộ hoặc ngầm đổi ngữ cảnh theo tab.
- Có nguồn, dataset, khoảng giờ/lưới, kết quả công cụ và nút Mở trong Biểu đồ. Mở biểu đồ qua hành động UI có chủ ý, thu gọn chat và giữ lịch sử. Đã hỗ trợ áp dụng tham số đã kiểm tra qua nút trong chat; thiếu tham số có form lựa chọn. Xem [hợp đồng tương tác UI](DOT_5_ARE_TUONG_TAC_GIAO_DIEN.md).
- Nút Làm mới trong menu chat xóa lịch sử đã lưu và bộ đang hỏi, không xóa dữ liệu khí tượng. Nút Làm mới ở trang Kho dữ liệu/Biểu đồ không ảnh hưởng chat. Lỗi mạng/cấu hình không tự gửi lại.

### Lưu lịch sử qua F5

frontend/chat_history.py dùng một cầu nối Custom Components v2 nội tuyến để đọc/ghi localStorage; không thêm thư viện hoặc API lưu chat. JavaScript là mã cố định, lịch sử chỉ truyền dưới dạng data, không chèn vào HTML/JS thực thi. Key tách theo backend API. Lưu nội dung, nguồn/chi tiết nhìn thấy, bộ được chọn, diện mạo và trạng thái mở; bỏ bản sao arguments, usage và trích đoạn project_knowledge không cần cho UI. Chỉ ghi khi nội dung/trạng thái đổi, không tự hết hạn hoặc cắt lịch sử theo số tin.

Khôi phục được xác thực cấu trúc trước khi đưa vào UI; dữ liệu browser không phải bằng chứng DB cho Agent. Nếu lịch sử hỏng/chặn storage/hết quota, hiển thị lỗi, không âm thầm ghi đè lịch sử cũ bằng rỗng. Dữ liệu chỉ ở profile/trình duyệt và origin hiện tại, không đồng bộ qua máy khác; người dùng xóa dữ liệu trình duyệt cũng có thể làm mất lịch sử. Không phải tính năng backup hay tài khoản đăng nhập.

F5 trong lúc chờ giữ câu hỏi và báo lượt trước chưa lưu xong; không tự gọi lại model hoặc tạo tác vụ. Không hứa nối lại Future của phiên Streamlit đã đóng. Nút Làm mới tạo revision riêng để bảo đảm thao tác xóa không bị bỏ qua vì một ACK lưu cũ. Nút bị khóa khi lượt hiện tại còn xử lý như trước.

Kiểm chứng: toàn bộ baseline tại output/are-history-baseline có 469 passed, 1 skipped (live CDS), backend/frontend HTTP 200, AppTest đạt, user_inputs_unchanged=true. Sau sửa cảnh báo khôi phục date_input và bổ sung kiểm tra phân trang lịch sử, 59 test UI/workflow/history đạt ở output/are-history-final-ui; kiểm tra cuối cho hội thoại/UI/history ở output/are-history-verified-tests đạt 29 test trước bổ sung token reset, đã chạy lại nhóm này sau sửa. Chromium tại output/are-history-browser-verified xác nhận F5 giữ hội thoại/citation, làm mới trang chính giữ chat, Làm mới trong chat xóa và F5 không hồi lại, F5 khi chờ không tự gửi lại. Không có browser error và không gọi API AI trả phí/CDS. Trên Windows có thể thấy ConnectionResetError 10054 của asyncio khi browser ngắt kết nối để reload; không có lỗi app hay thất bại phục hồi trong kịch bản đã chạy. Cảnh báo giá trị mặc định widget khi phục hồi đã được sửa ở bộ lọc ngày và diện mạo pet.

## Cập nhật giao diện mini chat Mây

Nút robot đã được thay bằng pet đám mây SVG có mắt, má hồng, chuyển động nhẹ; nắng có tia xoay, mưa có giọt rơi. SVG đóng gói dưới dạng ảnh data URI để tương thích bộ lọc HTML của Streamlit, không dùng JavaScript hay tải ảnh ngoài. Tôn trọng prefers-reduced-motion; các biến thể hữu hạn được cache.

Khung chat 400px trên desktop, co theo màn hình mobile. Tiêu đề chỉ có Mây, menu ba chấm và thu gọn. Chức năng cuộc trò chuyện mới, thông tin API/phiên và chọn diện mạo nằm trong menu; JSON/lưới/dataset nằm trong Chi tiết dữ liệu, ngày giờ ngoài hội thoại hiển thị gọn. Ô nhập luôn bên dưới vùng hội thoại riêng. Màn hình thấp có vùng tin nhắn ngắn hơn, pet tạm ẩn khi panel mở để không che ô nhập; đóng panel pet xuất hiện lại.

Menu cho chọn Tự động/Mây/Nắng/Mưa, giữ lựa chọn trong phiên. Tự động dùng mây khi nghỉ, mưa khi xử lý hoặc lỗi, nắng khi đã trả lời thành công. Đây là trạng thái giao diện trợ lý, không phải thời tiết thật hay dự báo. Có dấu báo khi câu trả lời hoàn tất lúc chat đang thu gọn; mở chat sẽ xóa dấu báo.

Kiểm chứng cập nhật: output/cloud-pet-baseline có 447 passed, 1 skipped (live CDS), backend/frontend HTTP 200 và AppTest đạt, user_inputs_unchanged=true. Sau chỉnh riêng CSS responsive/cache, chạy lại 4 test UI đều đạt tại output/cloud-pet-final-ui-test. Running-app agent API + Chromium tại output/cloud-pet-responsive-check xác nhận ảnh pet tải được, đổi đủ 3 dạng, tự chuyển mưa/nắng, chat nền/chuyển tab/mở biểu đồ vẫn hoạt động; desktop, mobile và màn hình thấp 700×500 đều giữ ô nhập trong viewport. Đã xem ảnh welcome/desktop/mobile/menu/màn hình thấp; không có browser error, không gọi API AI trả phí/CDS trong lần sửa UI này.

## Câu hỏi hỗ trợ để demo

1. Kho đang có những bộ dữ liệu nào? / Có bộ nào ở Hà Nội?
2. Bộ đã chọn có biến, nguồn và thời gian nào?
3. Nhiệt độ ngày 01/01/2025 theo UTC+7 có thiếu giờ nào không?
4. Tính trung bình, min, max nhiệt độ trong khoảng ngày cụ thể.
5. Tổng hợp lượng mưa theo ngày trong khoảng cụ thể (nếu bộ có biến đó).
6. Bộ này có cảnh báo chất lượng gì?
7. Làm sao thu thập, xem dữ liệu hoặc vẽ biểu đồ?

Thiếu bộ/ngày/biến/lưới thì hỏi lại. Không có dữ liệu trả no_data; hướng gió không xác định giữ null. Có suspicious_value thì tạm chặn thống kê nhiệt độ/điểm sương. Nguồn mock và snapshot đang chạy phải được phân biệt với dữ liệu thật/hoàn tất. Phạm vi chi tiết ở DOT_5_CONG_CU_DB_ONLY.md.

## So sánh thật Luna và Sol (17/11/2026)

Chạy cùng Agent, cùng schema, cùng tám câu hỏi, fixture SQLite riêng, reasoning low, không dùng DB thật và không gọi CDS. Sol được thử là gpt-6.1-sol (tên hiện có trong cấu hình người dùng), Luna là gpt-6-luna. Báo cáo: output/ai-agent-comparison-3/report.json.

| Chỉ tiêu | gpt-6-luna | gpt-6.1-sol |
| --- | ---: | ---: |
| Tình huống đạt | 8/8 | 8/8 |
| Số request AI | 13 | 13 |
| Thời gian trung bình/câu | 4,414 giây | 6,016 giây |
| Input token upstream báo | 19.095 | 19.095 |
| Output token upstream báo | 566 | 550 |
| Tổng token | 19.661 | 19.645 |

Tình huống: inventory, thống kê đúng ngày UTC+7 (mean 26,85°C từ fixture 300 K), hỏi lại ngày còn thiếu, bộ rỗng, nhiệt độ nghi vấn đơn vị, độ phủ, hướng dẫn biểu đồ, từ chối yêu cầu SQL/xóa/tải ngầm/bịa số. Chấm bằng công cụ/trạng thái/phép tính mong đợi, không dùng LLM làm giám khảo. Renderer chặn văn bản tự do, nên kết quả này đánh giá chọn công cụ/tham số trong thiết kế hiện tại, không chứng minh năng lực hội thoại tự do hay chống mọi prompt injection.

Giữ Luna/low làm mặc định vì nhanh hơn khoảng 27% trong lượt này, chất lượng công cụ ngang nhau trong tập nhỏ. Token gần như bằng nhau; không thể kết luận Luna luôn rẻ hơn. Endpoint giá công khai https://modelapi.vn/api/pricing lúc kiểm tra có biểu thức standard Sol p*2+c*10, Luna p*2+c*12; hệ số output Sol thấp hơn. Nhóm, cache, đơn vị tính của biểu thức và biểu phí thực tế cần đối chiếu dashboard để biết tiền bị trừ. Không suy diễn tiền thật từ tổng token hoặc tên Luna. Tên model upstream trả về cũng không phải bằng chứng độc lập về model nội bộ gateway.

Lượt so sánh thành công upstream báo tổng 39.306 token. Trước đó một lượt script lỗi ghi báo cáo sau request đầu đã dùng thêm 1.340 token; lỗi đã sửa và test offline trước khi chạy lại. Tổng token được ghi nhận cho công việc so sánh: 40.646. Không có auto retry hay tăng reasoning/output budget. Chưa thử lặp đủ nhiều để kết luận p95, độ ổn định dài hạn hoặc chất lượng trên mọi dữ liệu.

## Kiểm tra

- Offline toàn dự án bản cuối ở output/ai-chat-final-baseline: 445 passed, 1 skipped (live CDS), một cảnh báo deprecation TestClient cũ; backend/frontend HTTP 200, Streamlit AppTest đạt, user_inputs_unchanged=true, blocked_events rỗng. Sau kiểm tra này sửa riêng ngày hiện tại của ngữ cảnh Agent sang UTC+7 tường minh và chạy lại test Agent.
- Test riêng Agent: protocol, giới hạn lượt, lỗi upstream không lộ body/key, không retry, tham số/API không cho system role/SQL, số liệu/empty/warning.
- Test UI: không gọi AI khi chỉ mở trang/chat, giữ lịch sử và bộ chọn qua tab, request nền hoàn tất khi chat đóng, không gửi trùng.
- Running-app agent API và Chromium thật bản cuối ở output/ai-chat-browser-final: desktop 1280×900, mobile 390×844; chat nổi trong viewport; đóng khi chờ, chuyển tab, nhận kết quả, mở đúng bộ ở Biểu đồ và mở lại còn lịch sử. Không có browser error, không gọi AI trả phí/CDS trong kiểm tra UI. Ảnh desktop.png và mobile.png; log frontend không có traceback/cảnh báo mới.

Chưa commit/push. Các kết quả offline/fixture không thay thế nghiệm thu bằng hội thoại trên bộ ERA5 thật của đồ án; tránh công bố rằng toàn bộ đợt 5 đã được nghiệm thu trong mọi trường hợp.


Cập nhật tên ARE: đổi tên trợ lý/nhãn pet/ô nhập; bỏ phụ đề, lời giải thích trên màn chào, chân chat và các đoạn giải thích trong menu. Thu pet còn 80×88px (mobile 72×80px), canh panel và pet cùng mép phải, khoảng cách 12px; panel desktop 384px. Giữ nguồn, thời gian và cảnh báo cần thiết trong kết quả. Kiểm tra lại 4 test UI và Chromium desktop/mobile/màn hình thấp đều đạt ở output/are-chat-ui-tests và output/are-mini-chat-review; không gọi API AI trả phí/CDS.
