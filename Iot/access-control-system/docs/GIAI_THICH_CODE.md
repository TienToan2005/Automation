# Giải thích code các file của dự án

Bản đồ để người mới đọc code: file nào làm gì, hàm nào quan trọng, dữ liệu đi đường nào. Số dòng ghi gần đúng (có thể lệch khi code đổi).

## 1. Dự án gồm những phần nào

| Phần | Chạy ở đâu | Việc chính |
|---|---|---|
| **firmware** (ESP32-S3) | trên board ở cửa | Đọc thẻ RC522, hiện OLED, đóng relay mở khóa, phát hình camera, báo trạng thái về backend |
| **ai_service** (Python) | laptop, cổng 5050 | Nhận diện khuôn mặt, chống giả mạo, thử thách quay đầu / chớp mắt, báo kết quả về backend |
| **backend** (FastAPI) | Docker, cổng 8080 | "Bộ não": cơ sở dữ liệu, luật nghiệp vụ, API cho ESP32 / AI / web, trang web quản trị |
| **docs** | – | Hướng dẫn chạy, nạp firmware, ThingsBoard, nghiệp vụ, đặc tả API |

## 2. Một lần vào cửa đi qua những file nào

```
1  RC522 đọc thẻ ───────────► firmware/src/main.cpp  rfidRead() → submitCard()
2  POST /access/card-scan ──► backend/app/api/v1/access.py → services/access_service.py handle_card_scan()
        (kiểm tra thẻ: auth_policy.py; tạo phiên: models/auth_session.py)
3  backend gọi AI /trigger ─► services/ai_client.py → ai_service/main.py trigger_scan()
4  AI mở camera ESP32 ──────► ai_service/main.py CameraStream ← firmware/src/camera_stream.cpp (cổng 81)
5  AI xử lý từng khung ─────► main.py process_frame(): chất lượng → khớp mặt → PAD → thử thách (liveness.py)
6  AI báo kết quả ──────────► POST /access/verify → access_service.py handle_verify()
7  ESP32 hỏi kết quả ───────► GET /access/session/{id} → main.cpp pollSession()
8  granted → relay đóng ────► main.cpp unlock() → nhịp tim báo relay → backend ghi door_event
9  Mọi bước ghi nhật ký ────► models/access_log.py; cảnh báo: models/alert.py; ThingsBoard: services/thingsboard.py
```

## 3. Firmware (`firmware/`)

| File | Vai trò |
|---|---|
| `platformio.ini` | Cấu hình biên dịch. Hai môi trường: `esp32s3cam` (log qua cổng UART/CH343, mặc định) và `esp32s3cam_usb` (log qua cổng USB gốc). Khai báo board ESP32-S3 16 MB flash, 8 MB PSRAM và các thư viện (OLED, RC522, JSON) |
| `include/config.h` | **Mọi hằng số cấu hình**: chân GPIO, mức kích relay, thời gian mở khóa, số lần sai liên tiếp (`MAX_TRIES`), thời gian khóa cửa ngắn (`LOCKOUT_MS`), cấu hình camera. Đầu file nạp `config_local.h` nên giá trị riêng của bạn thắng giá trị mặc định |
| `include/config_local.example.h` | Mẫu để copy thành `config_local.h` |
| `include/config_local.h` | **Không lên git**: tên và mật khẩu WiFi, `BACKEND_URL`, `DEVICE_TOKEN` |
| `src/camera_stream.h/.cpp` | Khởi tạo camera OV5640, hai máy chủ HTTP nhỏ: cổng **81** phát luồng MJPEG liên tục (`/stream`, AI đọc), cổng **80** trang xem thử và ảnh tĩnh `/capture`. Có các hàm chỉnh nóng (xung nhịp, độ phân giải, chất lượng, lật hình) cho lệnh Serial `cam ...` |
| `src/main.cpp` | Toàn bộ logic thiết bị, xem bảng dưới |

### `main.cpp` theo từng khối
| Khối | Nội dung |
|---|---|
| Trạng thái | `WAIT_CARD` (chờ thẻ) → `WAIT_FACE` (chờ kết quả quét mặt) → `UNLOCKED` (relay đóng) hoặc `LOCKOUT` (khóa cửa ngắn). Hàm `setState()` đổi trạng thái, tắt / bật relay, hủy phiên khi rời `WAIT_FACE` |
| Relay | `relayInit()` đặt mức "nhả" **trước** khi bật chế độ output (không hút lúc cấp điện); `relaySet()` |
| Mạng | `httpCall()` gọi backend kèm `X-Device-Token`; WiFi tự nối lại trong `loop()` |
| Luật từ chối | `denyText()` đổi mã lý do thành chữ ngắn trên OLED; `countsTowardLockout()` quyết định lý do nào tính vào bộ đếm sai (thẻ sai, giả mạo...), `fail()` hiện "TU CHOI" và khóa cửa 15 giây khi đủ `MAX_TRIES` lần liên tiếp |
| OLED | `drawScreen()` vẽ khung; `showState()` chọn chữ theo trạng thái và gợi ý của AI (nhìn vào, quay trái / phải, chớp mắt, ảnh kém, sai mặt...) |
| Nghiệp vụ | `submitCard()` gửi UID, nhận 202 (có phiên) hoặc 403 / 429 (từ chối); `pollSession()` hỏi kết quả mỗi `POLL_MS` ms và cập nhật OLED; `unlock()` giới hạn tối đa `MAX_UNLOCK_MS` để bảo vệ cuộn dây |
| Nhịp tim / lệnh từ xa | `sendHeartbeat()` báo trạng thái, relay, RSSI, IP mỗi 3 giây và nhận lệnh từ web (mở khóa từ xa, hủy khóa cửa...). Đổi trạng thái relay thì gửi ngay để backend ghi được sự kiện cửa |
| RC522 | `rfidBegin()`, `rfidConfigOk()` (kiểm tra cấu hình và ăng-ten, tự khởi động lại chip nếu bị reset do sụt nguồn), `rfidRead()` trả UID chữ hoa (không đọc / ghi dữ liệu trong thẻ) |
| Lệnh Serial | `handleCommand()`: `help`, `status`, `uid <MA>` (giả lập quẹt thẻ), `open [ms]`, `relay on/off`, `i2c`, `i2cfind`, `exit?`, `rc522 break`, `cam bench/xclk/size/q/flip/off/on` |
| `setup()` / `loop()` | Khởi tạo thiết bị rồi vòng lặp: nối WiFi, nhịp tim, hết giờ mở khóa, khóa cửa ngắn, **nút trong theo cạnh bấm** (nhả rồi bấm mới mở, kẹt thấp thì cảnh báo), đọc thẻ, hỏi kết quả quét mặt |

## 4. AI Service (`ai_service/`)

| File | Vai trò |
|---|---|
| `main.py` (~930 dòng) | Trung tâm. Xem bảng dưới |
| `liveness.py` | `LivenessChallenge`: thử thách ngẫu nhiên quay trái / phải hoặc chớp mắt, dựa trên 68 điểm landmark; `pick_challenge_type()` bỏ chớp mắt khi camera chậm; `PassivePAD`: mô hình MiniFASNet (ONNX) chấm "mặt thật"; `PadWindow`: trung bình trượt 6 khung |
| `enrollment.py` | `inspect_image()` / `analyze_image()`: kiểm tra chất lượng ảnh đăng ký (đúng một mặt, đủ lớn, đủ nét, qua PAD) rồi trả vector 512 chiều; `enroll_faces()` chạy tay nạp ảnh từ `faces_db/` |
| `enroll_guide.py` | `GuidedEnroll`: máy trạng thái đăng ký kiểu ngân hàng (nhìn thẳng → quay trái → quay phải → chớp mắt); `SessionStore`: lưu các phiên quét đăng ký trong bộ nhớ |
| `start_ai.ps1` | Chạy AI với tham số `-Backend`, `-Token`, `-Camera`, `-ThingsBoard` (đặt biến môi trường rồi gọi `python main.py`) |
| `requirements.txt`, `Dockerfile`, `.dockerignore` | Cài thư viện và đóng gói |
| `tools/eval_far_frr.py` | Thu clip và đo FAR / FRR / APCER / BPCER để chọn ngưỡng |
| `tools/calibrate_liveness.py` | Đo số liệu thật của chớp mắt / quay đầu để chỉnh ngưỡng |
| `tools/export_pad_onnx.py` | Chuyển mô hình PAD sang ONNX |
| `tests/` | Kiểm thử: `test_verify_policy.py` (chính sách kết luận, cổng chất lượng, phân loại hết giờ), `test_liveness.py`, `test_enroll_*.py`, `test_enrollment.py`, `test_camera_and_appearance.py` (đọc camera, tự mở lại, chống sập khi đóng) |

### `main.py` theo từng khối
| Khối | Nội dung |
|---|---|
| Hằng số | Các ngưỡng đọc từ biến `AI_...`: độ khớp (`AI_SIM_THRESHOLD`), vùng "chưa kết luận" và "sai chủ thẻ đã xác nhận" (`AI_MISMATCH_CONFIRM_SIM`), PAD chắc chắn giả (`AI_SPOOF_CONF_THRESHOLD`), cổng chất lượng, thời gian phiên |
| `ai_state` | Từ điển trạng thái dùng chung giữa luồng camera và API (phiên hiện tại, kết quả, gợi ý, điểm số) |
| Hàm phân loại | `classify_similarity()`, `face_verifiable()`, `pad_verdict()`, `classify_timeout()`, `appearance_changed()`, `pad_applicable()`: các luật nhỏ, thuần, đã có test |
| Gửi sự kiện | `_event_worker()` đẩy kết quả sang backend (`/access/verify`) và ThingsBoard ở luồng nền, có hàng đợi; `finish()` kết thúc phiên đúng **một lần**; `report_attempt()` ghi một lần thử sai mặt mà không đóng phiên |
| API FastAPI | `/api/ai/trigger` (bắt đầu phiên), `/status` (ESP32 / backend hỏi), `/cancel`, `/reload`, `/health`, `/embed` (vector từ ảnh), `/enroll/*` (đăng ký có hướng dẫn) |
| `CameraStream` | Đọc camera ở luồng riêng, luôn giữ khung **mới nhất**; bỏ vài khung đầu của camera ESP32; tự mở lại khi luồng im lặng; luồng đọc là bên **duy nhất** đóng camera (tránh sập ffmpeg) |
| `SessionCtx` | Bộ đếm theo một phiên: số khung khớp, sai, chưa kết luận, ảnh kém, fps, trạng thái PAD |
| `process_frame()` | Đường xử lý từng khung: dò mặt → bỏ qua khung kém chất lượng → so khớp → phân loại → PAD (chỉ khung nhìn thẳng) → tạo thử thách → cho qua |
| `draw_overlay()` / `HINT_TEXT` | Vẽ chữ và khung mặt lên cửa sổ xem (chỉ để xem) |
| `main()` | Nạp mô hình, mở máy chủ, vòng lặp chính; camera chỉ mở khi có phiên |

## 5. Backend (`backend/`)

### Lõi (`app/core/`, `app/main.py`)
| File | Vai trò |
|---|---|
| `main.py` | Khởi động: tạo bảng, thêm cột còn thiếu (`add_missing_columns`), tạo tài khoản admin lần đầu, chạy tác vụ nền `device_monitor()` (phát hiện ESP32 mất kết nối), gắn router, xử lý chưa đăng nhập |
| `core/config.py` | `Settings` đọc từ biến `LAB_*` hoặc file `.env`: token, AI, thời gian phiên, rate limit, ngưỡng khóa tạm, ThingsBoard, thông báo |
| `core/database.py` | Kết nối SQLite / Postgres; `add_missing_columns()` nâng cấp DB cũ tại chỗ (chỉ thêm cột) |
| `core/security.py` | Băm mật khẩu có muối, tạo / giải mã JWT, `tokens_match()` so sánh token thời gian hằng (chống dò) |
| `core/clock.py` | `utcnow()` và đổi múi giờ (DB luôn lưu UTC, hiển thị UTC+7) |

### Dữ liệu (`app/models/`)
| File | Bảng / nội dung |
|---|---|
| `user.py` | `users`: mã SV, họ tên, vai trò, trạng thái, vector khuôn mặt |
| `card.py` | `cards`: UID, trạng thái lưu, hạn, khóa tạm, bậc cooldown; `effective_status()` tính trạng thái **hiệu lực** (thu hồi > vô hiệu > hết hạn > khóa tạm > hoạt động) |
| `auth_session.py` | `auth_sessions`: mỗi lần quẹt thẻ hợp lệ một dòng; trạng thái chỉ đổi từ `pending` sang trạng thái cuối **một lần** (nguyên tử) |
| `access_log.py` | `access_logs`: nhật ký mọi sự kiện (quẹt thẻ, kết quả mặt, nút trong, mở từ xa, quản trị, sự kiện cửa) |
| `alert.py` | `alerts`: cảnh báo an ninh, có trạng thái đã xử lý |
| `enums.py` | Các hằng: vai trò, trạng thái thẻ / phiên, **mã lý do** (`Reason`), tập lý do không phải bằng chứng an ninh |

### API (`app/api/`, `app/schemas/`)
| File | Vai trò |
|---|---|
| `api/deps.py` | Phụ thuộc dùng chung: `current_user` (đăng nhập), `require_admin`, `require_device` (token ESP32), `require_service` (token AI) |
| `api/v1/access.py` | ESP32 / AI: `card-scan`, `verify`, `session/{id}`, `cancel`, `exit`; tra nhật ký, ảnh nghi phạm, cảnh báo |
| `api/v1/users.py` | Người dùng, đăng ký khuôn mặt, thẻ: cấp, thu hồi, mở khóa, vô hiệu, bật lại, đặt hạn |
| `api/v1/devices.py` | Nhịp tim ESP32, trạng thái thiết bị, lệnh từ xa, mở cửa khẩn cấp, `system/status` |
| `api/v1/auth.py` | Đăng nhập / đăng xuất / đổi mật khẩu |
| `api/v1/notifications.py` | Chuông thông báo trên web |
| `api/v1/reports.py` | Số liệu báo cáo, xuất CSV / Excel |
| `api/v1/router.py` | Gom các router |
| `schemas/*.py` | Mô tả dữ liệu vào ra (Pydantic) cho từng nhóm API |

### Nghiệp vụ (`app/services/`)
| File | Vai trò |
|---|---|
| `access_service.py` | **Luồng chính**: `handle_card_scan()` (giới hạn tần suất, trạng thái thẻ, tạo phiên, gọi AI), `handle_verify()` (ràng buộc phiên, kiểm tra lại lúc cấp quyền, đóng phiên nguyên tử, nhật ký, cảnh báo), `_record_attempt()` (lần thử sai chỉ ghi log), `get_session_status()`, `cancel_session()`, `log_door_event()`, mở cửa từ xa |
| `auth_policy.py` | Giới hạn tần suất (`RateLimiter`), `card_denial_reason()`, bộ đếm lỗi theo loại và khóa tạm tăng dần (5 / 15 / 30 phút) |
| `user_service.py` | Tạo / sửa / xóa người dùng, đăng ký khuôn mặt, các thao tác thẻ có lý do và audit |
| `ai_client.py` | Gọi AI Service (trigger, status, cancel, embed, enroll) |
| `device_registry.py` | Trạng thái ESP32 và hàng đợi lệnh trong bộ nhớ; phát hiện chuyển trạng thái online, khóa, relay |
| `notifier.py` | Thông báo tức thời: chuông web, Telegram, Email, có chống trùng và lọc theo mức |
| `thingsboard.py` | Đẩy mỗi dòng nhật ký mới lên ThingsBoard sau khi commit, ở luồng nền |
| `report_service.py` | Tổng hợp số liệu, xuất CSV / Excel theo giờ địa phương |
| `audit.py` | Ghi một thao tác quản trị vào nhật ký |

### Giao diện web (`app/web/`)
| File | Vai trò |
|---|---|
| `routes.py` | Các trang HTML (Jinja2): đăng nhập, tổng quan, người dùng, chi tiết, nhật ký, cảnh báo, báo cáo, hồ sơ |
| `templates/*.html` | Mỗi trang; `base.html` là khung chung; `_enroll.html` là khối đăng ký khuôn mặt dùng chung |
| `static/app.js` | Hàm dùng chung (gọi API, định dạng, thông báo nổi, bảng nhật ký), đăng ký khuôn mặt có hướng dẫn bằng camera, biểu đồ |
| `static/notify.js` | Chuông thông báo (hỏi `/notifications` mỗi vài giây) |
| `static/style.css` | Giao diện |

### Khác
| File | Vai trò |
|---|---|
| `scripts/seed_demo.py` | Nạp dữ liệu demo |
| `tests/` | `test_acs_policy.py` (nghiệp vụ xác thực, khóa, phiên, audit), `test_flow.py` (luồng tổng), `test_notifications.py`, `test_reports.py`, `test_thingsboard.py`, `test_migration.py` (nâng cấp DB cũ), `test_ai_client.py`, `conftest.py` (môi trường test, AI giả) |
| `.env.example`, `requirements.txt`, `Dockerfile`, `README.md` | Cấu hình mẫu, thư viện, đóng gói, hướng dẫn backend |

## 6. Gốc dự án và tài liệu
| File | Vai trò |
|---|---|
| `docker-compose.yml` | Chạy backend (và AI nếu muốn) bằng Docker |
| `docs/HUONG_DAN_CHAY.md` | Cách chạy cả hệ thống từng bước |
| `docs/HUONG_DAN_NAP_FIRMWARE.md` | Nạp firmware, sơ đồ chân, lệnh Serial |
| `docs/HUONG_DAN_THINGSBOARD.md` | Dựng ThingsBoard, dashboard, cảnh báo |
| `docs/NGHIEP_VU_XAC_THUC.md` | Audit và chính sách phiên, khóa tạm, mã lỗi |
| `docs/api-spec.md` | Đặc tả các luồng giao tiếp REST |
| `docs/thingsboard/docker-compose.yml` | ThingsBoard Docker (giao diện cổng 9080) |

## 7. Ba ý tưởng thiết kế nên nhớ
1. **Fail-secure:** thiếu bất kỳ điều kiện nào (phiên, thẻ, tài khoản, kết quả AI) thì **không** mở cửa; AI hỏng thì từ chối, không có đường mở cửa dự phòng.
2. **Phân biệt lỗi thu nhận với bằng chứng an ninh:** không thấy mặt, ảnh kém, PAD lưng chừng không bao giờ bị tính là tấn công hay khóa thẻ; chỉ sai chủ thẻ đã xác nhận, giả mạo chắc chắn, không qua thử thách mới có hậu quả.
3. **Một nguồn sự thật về phiên:** bảng `auth_sessions` với chuyển trạng thái nguyên tử, nên kết quả muộn, hủy lặp, hay hai kết quả cùng lúc không đổi được phiên đã đóng.
