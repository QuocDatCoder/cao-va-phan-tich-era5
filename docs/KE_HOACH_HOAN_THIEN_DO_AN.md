# Kế hoạch hoàn thiện đồ án hỗ trợ thu thập dữ liệu khí tượng

Kế hoạch lập ngày 16/11/2026, dựa trên mã nhánh Tuan_Kiet tại thời điểm rà soát. Đây là các việc cần làm, không phải danh sách chức năng đã hoàn thành. Thông tin số năm và số địa điểm demo cần xác nhận với giảng viên.

## 1 Mục tiêu nghiệm thu

Người dùng chủ động chọn nguồn, biến, địa điểm và khoảng ngày để thu thập từ CDS. Hệ thống kiểm tra dữ liệu, cảnh báo cụ thể, lưu dữ liệu hợp lệ cùng thông tin chất lượng vào DB. Người dùng có thể truy vấn dữ liệu đã lưu, tải CSV, xem biểu đồ theo giờ/ngày/tháng/năm và hỏi một nhóm câu hỏi được chatbot hỗ trợ.

Giữ FastAPI, Streamlit, SQLite và hai provider hiện có. Chưa thay framework, chưa thêm Docker, tài khoản người dùng, dự báo thời tiết hoặc huấn luyện mô hình. Chưa bổ sung biến sóng biển nếu giảng viên chưa yêu cầu. Không thay toàn bộ hệ thống chỉ để làm giao diện đẹp hơn.

## 2 Hiện trạng cần tận dụng

- Có provider ERA5 single levels và ERA5-Land time-series, đọc NetCDF/CSV, chuẩn hóa đơn vị và lưu SQLite.
- Có API tạo download job, metadata job, API truy vấn/phân tích và kiểm thử.
- Có biểu đồ và tổng hợp theo lịch, CSV tải từ giao diện.
- Truy vấn hiện có thể tự tải phần thiếu từ CDS; chưa tách sạch tải chủ động với đọc DB.
- Provider được chọn qua cấu hình tiến trình; chưa cho người dùng lựa chọn nguồn theo từng yêu cầu một cách xuyên suốt.
- Parser Land bỏ qua một số ô lỗi/không hữu hạn; chưa cung cấp báo cáo warning theo hàng/cột.
- DownloadRequest hiện có một variable và area. Land dùng tâm area làm một điểm; không được trình bày như tải toàn bộ vùng đất liền.
- Chưa thấy chatbot hoàn chỉnh trong backend/frontend được rà soát.

## 3 Nguyên tắc làm việc với AI lập trình

Mỗi đợt chỉ làm một mục dưới đây, xem diff và chạy kiểm thử liên quan rồi mới chuyển tiếp. Không yêu cầu AI sửa toàn bộ repo trong một prompt. Không tự push, merge hay cập nhật báo cáo trước khi kiểm chứng hành vi mới.

Prompt nền dùng trước mỗi đợt:

```text
Bạn đang làm việc trong repository cao-va-phan-tich-era5. Đọc mã hiện tại trước khi sửa.
Mục tiêu là hỗ trợ người dùng thu thập dữ liệu, kiểm tra chất lượng, lưu DB rồi truy vấn.
Tận dụng FastAPI, Streamlit, SQLite và provider hiện có; giữ dữ liệu người dùng.
Không đọc/in token .env, không commit credentials, không xóa DB hay file tải thật.
Không thay dữ liệu thiếu bằng 0, không tạo số liệu hoặc báo cáo kiểm thử giả.
Không tự gửi yêu cầu CDS thật trong kiểm thử offline.
Giữ phân biệt mock với dữ liệu thật và dataset đất liền với dataset ERA5 chung.
Chỉ sửa phạm vi được giao, thêm migration tương thích nếu cần, chạy kiểm thử liên quan.
Kết thúc bằng danh sách thay đổi, kiểm thử thực chạy, phần chưa làm và cách chạy thử.
```

## 4 Đợt 0 Chốt nền và điểm khôi phục

Đọc git status, commit hiện tại và README; lập danh mục các file liên quan. Tạo nhánh làm việc riêng từ Tuan_Kiet nếu đang đúng bản muốn phát triển. Chỉ commit file code cụ thể, không git add toàn bộ output/ hoặc tmp/. Giữ nguyên .env và dữ liệu tải.

Chạy bộ kiểm thử offline hiện tại và lưu log mốc nền. Không tái sử dụng con số 188 từ bản cũ như kết quả của nhánh mới. Nếu có lỗi, phân loại lỗi môi trường, kiểm thử không còn khớp giao diện hay lỗi nghiệp vụ trước khi sửa.

Đạt khi: có mốc code và log có thể truy lại, backend/frontend mở được, biết rõ các lỗi tồn tại trước thay đổi.

## 5 Đợt 1 Tách thu thập khỏi truy vấn

Đây là đợt ưu tiên cao nhất. Giữ API tải nền và hợp đồng đang dùng nếu có thể; thêm đường đọc DB rõ ràng hoặc một chính sách truy vấn có kiểu thay vì mặc định tự tải. Dashboard và chatbot mới chỉ dùng chế độ đọc dữ liệu đã lưu. Các API cũ tự tải có thể giữ để tương thích nhưng phải ghi rõ và không bị gọi ngầm từ giao diện mới.

Chốt một định danh nguồn dùng xuyên suốt job, truy vấn, CSV, cache và DB. Có thể dùng source = land | era5, hiển thị Đất liền và Biển trên UI; việc chọn nhãn Biển không biến ERA5 thành dataset chỉ chứa biển. Mỗi kết quả phải ghi dataset và điểm lưới thực. Không tự đổi file .env toàn cục khi người dùng đổi nguồn.

Land nhận điểm đúng tên trường, không giả tải cả vùng. Nếu thêm danh sách nhiều điểm, triển khai tối thiểu bằng cách xếp các job điểm tuần tự, chưa cần đa luồng. Tôn trọng việc user chọn biến: tải thêm biến nếu cần tính gió phải được ghi nhận và hiển thị. Nếu giữ tối ưu tải cùng một số biến, giải thích rõ biến yêu cầu và biến thực nhận.

```text
Thực hiện đợt 1 trong docs/KE_HOACH_HOAN_THIEN_DO_AN.md.
Đọc api/downloads.py, api/climate.py, schemas.py, provider dependencies,
climate_service.py và hai provider. Thiết kế thay đổi tối thiểu tương thích code cũ.
Cho phép lựa chọn nguồn theo yêu cầu và lưu nguồn trong job/query/cache.
Tách đọc SQLite khỏi cơ chế tự tải. Luồng truy vấn mới tuyệt đối không gọi CDS.
Khi thiếu dữ liệu, trả coverage và phần thiếu có cấu trúc, không giả thành công.
Giữ tác vụ tải chủ động, điểm Land và vùng ERA5 được mô tả chính xác.
Viết kiểm thử chặn mọi lời gọi CDS trong truy vấn DB-only, kể cả khi cache thiếu.
Kiểm tra không lẫn dữ liệu giữa hai dataset cùng tọa độ, biến và thời gian.
```

Đạt khi: bấm truy vấn khoảng chưa có dữ liệu chỉ báo thiếu; bấm thu thập mới tạo job. Khởi động lại backend vẫn truy vấn được phần đã lưu. Hai nguồn không dùng nhầm cache.

## 6 Đợt 2 Kiểm tra chất lượng và warning bền vững

Thêm báo cáo chất lượng gắn download job: số hàng đọc, số giá trị hợp lệ, số giá trị thiếu, số hàng lỗi thời gian, số trùng, số nghi vấn và số giá trị đã nhập. Mỗi issue có code, mức độ, biến, thời gian nếu đọc được, dòng/file gốc và lý do. Lưu tổng số cùng danh sách chi tiết có phân trang hoặc tệp đính kèm, không trả hàng triệu issue trong một JSON.

Các trường tối thiểu đề xuất: code, severity, source_file, row_number, variable, time_utc, message. Vị trí và dataset có thể kế thừa từ job. Phân biệt missing_value, invalid_number, invalid_time, missing_hour, duplicate_conflict và suspicious_value. Dữ liệu hợp lệ vẫn nhập; phần thiếu có dấu vết trong quality issues dù bảng quan sát dài không lưu một ô null riêng.

Quy tắc bảo toàn dữ liệu:

- Không biến ô thiếu thành 0, không nội suy tự động.
- Dòng thiếu mưa nhưng có nhiệt độ: giữ nhiệt độ và báo thiếu mưa.
- Không có dữ liệu hợp lệ hoặc file không đọc được: failed.
- Có dữ liệu hợp lệ nhưng còn thiếu: hoàn tất có cảnh báo; trạng thái job khác với độ phủ truy vấn.
- Có thể giữ enum completed cùng quality_status/has_warnings để giảm phá vỡ code; không bắt buộc thêm enum mới.
- Đồng bộ không được nâng mốc thành đủ nếu vẫn thiếu các biến/giờ yêu cầu.
- Giá trị vượt ngưỡng chỉ gắn cờ, không tự xóa. Ngưỡng phải có tên, đơn vị, lý do và phạm vi áp dụng.
- Hướng gió không xác định khi lặng gió không mặc nhiên là lỗi file; phải phân biệt với thiếu dữ liệu nguồn u/v.
- Bảo toàn file gốc và dấu vết biến đổi; không trộn thời gian/đơn vị gốc với hiển thị.

```text
Thực hiện đợt 2: thêm báo cáo chất lượng cho tải Land và ERA5, lưu cùng job.
Rà các đoạn continue/isfinite trong parser và các ràng buộc import/completeness.
Ghi rõ từng loại dữ liệu bị bỏ qua thay vì bỏ qua âm thầm.
Cho phép lưu phần hợp lệ, warning phần thiếu; không nâng mốc sync khi chưa đủ.
Migration không phá DB hiện có. Đừng thêm ngưỡng khí tượng tùy tiện.
Test file có một ô rỗng, NaN, dòng sai timestamp, cột thiếu, giờ thiếu,
dòng trùng xung đột và file rỗng. Kiểm tra dữ liệu tốt được giữ, warning tồn tại
sau restart, số lượng thống kê khớp, trường hợp không có dữ liệu tốt phải failed.
```

Đạt khi: file thử chỉ thiếu một ô tạo warning đúng biến và thời điểm, phần hợp lệ có trong DB, job không tuyên bố đủ dữ liệu sai.

## 7 Đợt 3 Giao diện theo đúng quy trình của người dùng

Streamlit tối thiểu gồm bốn mục: Thu thập, Dữ liệu đã lưu, Biểu đồ, Chatbot. Dùng điều hướng chỉ chạy mục đang chọn. Bộ lọc trong st.form và nút thực hiện giúp tránh gọi lại khi đang nhập từng trường.

Thu thập có nguồn đất liền/biển, điểm hoặc vùng được hỗ trợ, biến, khoảng ngày, nút bắt đầu và bảng job. Hiển thị đã nhận yêu cầu/đang chạy/hoàn tất/có cảnh báo/thất bại đúng nghĩa; chỉ hiển thị phần trăm nếu đo được. Chống bấm gửi trùng khi cùng yêu cầu còn chạy.

Dữ liệu đã lưu có danh mục dataset, địa điểm, biến, ngày đầu/cuối và độ phủ; cho lọc, xem bảng và xuất CSV. Khi thiếu: chỉ hiển thị nút người dùng chủ động xác nhận tải bổ sung, không tự tải.

Biểu đồ có giờ/ngày/tháng/năm, đơn vị, múi giờ, dataset và độ phủ. Kỳ chưa trọn phải có nhãn. Không suy ra một đường xu hướng dài hạn từ vài ngày. Giữ đúng quy tắc đại lượng tích lũy riêng từng dataset, xác minh trước khi sửa lớp tổng hợp dùng chung.

```text
Thực hiện đợt 3, dùng API và báo cáo chất lượng của hai đợt trước.
Tổ chức dashboard thành Thu thập, Dữ liệu đã lưu, Biểu đồ, Chatbot.
Chỉ render mục đang chọn, dùng form để tránh tải hoặc truy vấn theo mỗi lần nhập.
Hiển thị nguồn, điểm thực, đơn vị, UTC+7, độ phủ và warning có thể tải về.
Không gọi CDS khi vào mục Dữ liệu đã lưu hoặc Biểu đồ.
Giữ chức năng xuất CSV và giờ/ngày/tháng/năm. Chưa làm chatbot ở đợt này;
có thể chưa hiện mục Chatbot cho đến khi chức năng sẵn sàng, không đưa UI giả.
Test API client và hành vi quan trọng: đổi bộ lọc chưa submit không tạo job,
đọc biểu đồ không tải mới, gửi lại tác vụ đang chạy không tạo bản trùng.
```

Đạt khi: một người dùng mới làm được quy trình thu thập → đọc warning → truy vấn → xuất CSV → biểu đồ mà không dùng Swagger.

## 8 Đợt 4 Ổn định tải và dữ liệu cho demo

Tận dụng khóa CDS hiện có. Với một backend một worker, thêm cơ chế từ chối/gộp job trùng và xếp tải tuần tự đủ dùng cho demo. Không tuyên bố khóa trong tiến trình bảo vệ được nhiều worker hoặc nhiều máy. Để scheduler mặc định tắt.

Lỗi xác thực, giấy phép, validation và job đã bị xóa không được retry vô hạn. Lỗi tạm thời có retry giới hạn và khoảng đợi; hiển thị rõ thất bại hoặc còn thiếu. Khi khởi động lại, job dở phải được nhận diện; dữ liệu đã nhập không mất. Tránh làm distributed queue nếu phạm vi chỉ demo cục bộ.

Chuẩn bị sẵn một bộ thực nghiệm có nguồn gốc: dataset, vị trí yêu cầu và điểm lưới, biến, thời gian, file gốc, kết quả import, CSV, warning và ảnh UI. Chưa tải nhiều năm trên nhiều điểm trước khi xác nhận yêu cầu giảng viên. Có thể bắt đầu với 1 điểm đất liền và 1 điểm biển, khoảng ngắn đủ kiểm tra; đây là cấu hình thử kỹ thuật, không phải số lượng nghiệm thu đã chốt.

```text
Thực hiện đợt 4: rà điều phối CDS và chống tải trùng trong phạm vi một tiến trình.
Không bật scheduler, không gọi CDS thật tự động. Bổ sung retry có giới hạn đúng loại lỗi,
xử lý job dở sau restart và bảo toàn dữ liệu đã nhập.
Viết checklist và script kiểm tra bộ dữ liệu demo có sẵn; script mặc định chỉ kiểm tra
local, yêu cầu cờ rõ ràng mới cho phép tải thật. Ghi log kết quả thực tế, không giả success.
```

Đạt khi: hai lần bấm cùng yêu cầu không làm đầy hàng đợi; lỗi CDS hiển thị có thể hiểu; restart vẫn đọc dữ liệu tốt đã nhập. Có ít nhất một lần thu thập thật phạm vi nhỏ được ghi nhận đầy đủ nếu tài khoản/dịch vụ cho phép.

## 9 Đợt 5 Chatbot với phạm vi hẹp

Cập nhật 17/11/2026: đã chọn LLM Agent qua Responses API, mặc định gpt-6-luna/low. Đã có adapter, công cụ DB-only, điều phối có giới hạn và chat nổi góc phải dưới. So sánh Luna/Sol thật trên tám tình huống fixture đều đạt 8/8; xem [thiết kế, kết quả và giới hạn nghiệm thu](DOT_5_AGENT_VA_CHAT.md). Chưa coi các test fixture là nghiệm thu mọi hội thoại trên dữ liệu ERA5 thật.

Theo yêu cầu mở rộng sau đó, ERA đã hỗ trợ thêm hội thoại phổ thông và giải thích sâu dự án từ chỉ mục code/tài liệu có nguồn. Số liệu quan trắc vẫn tách sang công cụ DB-only; lời giải thích model không được gán cờ xác minh DB. Chi tiết kiểm thử mới và giới hạn của chỉ mục nằm trong tài liệu Agent ở trên.

Ưu tiên chatbot có các câu hỏi được hỗ trợ rõ: dữ liệu đã có ở đâu/khi nào, thiếu gì, thống kê trong khoảng và hướng dẫn thao tác. Truy vấn dùng đúng service DB-only, không mở kết nối CDS ngầm hoặc thực thi SQL tự do từ văn bản.

Lựa chọn triển khai:

- Nhanh nhất: chatbot theo intent và mẫu câu, điền tham số qua lựa chọn giao diện; công bố rõ phạm vi, không gọi là LLM Agent.
- Nếu học phần yêu cầu LLM/Agent: dùng mô hình có tool calling và một số công cụ cố định; LLM chỉ xác định ý định/tham số, số liệu do service tính. Việc này cần xác nhận mô hình, chi phí và cấu hình riêng. Không huấn luyện mô hình từ đầu.

Chatbot phải hỏi lại nếu thiếu vị trí/khoảng ngày, từ chối phạm vi ngoài hỗ trợ, xử lý null/warning và nêu nguồn/đơn vị/thời gian. Chưa cho tự tạo tác vụ tải; có thể dẫn người dùng sang form Thu thập. Nếu cần tạo job qua chat, thêm bước người dùng xem và xác nhận tham số cụ thể.

```text
Thực hiện chatbot cho các câu hỏi được hỗ trợ, theo phương án đã chốt với người dùng.
Tái sử dụng service DB-only và inventory/quality của các đợt trước.
Không tự viết số liệu, không chạy SQL/command từ câu hỏi, không tải dữ liệu ngầm.
Thiếu tham số thì hỏi lại; thiếu dữ liệu thì nói rõ; ngoài phạm vi thì hướng dẫn.
Nếu dùng mẫu intent, công bố đó là chatbot theo luật. Nếu dùng LLM, tách adapter
mô hình và chỉ cho gọi danh sách công cụ cố định. Không tự cài provider LLM hoặc
đặt khóa vào repo khi chưa thống nhất.
Test câu hỏi đủ/thiếu tham số, không có dữ liệu, có warning, ngoài phạm vi và
văn bản cố yêu cầu bot bịa số hoặc bỏ qua ràng buộc.
```

Đạt khi: chatbot trả lời được một tập câu hỏi công bố trước bằng đúng dữ liệu DB; không trả số khi dữ liệu không có.

## 10 Đợt 6 Nghiệm thu và cập nhật báo cáo

Chạy bộ kiểm thử nhánh cuối và lưu log mới. Tách kết quả offline khỏi tải CDS thật. Thu thập ảnh UI và file thực nghiệm đúng phiên bản; cập nhật báo cáo, README và hướng dẫn demo theo chức năng thực có. Đánh dấu hướng phát triển cho phần chưa hoàn thành.

Kịch bản demo tối thiểu:

1. Chọn Đất liền và tạo một job tải phạm vi nhỏ.
2. Xem job hoàn tất và dữ liệu trong DB; trình bày nguồn và điểm lưới.
3. Truy vấn dữ liệu đã lưu mà không tạo request CDS mới; xuất CSV.
4. Đổi giờ/ngày/tháng/năm; giải thích kỳ tháng/năm chưa trọn nếu bộ mẫu ngắn.
5. Chạy file lỗi có kiểm soát trong môi trường thử và xem warning đúng hàng/cột.
6. Chọn Biển và đọc bộ dữ liệu đã chuẩn bị; không tráo dữ liệu Land.
7. Hỏi chatbot một câu có dữ liệu và một câu thiếu dữ liệu.
8. Khởi động lại backend, kiểm tra dữ liệu và warning vẫn còn.

File thiếu có chủ ý phải ghi rõ dữ liệu kiểm thử, dùng DB thử, không sửa dữ liệu thật để dựng lỗi. Không yêu cầu năm nào cũng có đủ nếu chưa thực sự tải và kiểm tra.

## 11 Thứ tự khi gần hạn

Hoàn thành đợt 0–3 trước: đây là sản phẩm thu thập dữ liệu mà người dùng trực tiếp sử dụng. Sau đó làm đủ ổn định tải và một bộ minh chứng ở đợt 4. Tiếp đến chatbot phạm vi hẹp, cuối cùng cập nhật tài liệu. Nếu thiếu thời gian, giảm phạm vi câu hỏi chatbot và dữ liệu demo sau khi chốt với giảng viên, không bỏ cảnh báo hay giả dữ liệu.

Chốt với giảng viên: bao nhiêu năm/địa điểm chuẩn bị sẵn; biển cần biến khí quyển hay cả sóng; bất thường nghĩa là lỗi chất lượng hay vượt ngưỡng khí tượng; chatbot theo luật có được chấp nhận hay bắt buộc LLM/Agent.

Một đợt chỉ hoàn thành khi có diff được xem, kiểm thử phù hợp thực chạy và thao tác thử thành công. Không ước lượng tổng thời gian hoặc điểm số trước khi kiểm tra độ khó các phần còn thiếu.
