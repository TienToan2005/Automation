# Nghiệp vụ xác thực RFID + khuôn mặt: audit, thiết kế và kết quả kiểm thử

Tài liệu này ghi lại **những gì đã kiểm tra bằng code**, **bằng test**, và **những gì vẫn cần thử trên thiết bị thật**.
Không có kết luận nào ở đây là "an toàn tuyệt đối" hay "chống giả mạo hoàn toàn".

## 1. Luồng thật (sau khi sửa)

```
RC522 ─► ESP32 submitCard ─► POST /access/card-scan ─► backend
                                │ giới hạn tần suất (uid, thiết bị)
                                │ tra thẻ ─► trạng thái HIỆU LỰC (thu hồi > vô hiệu > hết hạn > khóa tạm > hoạt động)
                                │ tài khoản, đã đăng ký mặt
                                │ đóng các phiên cũ còn mở của cửa (superseded)
                                │ tạo AuthSession PENDING (hạn = face_timeout + grace)
                                └► AI /trigger ─► camera ─► chất lượng ─► khớp ─► PAD ─► thử thách ─► finish()
                                                                  │
ESP32 poll /access/session/{id}  ◄── backend ◄── POST /access/verify (service token)
   granted ─► relay đóng ─► nhịp tim báo relay ─► backend ghi door_unlocked / door_locked
```

Phiên chỉ đóng **một lần** bằng `UPDATE … WHERE state='pending'`; ai thắng thì ghi nhật ký, cảnh báo, bộ đếm.

## 2. Kết quả audit (trước khi sửa) và tình trạng

| # | Phát hiện | Mức | Tình trạng |
|---|---|---|---|
| A1 | Khóa tạm nằm ở ESP32, **dùng chung cả cửa**, mất khi khởi động lại; thẻ lạ cũng tính → DoS cửa | Cao | Đã sửa: bộ đếm của cửa chỉ tính bằng chứng an ninh; khóa theo thẻ ở backend, trong DB |
| A2 | Lỗi kỹ thuật (`ai_unavailable`, `not_enrolled`…) bị tính là lần sai | Cao | Đã sửa (`NON_SECURITY_REASONS`, firmware `isSecurityFailure`) |
| A3 | Không có trạng thái thẻ ngoài active/revoked, không bộ đếm / cooldown / rate limit | Cao | Đã sửa: `expires_at`, `locked_until`, `lock_level`, `disabled`, rate limit |
| A4 | Không có bảng phiên; `handle_verify` check-then-insert (không nguyên tử) | Cao | Đã sửa: bảng `auth_sessions`, chuyển trạng thái nguyên tử |
| A5 | `/verify` không ràng buộc phiên / người | Cao | Đã sửa: 404 phiên lạ, 409 sai chủ phiên (+ cảnh báo) |
| A6 | Thẻ thu hồi giữa phiên vẫn mở được | Trung bình | Đã sửa: kiểm tra lại thẻ + tài khoản lúc cấp quyền |
| A7 | "Sai chủ thẻ" kết luận từ khung mờ / độ khớp lửng lơ | Cao | Đã sửa: cổng chất lượng + vùng "chưa kết luận" |
| A8 | PAD thấp = `spoof_pad` mức HIGH ngay cả khi điểm lưng chừng (người thật bị 0,64) | Cao | Đã sửa: PAD chắc chắn mới là `spoof_pad`; lưng chừng → `pad_uncertain` |
| A9 | `revoked_card_used` tạo cảnh báo mới mỗi lần quẹt | Thấp | Đã sửa: một cảnh báo cho tới khi admin xử lý |
| A10 | Không ghi nhận cửa thật sự đã mở | Trung bình | Đã sửa một phần: ghi `door_unlocked/door_locked` theo relay (xem rủi ro §9) |
| A11 | Không rate limit `/card-scan` | Trung bình | Đã sửa (uid và thiết bị, 429) |
| A12 | Giao diện admin không có khóa tạm / mở khóa | Trung bình | Đã sửa: trang chi tiết người dùng + 4 API, bắt buộc lý do, có audit |
| (thêm) | `challenge_failed` bị gắn HIGH dù chỉ là liveness chưa qua | Thấp | Đã sửa: MEDIUM |
| (thêm) | PAD chấm cả khung đang quay đầu (mặt nghiêng) → người thật bị từ chối nhầm | Cao | Đã sửa trước đó: PAD chỉ chấm khung nhìn thẳng |

**Giữ nguyên vì đã đúng:** fail-secure khi AI hỏng, không có đường mở cửa dự phòng, `REQUIRE_PAD`, mở cửa từ xa có lý do + audit,
`finish()` của AI chỉ chạy một lần, ảnh nghi phạm chỉ admin xem.

## 3. Vòng đời phiên

`pending` → `granted | denied | timeout | cancelled | error` (trạng thái cuối, không đổi lại).

Kết thúc khi: có kết quả, hết hạn (backend tự đóng khi có người hỏi / có kết quả muộn), thiết bị hủy, có phiên mới thay thế, AI hỏng
(`error`). Sau khi đóng: kết quả đến muộn bị bỏ qua (200, `action: none`), gọi hủy lặp lại vô hại, mở cửa chỉ khi **phiên còn mở + còn hạn +
đúng người + thẻ và tài khoản còn hiệu lực lúc cấp quyền**.

Thử lại **trong cùng phiên**: không thấy mặt, ảnh kém, độ khớp chưa kết luận, PAD chưa chắc → AI tiếp tục quét tới hết phiên (30 giây),
không bắt quẹt lại. Sai chủ thẻ đã xác nhận / PAD chắc chắn giả → đóng phiên ngay.

## 4. Phân loại lỗi và ánh xạ tên nghiệp vụ

| Mã lưu (`reason`) | Tên nghiệp vụ | Đếm vào khóa tạm? | Cảnh báo |
|---|---|---|---|
| `unknown_card` | CARD_UNKNOWN | Không (không có thẻ để khóa) | không |
| `card_disabled` | CARD_INACTIVE | Không | không |
| `card_expired` | CARD_EXPIRED | Không | không |
| `card_locked` | CARD_TEMPORARILY_LOCKED | Không | không |
| `card_revoked` | CARD_REVOKED | Không | HIGH, 1 cảnh báo tới khi xử lý |
| `account_suspended` | ACCESS_DENIED | Không | không |
| `rate_limited` | (giới hạn tần suất) | **Không** | không |
| `ai_unavailable` | SYSTEM_ERROR | **Không** | thông báo hệ thống |
| `face_not_detected` | FACE_NOT_DETECTED | **Không** | không |
| `face_quality_low` | FACE_QUALITY_LOW | **Không** | không |
| `multiple_faces` (gợi ý OLED) | FACE_MULTIPLE_DETECTED | **Không** | không |
| `face_inconclusive` | FACE_MATCH_INCONCLUSIVE | **Không** | không |
| `pad_uncertain` | (PAD chưa đủ tin cậy) | **Không** | không |
| `face_mismatch` | FACE_MISMATCH (đã xác nhận) | **Có**, bộ đếm riêng | MEDIUM |
| `challenge_failed` | LIVENESS_FAILED | **Có**, bộ đếm riêng, ngưỡng cao | MEDIUM |
| `spoof_pad` | SPOOF_DETECTED | **Có**, bộ đếm riêng | HIGH |
| `timeout` | AUTH_TIMEOUT | **Không** | không |
| `cancelled`, `superseded` | AUTH_CANCELLED | **Không** | không |
| `ok` | ACCESS_GRANTED | reset không cần (đếm theo phiên bị từ chối) | không |
| `door_unlocked`, `door_locked` | (sự kiện cửa) | – | không |

## 5. Chính sách thử lại, cooldown, khóa tạm

Giá trị **khởi điểm** (cấu hình, không hard-code), cần hiệu chỉnh bằng dữ liệu thật:

| Tham số (`LAB_…`) | Mặc định | Ý nghĩa và lý do |
|---|---|---|
| `MISMATCH_LOCK_THRESHOLD` / `MISMATCH_WINDOW_S` | 3 / 300 | 3 lần `face_mismatch` đã xác nhận trong 5 phút. Mỗi lần "xác nhận" đã là 20 khung liên tiếp độ khớp thấp rõ rệt, nên 3 lần là mức rất đáng ngờ |
| `SPOOF_LOCK_THRESHOLD` / `SPOOF_WINDOW_S` | 3 / 900 | bộ đếm riêng, cửa sổ dài hơn |
| `LIVENESS_LOCK_THRESHOLD` / `LIVENESS_WINDOW_S` | 5 / 900 | ngưỡng cao hơn vì có thể là lỗi thu nhận |
| `LOCK_BASE_S`, `LOCK_CAP_S` | 300, 1800 | cooldown 5 → 15 → 30 phút (nhân 3, có trần) |
| `LOCK_LEVEL_DECAY_S` | 86400 | quá một ngày yên ổn thì bậc về 1 |
| `SCAN_RATE_PER_UID_PER_MIN` / `…_PER_DEVICE_PER_MIN` | 6 / 30 | chống tự động hóa; **không** đổi bộ đếm lỗi nên không dùng để khóa thẻ người khác |
| `FACE_TIMEOUT_S`, `SESSION_GRACE_S` | 30, 5 | thời lượng phiên |
| Firmware `MAX_TRIES`, `FAIL_WINDOW_MS`, `LOCKOUT_MS` | 3, 120000, 15000 | khóa cửa NGẮN chống thử liên tục; chỉ tính bằng chứng an ninh |

Hành vi:
- Mỗi lần khóa đặt `failures_reset_at = now`: đợt sau đếm lại từ đầu, **lịch sử vẫn giữ**.
- Bộ đếm tính từ bảng `auth_sessions` nên bền vững qua khởi động lại; hết cooldown **không** làm sống lại thẻ thu hồi / vô hiệu / hết hạn.
- Mở khóa của admin: bắt buộc lý do ≥ 5 ký tự, ghi audit, **không xóa lịch sử**, không áp dụng cho thẻ thu hồi / vô hiệu.

### Khóa theo thẻ hay theo người / thiết bị?

| Lựa chọn | Ưu | Nhược |
|---|---|---|
| **Khóa thẻ (đã chọn)** | Đúng đối tượng bị lạm dụng; người khác và thẻ khác của cùng người vẫn dùng được | Một người có nhiều thẻ có thể đổi thẻ để né cooldown (chấp nhận: thẻ kia cũng cần mặt khớp) |
| Khóa danh tính | Chặn cả việc đổi thẻ | Kẻ xấu cố ý sai để khóa nạn nhân trên mọi thẻ; không có căn cứ đủ mạnh |
| Khóa thiết bị / cửa | Đơn giản | Một người lạ khóa cả cửa với mọi người (DoS): chỉ giữ dạng 15 giây và chỉ cho bằng chứng an ninh |
| Giới hạn tần suất | Chặn tự động hóa | Không phải bằng chứng an ninh, nên không dẫn tới khóa |

**Chống khóa có chủ đích:** cần 3 lần sai chủ thẻ *đã xác nhận* (không phải lỗi thu nhận), cooldown tạm thời có trần 30 phút, thẻ lạ không
khóa được ai, quản trị viên mở khóa có kiểm soát. Rủi ro còn lại ở §9.

## 6. Cấu hình AI (biến `AI_…`)

| Biến | Mặc định | Ghi chú |
|---|---|---|
| `AI_SIM_THRESHOLD` | 0,50 | độ khớp ≥ mức này là khớp |
| `AI_MISMATCH_CONFIRM_SIM` | 0,35 | **dưới** mức này mới là "sai chủ thẻ"; giữa 0,35 và 0,50 là *chưa kết luận* |
| `AI_PAD_THRESHOLD` | 0,70 | PAD trung bình tối thiểu để đi tiếp |
| `AI_SPOOF_CONF_THRESHOLD` | 0,40 | PAD trung bình **dưới** mức này mới là giả mạo chắc chắn; giữa 0,40 và 0,70 là *chưa đủ tin cậy* |
| `AI_VERIFY_MIN_DET_SCORE` | 0,50 | độ tin cậy dò mặt tối thiểu để dùng khung kết luận |
| `AI_VERIFY_MIN_SHARPNESS` | 8 | độ nét tối thiểu. Số đo thật trên camera ESP32 qua WiFi: **9–39** (ngưỡng đăng ký 60 sẽ loại gần hết) |
| `AI_BLINK_MIN_FPS` | 6 | dưới tốc độ này không ra thử thách chớp mắt |

**Điểm similarity không có ý nghĩa tuyệt đối:** 0,50 / 0,35 chỉ là khởi điểm cho mô hình `buffalo_l` (cosine). Hiệu chỉnh bằng
`python tools/eval_far_frr.py` (thu clip chính chủ / người khác / ảnh giả rồi `evaluate`) trên **đúng camera và ánh sáng của cửa**,
và đo thêm FTA (tỉ lệ không thu nhận được). Không dùng nhiều lần thử để cộng dồn xác suất rồi cấp quyền.

## 7. API quản trị thẻ (admin, bắt buộc lý do ≥ 5 ký tự, ghi audit)

| Endpoint | Tác dụng |
|---|---|
| `POST /api/v1/cards/{id}/unlock` | Mở khóa tạm; đặt mốc đếm lại; 409 nếu thẻ thu hồi / vô hiệu |
| `POST /api/v1/cards/{id}/disable` / `enable` | Vô hiệu / bật lại; 409 nếu đã thu hồi |
| `POST /api/v1/cards/{id}/expiry` | `{expires_at, reason}`; `expires_at = null` bỏ hạn |
| `POST /api/v1/cards/{id}/revoke` | Thu hồi vĩnh viễn (đã có) |

`GET /api/v1/users` trả thêm cho mỗi thẻ: `effective_status`, `expires_at`, `locked_until`, `lock_reason`, `lock_level`.
Trang **Người dùng → Chi tiết** hiển thị và gọi các API này (không có nút giả).

## 8. Migration

Không cần Alembic: khởi động chạy `create_all` (tạo bảng `auth_sessions`) rồi `add_missing_columns` (thêm 6 cột vào `cards`, nullable hoặc
mặc định hằng). Đã chạy trên DB thật: giữ nguyên 2 thẻ, 3 người dùng, 140 dòng nhật ký. Rollback: bỏ qua các cột mới (code cũ không đọc chúng).

## 9. Rủi ro còn tồn tại

1. **Khẩu trang / che mặt:** độ khớp có thể tụt dưới 0,35 và bị tính là `face_mismatch` (3 lần → khóa thẻ 5 phút). Cần đo trên dữ liệu thật; hạ `AI_MISMATCH_CONFIRM_SIM` nếu cần.
2. **Chưa hiệu chỉnh FAR / FRR / FTA** bằng dữ liệu đại diện. Mọi ngưỡng là khởi điểm.
3. **PAD chưa kiểm thử đủ tấn công:** chỉ thấy ảnh trên điện thoại bị chấm 0,358. Chưa thử video replay chất lượng cao, mặt nạ.
4. **Lạm dụng khóa:** kẻ có thẻ nạn nhân cố tình sai 3 lần xác nhận có thể khóa thẻ đó tối đa 30 phút (mở khóa bằng admin).
5. **Rate limit và `RateLimiter` trong bộ nhớ:** đủ cho một tiến trình; nhiều worker cần Redis. Trạng thái khóa thẻ thì nằm trong DB (không mất).
6. **Chưa có cảm biến trạng thái cửa:** chỉ biết relay đóng / nhả, không biết cửa vật lý đã mở, mở quá lâu hay bị cạy.
7. **Thoát hiểm:** khóa chốt điện từ qua relay NO: mất điện thì khóa đóng. Cần có nút trong / khóa cơ theo quy định an toàn của cơ sở; phần mềm không được cản việc đó.
8. **Token dev mặc định** (`dev-service-token`, `dev-device-token`) phải đổi khi triển khai thật.
9. Đường đi `ESP32 → backend` dùng HTTP không mã hóa trong mạng nội bộ.

## 10. Hướng dẫn kiểm thử thủ công (đầy đủ quy trình)

1. Bật backend (`docker compose up -d`), AI (`.\start_ai.ps1 -Camera "http://<IP board>:81/stream"`), nạp firmware, cấp nguồn board.
2. **Người hợp lệ:** quẹt thẻ đã đăng ký, nhìn camera, quay đầu theo yêu cầu → OLED "MO CUA", relay tách, khóa thụt ~5 giây. Web → Nhật ký có `card_scan` + `face_verify (granted)` + `door_event (door_unlocked, door_locked)` cùng `session_id`.
3. **Thẻ lạ:** quẹt thẻ chưa đăng ký ≥ 5 lần liên tiếp → mỗi lần OLED "THE KHONG HOP LE", **cửa không bị khóa**, thẻ thật vẫn dùng được ngay.
4. **Không thấy mặt:** quẹt thẻ rồi che camera / đứng ngoài khung đến hết 30 giây → OLED "HET GIO — Khong thay khuon mat", nhật ký `face_not_detected`, **không** có cảnh báo, thẻ **không** bị khóa dù lặp 10 lần.
5. **Dùng thẻ người khác:** thẻ của A, mặt B, giữ trước camera tới khi từ chối → `face_mismatch` (MEDIUM). Lặp 3 lần trong 5 phút → thẻ bị khóa 5 phút ("THE TAM KHOA"), cảnh báo `card_locked`. Trang chi tiết người dùng hiện "Tạm khóa còn ~N phút".
6. **Admin mở khóa:** bấm "Mở khóa", nhập lý do → thẻ dùng lại được; nhật ký có dòng audit "Mở khóa thẻ"; các phiên bị từ chối cũ vẫn còn.
7. **Ảnh giả:** giơ ảnh trên điện thoại → `spoof_pad` (HIGH) nếu PAD < 0,40; nếu lưng chừng → `pad_uncertain` (không có cảnh báo, không mở cửa).
8. **Thu hồi / vô hiệu / hết hạn:** thao tác trên web, quẹt thẻ → OLED hiện đúng lý do; chờ hết cooldown thì thẻ thu hồi vẫn bị từ chối.
9. **Quẹt liên tục:** quẹt cùng thẻ > 6 lần / phút → "THAO TAC QUA NHANH".
10. **AI hỏng:** tắt AI, quẹt thẻ hợp lệ → "HE THONG BAN", thẻ không bị tính sai, không mở cửa.
