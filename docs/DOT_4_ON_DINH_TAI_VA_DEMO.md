# Đợt 4 — Ổn định thu thập và kiểm tra dữ liệu demo

## Trạng thái bản chuẩn bị push

Phần điều phối/retry/restart và kiểm tra local đã triển khai; dữ liệu thật mới kiểm chứng được ERA5-Land. Đây là mốc code có thể review/push, **không phải tuyên bố đã nghiệm thu ERA5/biển hoặc hoàn thành toàn bộ dữ liệu demo**. Chưa tự gửi CDS thật mới, không bật scheduler và chưa commit/push thay người dùng.

Audit ngày 17/11/2026: bốn bộ Land đạt độ phủ và hash, log `output/phase4-push/land-audit.json`, CSV ở `output/phase4-push/land-csv/`. Audit yêu cầu đồng thời Land và ERA5 trả exit 1 với lý do chưa có bộ ERA5 đạt kiểm tra (`output/phase4-push/local-audit.json`). Chỉ có các snapshot Land trong DB tại lần kiểm tra này; các thông tin bộ ERA5 failed ở phần mốc ban đầu bên dưới là lịch sử kiểm tra.

Vòng kiểm chứng bản chuẩn bị push:

- `scripts/baseline_offline.py --output-dir output/p4ready`: **333 passed, 1 skipped**, HTTP health/docs/frontend đều 200, AppTest đạt, inventory DB/file nguồn/metadata `.env` trước và sau không đổi. File phối hợp tạm của SQLite (`-wal`, `-shm`) được loại khỏi so sánh inventory, không coi sự tạo/xóa các file này là thay đổi dữ liệu bền vững.
- `scripts/browser_review.py`: **passed**, log `output/browser_review/20261117-025022/`. Kiểm tra trên backend/DB thử riêng: CSV 48 giờ, biểu đồ giờ/ngày/tháng/năm, F5, canh nút Làm mới trên màn hình dọc, xác nhận xóa. Mock chỉ được tạo qua API thử, không đưa lại lựa chọn Demo vào giao diện.
- Cả hai runner chặn đọc credential files và chặn CDS/mạng ngoài. Không dùng kết quả mock để tuyên bố ERA5 thật thành công. Một warning deprecation từ FastAPI/TestClient còn tồn tại, không làm kiểm thử thất bại.
- `.gitignore` bỏ qua `output/`, `tmp/` và cache pytest; không đưa DB, file tải hay credentials vào commit.
- Đã mở frontend đang chạy để chụp bảng/biểu đồ của bộ Land thật, chỉ điều hướng đọc dữ liệu đã lưu, không bấm tải/xóa. Ảnh ở `output/phase4-push/real-land-ui/stored.png` và `chart.png`; log điều hướng ở `result.json`. Ảnh mock của script Chromium được giữ riêng, không dùng thay ảnh Land thật.

Các bộ Land được audit ở mốc này:

| Bộ | Độ phủ biến chính |
|---|---:|
| `638562f6-16c5-458c-866d-3eddb1e3a147` | 24/24 |
| `553ed084-43e6-470d-93fc-f9f0fb91f956` | 24/24 |
| `26047a4a-dc48-4759-a8c9-a32a2ad81b9a` | 24/24 |
| `9eb3337f-6d92-4f90-b412-7bea8e828fd3` | 96/96 |

Chạy lại audit khi các bộ trên đã bị người dùng xóa hoặc có thêm lần thu thập mới. Số liệu này là ảnh chụp trạng thái, không phải dữ liệu kèm repository.

### Push mốc code hiện tại

Mốc này bao gồm code đợt 4 và các điều chỉnh giao diện/xóa/loading được yêu cầu sau commit đợt 3. Tên commit đề xuất: `feat: harden collection jobs and refine dashboard workflow`. Nêu rõ trong PR: Land có minh chứng local, ERA5 thật còn chờ kiểm chứng; chưa làm chatbot.

```powershell
git add -- .gitignore README.md backend/app frontend scripts tests docs/DOT_3_GIAO_DIEN_QUY_TRINH.md docs/DOT_4_ON_DINH_TAI_VA_DEMO.md
git diff --cached --stat
git diff --cached --check
git commit -m "feat: harden collection jobs and refine dashboard workflow"
git push origin NDuong
```

Đọc danh sách staged trước khi commit. Không dùng force push. Các log/ảnh/CSV trong output chỉ để kiểm chứng local hoặc đính kèm PR thủ công khi cần, không nằm trong commit này.

## Phạm vi

### Bổ sung hủy tác vụ (17/11/2026)

`POST /api/v1/jobs/{job_id}/cancel`: queued → cancelled ngay; running → cancelling → cancelled khi worker đã dừng. Gọi lại không tạo thêm công việc; job đã completed/failed giữ nguyên. Job cancelling vẫn khóa thao tác xóa và tiếp tục được theo dõi. Thao tác hủy không cần đợi khóa hàng đợi tải; worker nhường khóa sau khi dừng an toàn.

Giao diện có nút **Hủy tác vụ** trong chi tiết tác vụ đang chờ/chạy, nút bị khóa trong **Đang hủy**, và thẻ **Đã hủy** khi worker dừng. Người dùng có thể nhập yêu cầu khác; yêu cầu mới xếp hàng cho đến khi thao tác hiện tại giải phóng khóa.

Hủy có tính hợp tác: kiểm tra khi poll kết quả CDS, retry, streaming HTTP, parse/import và trước công bố file hoàn tất. Với CDS SDK hiện cài dùng API submit/results công khai và chờ ngắt được; không dùng kill thread, không đổi scheduler. Một thao tác mạng đang thực thi có thể cần trả về hoặc timeout trước khi dừng. Không bảo đảm remote job tại CDS được xóa; không báo phần trăm hoặc khẳng định hủy CDS thành công. File partial, file gốc và quan sát đã nhập được giữ; transaction đang nhập có thể rollback phần dở. Job đã hủy không bị ghi đè thành completed/failed bởi worker đến muộn. Khi xác minh worker cũ đã chết, phục hồi cancelling thành cancelled.

Kiểm chứng offline: API/UI hủy queued, idempotency, 404, giữ completed, hủy running và giữ snapshot 24 quan sát, job tiếp theo chạy được, chặn finalizer ghi đè, adapter CDS dừng poll không download, phục hồi hủy sau restart. Chromium API fixture kiểm tra nút hủy → trạng thái đang hủy/nút disabled → đã hủy/nút biến mất. Không gọi CDS thật; ảnh `output/job-loading-browser/cancelling.png`, `cancelled.png`. Kết quả bộ offline cuối ở `output/cancel-jobs-final/`.

Một backend, một tiến trình/worker. Không bật scheduler và không tự gửi CDS thật khi khởi động, phục hồi hoặc kiểm tra local. Giữ nguyên dữ liệu/file hiện có. Chưa làm chatbot.

Kiểm thử sau bổ sung hủy: **342 passed, 1 skipped** (CDS thật), một deprecation warning TestClient; backend/frontend startup và AppTest đạt; inventory dữ liệu người dùng không đổi. Log: `output/cancel-jobs-final/pytest.log`, `baseline.json`, `test_safety.json`.

## Điều phối và chống trùng

- Job có cùng fingerprint đang queued/running được gộp bằng transaction SQLite `BEGIN IMMEDIATE`. Snapshot không ghi đè bộ cũ; tác vụ kết thúc được phép tạo lần thu thập mới.
- Một khóa dùng chung giữa các instance `DownloadJobService` trong tiến trình bao quanh toàn bộ lần chạy job. Job chờ khóa vẫn là queued; chỉ claim running khi đến lượt. Claim có điều kiện ngăn thực thi hai lần cùng ID.
- Khóa CDS dùng chung cho Land/ERA5 và đường tương thích cũ vẫn được giữ. Khởi tạo CDS client và truyền file nằm trong khóa.
- Không cam kết FIFO, không phải hàng đợi phân tán; không dùng để điều phối nhiều worker/máy. Chạy backend một worker.

## Retry và file dở

- Mặc định tối đa **3 lần thử**, đợi **2 rồi 4 giây**. Chỉ retry timeout, lỗi kết nối và HTTP 408/429/500/502/503/504.
- Không retry HTTP 400/401/403/404/422, lỗi chứng chỉ TLS, validation, parser hoặc lỗi DB. Không retry cả bước import, tránh nhập lặp quan sát và warning.
- Session HTTP giữ phân loại lỗi tạm thời qua CDS SDK, tránh SDK đổi timeout thành lỗi chung không thể phân loại.
- Retry mặc định rất lớn của CDS SDK được giảm về một lần mỗi thao tác SDK; lớp ứng dụng quản lý số lần gọi retrieve. Việc CDS xử lý một job đã chấp nhận có thể kéo dài, không phải timeout tổng cho toàn bộ tác vụ.
- Cấu hình: `CDS_MAX_ATTEMPTS` (1–5, mặc định 3), `CDS_RETRY_BASE_SECONDS` (0–30, mặc định 2), `CDS_REQUEST_TIMEOUT_SECONDS` (1–300, mặc định 60). Không sửa `.env` trong lượt triển khai này.
- File tải đi qua tên riêng `*.part`. Chỉ chuyển sang đường dẫn chính sau khi retrieve thành công và file khác rỗng. File partial thất bại được giữ để kiểm tra; không dùng làm file hoàn tất. File hoàn tất vẫn phải qua parser/chất lượng.
- Log retry ghi dataset, lần thử, loại lỗi, HTTP status và thời gian đợi; không ghi token, URL lỗi hoặc nội dung exception.
- Một timeout lúc submit có thể xảy ra sau khi CDS đã nhận yêu cầu. Không thể bảo đảm chống trùng phía CDS bằng khóa local; số lần thử được giới hạn, không tuyên bố exactly-once từ xa.

## Restart

- Lưu PID và dấu thời điểm tạo tiến trình ngay khi tạo job/snapshot, bao gồm queued. Migration chỉ thêm cột.
- Khi khởi động, nếu xác nhận chủ tiến trình đã chết hoặc PID đã được dùng lại, đánh failed cho job và interrupted cho snapshot. Bao gồm trường hợp job đã tạo nhưng chưa kịp tạo snapshot.
- Tính lại summary từ quan sát còn trong DB, giữ file, giá trị hợp lệ và warning; không xóa dữ liệu, không tạo request CDS mới, không nâng mốc đồng bộ.
- Worker còn sống hoặc hệ điều hành không cho xác minh thì không đoán là đã chết. Job cũ chưa có dấu nhận dạng không được tự kết luận interrupted. Không tự tiếp tục công việc sau restart; người dùng xem phần còn lại rồi chủ động tạo job mới.

## Script kiểm tra local

Không đọc `.env`/`.cdsapirc`, không tạo provider/CDS client. Mở SQLite read-only, kiểm tra integrity, nguồn/dataset, số dòng từng biến, độ phủ đúng biến chính và giờ yêu cầu, warning và SHA-256/kích thước file nguồn. Biến gió dẫn xuất kiểm tra giao giờ u/v. Nguồn mock không được tính là minh chứng thật.

```powershell
# Toàn bộ bộ dữ liệu đang có; yêu cầu đủ cả hai nguồn để chuẩn bị demo
.\.venv\Scripts\python.exe -X utf8 scripts/check_demo_data.py --require-source land --require-source era5

# Chọn bộ cụ thể và xuất CSV kiểm chứng, không tải mới
.\.venv\Scripts\python.exe -X utf8 scripts/check_demo_data.py --collection <collection-id> --export-dir output/phase4/csv
```

Mặc định log JSON ở `output/phase4/<timestamp>.json`. Có thể đặt `--log <path>`. Mã thoát: **0** = local audit đạt; **1** = thiếu/lỗi/chưa đủ demo; **2** = lỗi tham số hoặc live job mới chỉ được tiếp nhận. Bộ partial/failed vẫn được ghi đầy đủ trong log nhưng không coi là demo sẵn sàng. Warning không tự làm bộ đầy đủ thành failed; phải xem nội dung cảnh báo.

### Chỉ khi người vận hành chủ động cho phép tải thật

Tạo file JSON theo `DownloadRequest`, nguồn rõ `land` hoặc `era5`, khoảng ngắn và tọa độ/vùng nhỏ đã kiểm tra. Không đặt token trong file. Ví dụ cấu trúc Land:

```json
{
  "source": "land",
  "variable": "temperature",
  "location": {"latitude": 21.0245, "longitude": 105.8412},
  "start_date": "2025-01-01",
  "end_date": "2025-01-01",
  "frequency": "hourly"
}
```

```powershell
# Đây là thao tác có thể tạo request CDS thật; KHÔNG chạy trong kiểm thử offline.
.\.venv\Scripts\python.exe -X utf8 scripts/check_demo_data.py --allow-live --live-request request.json --api-url http://localhost:8000
```

Cần cả hai cờ `--allow-live` và `--live-request`; thiếu một trong hai dừng trước khi gửi. Chỉ gửi tới backend local, không tự bật scheduler. Script ghi ID/trạng thái tiếp nhận và thoát 2, **không coi accepted là tải thành công**. Theo dõi job trên UI rồi chạy lại audit local theo collection ID khi kết thúc. Nếu submit timeout thì kiểm tra lịch sử tác vụ trước khi gửi lại.

## Checklist nghiệm thu

- [ ] Backend chạy một worker, `ERA5_SYNC_ENABLED=false`.
- [ ] Gửi cùng yêu cầu hai lần khi còn chờ/chạy: cùng job ID; không chạy provider hai lần.
- [ ] Hai job khác nhau chạy tuần tự; job thứ hai queued khi job đầu đang chạy.
- [ ] Lỗi tạm thời chỉ retry trong giới hạn; xác thực/giấy phép/404/validation không retry.
- [ ] Sau khi dừng backend giữa chừng và khởi động lại: job interrupted/failed nếu xác minh được worker chết; phần đã nhập và warning vẫn đọc được.
- [ ] Audit chọn ít nhất một bộ Land và một bộ ERA5 có nguồn gốc thật, file/hash hợp lệ, đúng biến/điểm/khoảng ngày và độ phủ.
- [ ] Chuẩn bị CSV và ảnh UI đúng bộ dữ liệu; ghi rõ dữ liệu thử/lỗi có kiểm soát ở DB thử riêng.
- [ ] Thiếu dữ liệu thì công bố thiếu, không tráo mock và không ghi thành công giả.

## Mốc kiểm tra ban đầu ngày 17/11/2026

- Bộ offline chặn credential files/CDS/outbound: **321 passed, 1 skipped** (live CDS), log `output/phase4-final-verified/pytest.xml` và `test_safety.json`. Một warning deprecation từ thư viện FastAPI/TestClient.
- Các test mới: retry theo status và timeout, giới hạn số lần/đợi, giữ partial, chỉ công bố file thành công, hai service instance dùng chung khóa, claim chống chạy lặp, queued/orphan sau restart, trường hợp không xác minh được PID, audit hash/số liệu/nguồn và khóa chế độ live.
- Audit local: `output/phase4/local-demo-audit.json`, exit **1**, `ready=false`.
- Ba snapshot Land đủ dữ liệu, hash đúng: `26047a4a-dc48-4759-a8c9-a32a2ad81b9a` (24/24), `68378f42-7a4f-407f-bb1e-9cca95f0981c` (24/24), `9eb3337f-6d92-4f90-b412-7bea8e828fd3` (96/96).
- Snapshot ERA5 `d1297e01-1e98-41e1-b531-738c6cc5f313` failed, 0/24, chưa có file nguồn. **Chưa đủ minh chứng ERA5/biển cho demo.** Không tự tải bù.
- Một lần chạy test trực tiếp ban đầu phát hiện test tương thích cũ phụ thuộc cấu hình provider thật và thử kết nối CDS; sandbox chặn kết nối. Test đã được sửa để dùng mock cục bộ. Không có tải CDS thật thành công được ghi nhận trong đợt này. Các lượt xác nhận sau dùng runner offline chặn CDS.
- Chưa thực hiện tải CDS thật mới hoặc dừng cưỡng bức backend người dùng để thử crash; restart được kiểm tra với DB/file fixture, không sửa dữ liệu thật để dựng lỗi.
