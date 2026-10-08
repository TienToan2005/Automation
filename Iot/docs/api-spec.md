# Đặc tả giao tiếp REST/MQTT và phân công nhiệm vụ

Hệ thống kiểm soát ra vào phòng lab và tích hợp phát hiện truy cập trái phép.

**Quy ước chung**
- Dữ liệu JSON, UTF-8. Thời gian theo ISO-8601 UTC, ví dụ `2026-09-29T08:15:30Z`.
- Response của Backend theo khung `{status, code, action, data}`.
- Giữa các service dùng header `X-Service-Token: <secret>`. ESP32 dùng device token riêng.
- Nhãn trạng thái: **[ĐÃ CODE]** là phần AI Service đã chạy và kiểm thử; **[ĐỀ XUẤT]** là phần các thành viên khác xây, cần chốt lại.

**Kiến trúc tóm tắt**

```
ESP32 --(1) UID--> Backend --(2) trigger--> AI Service --(3) kết quả + ảnh--> Backend
                      |                                       |
                      |<-----------(3) verify (open/deny)-----+
                      |--(5) MQTT lab/door/control--> ESP32       AI --(4) cảnh báo--> ThingsBoard
                                                      ESP32 --(6) heartbeat--> ThingsBoard
```

Backend ra quyết định mở cửa cuối cùng. AI Service chỉ xác minh "đúng người và là người thật".

---

## 1. Luồng Quẹt thẻ: ESP32 -> Backend [ĐỀ XUẤT]

`POST /api/v1/access/card-scan`

```json
{
  "device_id": "door-esp32-01",
  "uid": "04A1B2C3",
  "timestamp": "2026-09-29T08:15:28Z"
}
```

Thẻ hợp lệ, chờ quét mặt (Backend đồng thời gọi AI ở bước 2):

```json
{
  "status": "pending",
  "code": 202,
  "action": "scan_face",
  "data": {
    "session_id": "7f3a9c1e2b44",
    "student_name": "Hoàng Tiến Toàn",
    "face_timeout_s": 16,
    "message": "Thẻ hợp lệ. Vui lòng nhìn vào camera."
  }
}
```

Thẻ bị từ chối (thẻ lạ, thu hồi, khóa, hết hạn, ngoài khung giờ, AI không phản hồi):

```json
{
  "status": "forbidden",
  "code": 403,
  "action": "none",
  "data": {
    "reason": "card_revoked",
    "message": "Thẻ đã bị thu hồi."
  }
}
```

Giá trị `reason` đề xuất: `unknown_card`, `card_revoked`, `account_suspended`, `credential_expired`, `outside_schedule`, `ai_unavailable`.

---

## 2. Luồng Kích hoạt AI: Backend -> AI Service [ĐÃ CODE]

`POST http://<AI_HOST>:5050/api/ai/trigger`

```json
{
  "student_id": "B23DCCN123",
  "session_id": "7f3a9c1e2b44",
  "embedding": [0.0123, -0.0456, "... đủ 512 số thực ..."]
}
```

- `session_id` và `embedding` là tùy chọn. Nếu thiếu `embedding`, AI dùng file `embeddings/<student_id>.npy` cục bộ.
- Backend nên gửi `embedding` lấy từ DB trong mỗi lần gọi. Khi đó thu hồi mặt có hiệu lực ngay, vì Backend không gửi vector nữa.

Thành công:

```json
{ "status": "success", "session_id": "7f3a9c1e2b44", "message": "AI started scanning" }
```

Chưa đăng ký khuôn mặt (từ chối ngay):

```json
{ "status": "denied", "reason": "not_enrolled", "session_id": "7f3a9c1e2b44", "message": "Student has no face enrollment" }
```

`GET http://<AI_HOST>:5050/api/ai/status`: lấy trạng thái phiên, poll mỗi 500 ms. ESP32 dùng để hiện hướng dẫn lên OLED.

```json
{
  "session_id": "7f3a9c1e2b44",
  "student_id": "B23DCCN123",
  "result": "scanning",
  "reason": "",
  "phase": "challenge",
  "hint": "",
  "challenge": { "type": "turn_right", "text": "QUAY DAU SANG PHAI", "text_vi": "Quay đầu sang phải", "time_left": 6 },
  "similarity": 0.83,
  "pad_score": 0.957,
  "time_left": 11
}
```

- `hint`: `""`, `no_face`, `move_closer`, `multiple_faces`.
- `challenge.type`: `turn_left`, `turn_right`, `blink` (chọn ngẫu nhiên mỗi phiên).

`POST http://<AI_HOST>:5050/api/ai/cancel`: hủy phiên. Trả `{"status": "success"}`.

---

## 3. Luồng Xác thực: AI Service -> Backend API [ĐÃ CODE (AI gửi), Backend do thành viên 3 xây]

`POST /api/v1/access/verify`

AI gửi khi **mỗi phiên kết thúc**, cả thành công lẫn từ chối. Backend dựa vào `result` để ghi log và quyết định mở cửa.

```json
{
  "camera_id": "cam_cua_chinh_01",
  "student_id": "B23DCCN123",
  "confidence_score": 0.89,
  "timestamp": "2026-09-29T08:15:30Z",
  "session_id": "7f3a9c1e2b44",
  "result": "granted",
  "reason": "ok",
  "liveness": { "passed": true, "pad_score": 0.957, "challenge_type": "turn_right" },
  "duration_ms": 4200,
  "snapshot_jpeg_base64": null
}
```

Ví dụ khi bị từ chối vì nghi giả mạo (có kèm ảnh nghi phạm):

```json
{
  "camera_id": "cam_cua_chinh_01",
  "student_id": "B23DCCN123",
  "confidence_score": 0.983,
  "timestamp": "2026-09-29T08:20:11Z",
  "session_id": "a81c0d77e912",
  "result": "denied",
  "reason": "spoof_pad",
  "liveness": { "passed": false, "pad_score": 0.051, "challenge_type": null },
  "duration_ms": 2350,
  "snapshot_jpeg_base64": "<ảnh JPEG rộng 480px, base64>"
}
```

Ý nghĩa trường:

| Trường | Ý nghĩa |
|---|---|
| `confidence_score` | Độ tương đồng cosine cao nhất trong phiên (0 đến 1) |
| `result` | `granted`, `denied`, `timeout`, `cancelled` |
| `reason` | `ok`, `face_mismatch`, `spoof_pad`, `challenge_failed`, `not_enrolled`, `pad_unavailable`, `timeout`, `cancelled`, `superseded` |
| `liveness.pad_score` | Điểm "người thật" của model chống giả mạo (càng cao càng thật) |
| `snapshot_jpeg_base64` | Chỉ có khi `result` là `denied` hoặc `timeout`; ngược lại là `null` |

Response thành công (Backend trả cho AI):

```json
{
  "status": "success",
  "code": 200,
  "action": "open_door",
  "data": {
    "student_name": "Hoàng Tiến Toàn",
    "role": "student",
    "message": "Xác thực thành công. Đang mở cửa..."
  }
}
```

Response từ chối:

```json
{
  "status": "forbidden",
  "code": 403,
  "action": "none",
  "data": {
    "student_name": "Unknown",
    "message": "Tài khoản không tồn tại hoặc đã bị khóa quyền truy cập."
  }
}
```

Backend cần:
- Trả 2xx nhanh, lưu ảnh bất đồng bộ và xử lý theo `session_id` để tránh ghi trùng.
- Nếu `reason` là `spoof_pad` hoặc `challenge_failed` thì tạo cảnh báo an ninh.
- Nếu `result` là `granted`, kiểm tra lại trạng thái tài khoản rồi gửi lệnh MQTT ở luồng 5.

---

## 4. Luồng Cảnh báo An ninh: AI Service -> ThingsBoard [ĐÃ CODE]

`POST http://<THINGSBOARD_HOST>:8080/api/v1/<DEVICE_TOKEN>/telemetry`

Khi `reason` là `spoof_pad`, `challenge_failed` hoặc `face_mismatch`:

```json
{
  "event_type": "security_alert",
  "alert_level": "high",
  "message": "Phát hiện giả mạo khuôn mặt (ảnh in / ảnh hoặc video trên màn hình)",
  "reason": "spoof_pad",
  "student_id": "B23DCCN123",
  "camera_id": "cam_cua_chinh_01",
  "timestamp": "2026-09-29T22:10:05Z",
  "confidence_score": 0.983,
  "pad_score": 0.051,
  "duration_ms": 2350
}
```

Mức cảnh báo: `spoof_pad` và `challenge_failed` là `high`; `face_mismatch` là `medium`.

Các kết quả khác (`granted`, `timeout`, `not_enrolled`...) gửi với `"event_type": "access_event"` kèm `result`, `reason`.

**Chưa có:** cảnh báo "người lạ lảng vảng trước phòng Lab" (trường `stranger_frame_count` trong mẫu). AI hiện chỉ chạy khi có phiên quẹt thẻ nên không theo dõi lúc rảnh. Nếu cần, phải thêm chế độ giám sát liên tục; việc này tốn GPU/CPU và cần bàn lại.

---

## 5. Luồng Điều khiển Phần cứng: Backend -> ESP32 [ĐỀ XUẤT]

Giao thức MQTT, topic `lab/door/control`.

Mở khóa:

```json
{
  "command": "open_lock",
  "duration_ms": 5000,
  "trigger_buzzer": true,
  "trigger_led": "green"
}
```

Từ chối hoặc báo động (đề xuất thêm, thành viên 1 và 3 chốt):

```json
{
  "command": "deny",
  "duration_ms": 3000,
  "trigger_buzzer": true,
  "trigger_led": "red",
  "display_text": "TU CHOI"
}
```

---

## 6. Luồng Báo cáo Trạng thái: ESP32 -> ThingsBoard (Heartbeat) [ĐỀ XUẤT]

MQTT hoặc `POST http://<THINGSBOARD_HOST>:8080/api/v1/<ESP32_TOKEN>/telemetry`

```json
{
  "device_status": "online",
  "door_sensor": "closed",
  "relay_state": 0,
  "wifi_signal_dbm": -65,
  "uptime_seconds": 3600
}
```

Nút Exit bên trong nên gửi thêm một bản tin, ví dụ `{"event_type": "exit_button", "timestamp": "..."}`, để có nhật ký.

---

## 7. Luồng Đăng ký người dùng (Dashboard)

**Backend -> AI: ảnh sang vector** [ĐÃ CODE]

`POST http://<AI_HOST>:5050/api/ai/embed`

```json
{ "image_base64": "<JPEG hoặc PNG base64, tối đa 8MB>" }
```

Thành công:

```json
{
  "ok": true,
  "reason": "ok",
  "embedding": [0.0123, "... 512 số thực ..."],
  "face_width": 153.2,
  "det_score": 0.753,
  "pad_score": 0.938
}
```

Thất bại:

```json
{ "ok": false, "reason": "low_quality", "face_width": 70.1, "det_score": 0.55, "pad_score": 0.9 }
```

`reason` thất bại: `no_face`, `multiple_faces`, `low_quality` (mặt nhỏ hoặc mờ), `spoof_suspected` (ảnh chụp từ màn hình hoặc ảnh in, chặn đăng ký bằng ảnh giả).

**Dashboard -> Backend (CRUD)** [ĐỀ XUẤT, thành viên 3 và 4 chốt]

| Method | Endpoint | Mục đích |
|---|---|---|
| GET | `/api/v1/users` | Danh sách sinh viên |
| POST | `/api/v1/users` | Tạo sinh viên, gắn UID, nhận ảnh để gọi `/api/ai/embed` |
| PUT | `/api/v1/users/{id}` | Sửa thông tin, hiệu lực, vai trò |
| POST | `/api/v1/users/{id}/suspend` | Tạm khóa (kèm lý do) |
| POST | `/api/v1/users/{id}/revoke-card` | Báo mất, thu hồi thẻ |
| POST | `/api/v1/users/{id}/reissue-card` | Cấp lại thẻ mới |
| PUT | `/api/v1/users/{id}/face` | Cập nhật khuôn mặt |
| DELETE | `/api/v1/users/{id}` | Offboarding, xóa dữ liệu sinh trắc |
| GET | `/api/v1/access-logs?from=&to=&result=&reason=` | Nhật ký ra vào (có ảnh nghi phạm) |
| GET | `/api/v1/alerts` | Cảnh báo an ninh đang mở |
| POST | `/api/v1/door/emergency-open` | Mở cửa khẩn cấp (yêu cầu quản trị và ghi lý do) |

---

## 8. API phụ của AI Service [ĐÃ CODE]

`GET /api/ai/health`:

```json
{
  "status": "ok",
  "camera_ok": true,
  "camera_id": "cam_cua_chinh_01",
  "pad_enabled": true,
  "pad_models": ["2.7_80x80", "4.0_80x80"],
  "require_pad": true,
  "gpu": false,
  "local_enrolled_count": 2,
  "session_active": false,
  "thresholds": { "similarity": 0.5, "pad": 0.7, "min_face_width": 120 }
}
```

Backend nên gọi định kỳ và báo động khi `camera_ok` là `false` hoặc `pad_enabled` là `false`.

`POST /api/ai/reload`: nạp lại embeddings cục bộ, trả `{"status": "success", "enrolled": ["B23DCAT286"]}`.

---

## 9. Phân công nhiệm vụ từng thành viên

### Thành viên 1: Phần cứng và vi điều khiển
- Đấu nối, hàn, lắp ráp: ESP32-S3, RFID RC522, relay, OLED, nút Exit, mạch hạ áp LM2596; đóng khung mô hình cửa.
- Firmware: đọc UID qua SPI; phát stream camera OV5640 về máy chủ; hiển thị OLED (I2C); bắt nút Exit; điều khiển relay.
- Giao tiếp: gửi UID lên Backend (luồng 1), nhận lệnh MQTT (luồng 5), gửi heartbeat (luồng 6).
- Cần thêm cho khớp AI: hiển thị `challenge.text` và `hint` từ `/api/ai/status`. Phiên bản Wokwi hiện chỉ đọc `result` nên vẫn chạy, nhưng chưa hiển thị hướng dẫn.

### Thành viên 2: AI Core và Computer Vision (phần của bạn)

| Việc | Trạng thái |
|---|---|
| Pipeline InsightFace (SCRFD, ArcFace 512 chiều) | Đã làm |
| So khớp Cosine Similarity với vector gốc | Đã làm |
| Chống giả mạo mức 2: model MiniFASNet (ONNX) | Đã làm, đã kiểm thử bằng ảnh |
| Chống giả mạo mức 1: thách thức ngẫu nhiên quay đầu/chớp mắt | Đã làm, đã kiểm thử bằng ảnh tĩnh và landmark tổng hợp |
| "Chặn cửa": chỉ chạy AI khi có phiên, mặt đủ lớn, chỉ một người | Đã làm |
| API `/trigger`, `/status`, `/cancel`, `/embed`, `/health`, `/reload` | Đã làm |
| Đẩy sự kiện sang Backend và ThingsBoard (luồng 3 và 4) | Đã làm, đã kiểm thử với server giả |
| Hỗ trợ GPU/CUDA | Đã có công tắc `AI_USE_GPU=1`; chưa chạy thử với CUDA (cần `onnxruntime-gpu`) |
| **Hiệu chỉnh trên camera thật** (`AI_TURN_DELTA`, `AI_PAD_THRESHOLD`, `AI_SIM_THRESHOLD`) | **Chưa làm, cần làm trước khi demo** |
| Đo tỉ lệ từ chối nhầm người thật (FRR) và chấp nhận nhầm (FAR) | Chưa làm |
| Cảnh báo người lạ lảng vảng khi rảnh | Chưa làm (xem luồng 4) |

### Thành viên 3: Backend, CSDL và An toàn thông tin
- CSDL PostgreSQL: người dùng, thẻ UID, vector khuôn mặt (512 float), phân quyền theo vai trò, nhật ký ra vào, nhật ký kiểm toán.
- API trung tâm: nhận UID (luồng 1), gọi AI (luồng 2), nhận kết quả từ AI (luồng 3), gửi lệnh MQTT (luồng 5), lưu log kèm ảnh nghi phạm.
- Nghiệp vụ an ninh: phân quyền theo khung giờ và vai trò; trạng thái tài khoản (khóa, thu hồi, hết hạn); mã hóa vector sinh trắc và kênh truyền (TLS, token giữa các service).
- Vòng đời và thu hồi: báo mất thẻ có hiệu lực ngay; thẻ đã mất mà bị quẹt lại thì cảnh báo; offboarding xóa ảnh và vector theo chính sách lưu trữ.
- ThingsBoard: tạo thiết bị cho AI và ESP32; cấu hình rule alarm.

### Thành viên 4: Dashboard, kiểm thử và tài liệu
- Dashboard web: trang đăng ký người dùng (quẹt thẻ ghi UID, chụp 3 đến 5 ảnh, hiển thị lỗi theo `reason` của `/embed`); trang giám sát nhật ký thời gian thực, ảnh nghi phạm, danh sách cảnh báo; quản lý vòng đời người dùng; nút mở cửa khẩn cấp (có ghi lý do).
- Dashboard ThingsBoard: nhật ký sự kiện, biểu đồ lượt vào/từ chối theo giờ, tỉ lệ lý do từ chối, trạng thái AI và ESP32, cảnh báo đang mở.
- Kiểm thử:

| Kịch bản | Kết quả mong đợi |
|---|---|
| Đúng thẻ, đúng người thật, làm đúng thách thức | `granted` |
| Đúng thẻ, người khác | `denied` / `face_mismatch` |
| Đúng thẻ, ảnh điện thoại của chủ thẻ | `denied` / `spoof_pad` hoặc `challenge_failed` |
| Đúng thẻ, ảnh in | `denied` / `spoof_pad` hoặc `challenge_failed` |
| Đúng thẻ, video của chủ thẻ trên điện thoại | `denied` (video quay sẵn là kịch bản khó nhất, cần thử thật) |
| Thẻ hợp lệ nhưng chưa đăng ký mặt | `denied` / `not_enrolled` |
| Đứng quá xa | `hint = move_closer`, rồi `timeout` |
| Hai người trong khung | `hint = multiple_faces` |
| Tắt AI Service | Backend từ chối với `ai_unavailable` |
| Mất mạng ESP32 | Không mở cửa bằng quét thẻ |
| Quẹt sai thẻ 3 lần | Khóa tạm 15 giây |

- Tài liệu: sơ đồ kiến trúc, use case, sequence diagram, báo cáo bài tập lớn, slide bảo vệ.

### Điểm giao thoa cần họp thống nhất

| Giao thoa | Nội dung cần chốt |
|---|---|
| Thành viên 1 và 2 | JSON hiển thị OLED từ `/api/ai/status` (`challenge.text`, `hint`) |
| Thành viên 1 và 3 | Định dạng luồng 1, luồng 5 (kể cả lệnh `deny`) và luồng 6 |
| Thành viên 2 và 3 | Luồng 2 và 3: Backend gửi `embedding` trong `/trigger`; Backend nhận payload ở luồng 3; token giữa service |
| Thành viên 3 và 4 | Danh sách endpoint CRUD ở mục 7, định dạng nhật ký và cảnh báo |
| Thành viên 2 và 4 | Ngưỡng hiệu chỉnh, kịch bản kiểm thử chống giả mạo, ảnh dùng để đăng ký |

---

## 10. Giới hạn đã biết

- Chống giả mạo hiện chặn được ảnh tĩnh (mức 1) và ảnh in hoặc ảnh trên màn hình (mức 2). Chưa chống được mặt nạ 3D hay deepfake. Video quay sẵn chỉ giảm khả năng thành công, chưa kiểm thử thật.
- Các ngưỡng đang đặt theo ước lượng và chỉ thử với ảnh tĩnh, chưa thử trên webcam thật.
- Một AI Service phục vụ một camera và một phiên tại một thời điểm.
