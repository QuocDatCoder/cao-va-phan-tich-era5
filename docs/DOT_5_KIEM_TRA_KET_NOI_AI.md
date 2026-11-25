# Đợt 5 — Bước 1: kiểm tra Responses API và tool calling

## Cấu hình đã thống nhất

- Gateway: `https://modelapi.vn/v1`. Ngày 19/11/2026 đã xác nhận cùng khóa: `/models` trên hostname này trả 200 và probe Responses/tool calling đạt; hostname có tiền tố `api.` trả 401 Invalid token. Không suy ra hai hostname dùng chung kho khóa.
- `/api/v1/agent/status` hiển thị gateway thực tế và key_source (environment/env_file), không trả khóa. Biến môi trường ưu tiên hơn `.env`: nếu probe ở tiến trình mới đạt nhưng backend vẫn 401, kiểm tra biến AI_API_KEY cũ trong terminal và tiến trình uvicorn con còn giữ cổng. Dừng đúng cây tiến trình backend, không dừng Streamlit hoặc ứng dụng Python khác. Khi dùng `.env` cục bộ, bỏ biến ghi đè cũ trong terminal trước khi khởi động backend lại.
- Model: `gpt-6-luna`.
- Reasoning effort: `low`.
- Mỗi request gửi `max_output_tokens=512`, `store=false`, `stream=false`; không tự retry hoặc tự đổi model/giao thức.
- Dùng `httpx` đã có trong requirements; chưa cài SDK/provider mới. Chưa thêm tab chat, công cụ dữ liệu thật hoặc Agent hoàn chỉnh.

Adapter nằm tại `backend/app/ai/`. Không import CDS/provider khí tượng, DB hoặc dịch vụ thu thập. Cấu hình AI tách khỏi Settings khí tượng; chỉ đọc khi được gọi, không tạo kết nối AI lúc backend khởi động.

## Kiểm tra những gì

1. Gửi request Responses có một function cố định `connection_probe`, ép gọi đúng function, tắt parallel tool calls.
2. Kiểm tra đúng một function call, tên/tool arguments và nonce; không thực thi function khác từ văn bản mô hình.
3. Công cụ cục bộ tạo verification code ngẫu nhiên chưa có trong câu hỏi, gắn nhãn dữ liệu kiểm tra, không phải khí tượng.
4. Gửi lại output của lượt đầu (bao gồm reasoning item nếu có) cùng function_call_output đúng call_id. Không phụ thuộc previous_response_id hoặc việc gateway lưu response.
5. Lượt thứ hai tắt gọi công cụ; chỉ đạt nếu câu trả lời khớp verification code công cụ trả về. Cho phép khoảng trắng sau `PROBE_OK:`; không cho phép mã sai, nội dung thêm hoặc suy đoán số liệu.

Tối đa hai request mô hình trên đường thành công; nếu lượt đầu lỗi hoặc tool không hợp lệ thì dừng sớm. HTTP thành công nhưng response incomplete, công cụ lạ, argument sai hoặc câu trả lời không khớp đều không được coi là đạt. Token usage chỉ ghi khi upstream thực sự trả về; không ước lượng chi phí thành số liệu thật.

## Chạy offline trước

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/check_ai_connection.py --log output/ai-connection/offline.json
```

Mặc định dùng `httpx.MockTransport`, bỏ qua `.env` và cấu hình AI_* trong môi trường. Không kết nối ModelAPI, không gọi CDS, không đọc DB. `mode=offline_fixture` và `live_connection_verified=false`, kể cả khi fixture pass. Kết quả này chỉ xác nhận code giao thức, không chứng minh gateway/model hỗ trợ tool calling.

## Cấu hình cục bộ để chạy thật

Mở `.env` đang có ở gốc dự án, **thêm** các dòng bên dưới; không chép đè file hoặc xóa cấu hình CDS đang dùng:

```dotenv
AI_BASE_URL=https://modelapi.vn/v1
AI_MODEL=gpt-6-luna
AI_REASONING_EFFORT=low
AI_API_KEY=<dien-key-cua-ban-tren-may>
AI_TIMEOUT_SECONDS=30
AI_MAX_OUTPUT_TOKENS=512
```

Không gửi key vào chat, Git hoặc ảnh chụp. Script không lấy key từ `.codex/auth.json`, không dùng OPENAI_API_KEY của tiến trình khác. API key có kiểu SecretStr; báo cáo không chứa headers/request/response thô. Lỗi upstream chỉ ghi HTTP status và mô tả cố định, không in body có thể phản chiếu key. Biến AI_* đã đặt trong terminal ưu tiên hơn `.env`.

## Chạy thật — có thể phát sinh phí AI

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/check_ai_connection.py --live --log output/ai-connection/live.json
```

Lệnh này chỉ gọi `/responses` của gateway AI, không cần backend hoặc Streamlit chạy. Không dùng dữ liệu khí tượng thật và không gọi CDS. Mã thoát: 0 = vòng kiểm tra đạt; 1 = lỗi kết nối/giao thức/verification; 2 = cấu hình/tham số không hợp lệ.

Chỉ công nhận bước kết nối thật đạt khi đồng thời có `mode=live`, `passed=true`, `live_connection_verified=true`, `function_calls_executed=1`, `tool_output_verified=true`. `returned_model_matches_request` cho biết tên model trả về có khớp tên đã yêu cầu hay không; nếu false cần xem việc ánh xạ model ở gateway trước khi chốt model thực phục vụ. Đây không phải bằng chứng độc lập về model nội bộ của nhà cung cấp.

Nếu trả lỗi 401/403: kiểm tra key và quyền nhóm/model. Lỗi 402: kiểm tra hạn mức. Lỗi 400/404: kiểm tra model ID, endpoint Responses và hỗ trợ tools/reasoning/include của gateway. Lỗi 429/timeout: không tự gửi lại liên tục; kiểm tra dashboard trước khi thử lại. `incomplete_response` có thể do hết budget output/reasoning; chưa tự tăng token. Không tắt tool validation để làm probe báo thành công.

## Phạm vi còn lại

Kiểm tra thật đã đạt ngày 17/11/2026, báo cáo `output/ai-connection/live.json`: `mode=live`, `passed=true`, `live_connection_verified=true`, đúng một function call; cả hai phản hồi báo tên model khớp `gpt-6-luna`, reasoning gửi `low`. Lượt xác nhận cuối có hai request, usage upstream trả về 242 và 318 token (tổng 560 token của lượt này, không phải tổng mọi lần thử). Trước đó có một lỗi kết nối và hai lượt chưa đạt do kiểm tra định dạng quá chặt; không có auto retry. Khi chuẩn hóa khoảng trắng, vẫn bắt buộc verification code bí mật do tool tạo phải khớp nguyên vẹn; test chặn mã sai và văn bản thừa vẫn đạt.

Người dùng ban đầu nhập key vào `.env.example`; đã chuyển riêng AI_API_KEY sang `.env` cục bộ và để trống lại template, không thay cấu hình CDS hay đọc auth.json của Codex. Không commit credentials hoặc output. Model gateway báo trùng alias không phải chứng thực độc lập model nội bộ.

Đã hoàn thành kiểm tra kết nối/tool calling thật. Công cụ inventory/quality/statistics DB-only, điều phối và giao diện Agent đã được bổ sung ở bước tiếp theo; xem [Agent và chat](DOT_5_AGENT_VA_CHAT.md) để biết trạng thái hiện tại và kết quả so sánh model.

## Kiểm chứng code sau lượt live

- Test AI adapter/probe: **30 passed**, dùng HTTP fixture, không phát sinh request AI thật.
- Runner offline cuối: **393 passed, 1 skipped** (CDS thật), một warning deprecation TestClient; startup backend/frontend HTTP 200, AppTest đạt, inventory dữ liệu người dùng không đổi. Log `output/ai-step1-final/`.
- Một lượt startup trước đó phát hiện lỗi cũ: hai request đầu khởi tạo JobRepository đồng thời gây duplicate column khi migration. Đã thêm khóa schema trong tiến trình và test 16 lần khởi tạo trên 8 thread. Đây không phải khóa đa worker/máy, vẫn giữ phạm vi demo một backend một worker.
- Không commit/push, không thêm SDK hoặc tạo tab Agent giả. `.env.example` đã được xác nhận không chứa AI key; `.env` và log output đều được Git ignore.

Tham khảo giao thức: [Function calling](https://developers.openai.com/api/docs/guides/function-calling), [Responses create](https://developers.openai.com/api/reference/resources/responses/methods/create). Model ID và base URL lấy từ cấu hình người dùng; chưa có khẳng định từ tài liệu OpenAI về model alias của gateway bên thứ ba.
