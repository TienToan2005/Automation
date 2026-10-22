# Hướng dẫn chạy hệ thống kiểm soát cửa (từ máy tính tới phần cứng thật)

Đi theo thứ tự: **(A) chạy trên máy tính với webcam → (B) đóng gói Docker → (C) nối phần cứng thật** (board ESP32-S3-CAM, relay, khóa). Mỗi bước chạy được độc lập, nên xong bước nào thì kiểm tra bước đó rồi mới sang bước sau.

## 0. Tổng quan

```
 Trình duyệt (admin/sinh viên) ──► Backend :8080 ◄── ESP32-S3-CAM (mạch thật)
                                     │  ▲               RFID RC522, relay, OLED
                      trigger+vector │  │ verify (kết quả)
                                     ▼  │
                                  AI Service :5050 ◄── camera (webcam hoặc camera của board qua URL)
```

| Thành phần | Thư mục | Cổng |
|---|---|---|
| Backend + Web quản lý | `backend` | **8080** (không phải 8000) |
| AI Service (nhận diện + chống giả mạo) | `ai_service` | 5050 |
| Firmware ESP32-S3-CAM (mạch thật) | `firmware` | – |

Quy tắc: ESP32 chỉ nói chuyện với Backend; Backend gọi AI; AI báo kết quả về Backend; Backend quyết định mở cửa.

## 1. Cài đặt một lần

| Cần | Ghi chú |
|---|---|
| Python 3.11+ | Đã thử trên 3.13 |
| VS Code + extension **PlatformIO** | Chỉ cho bước C (nạp firmware) |
| Docker Desktop | Chỉ cho bước B |
| Webcam | Bước A |
| (Tùy chọn) card NVIDIA | AI nhanh hơn ~6 lần, xem A.3 |

Dùng PowerShell. Các lệnh dưới đây chạy từ thư mục gốc dự án (`Iot`).

## A. Chạy trên máy tính với webcam

### A.1. Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m scripts.seed_demo          # tạo admin + 3 sinh viên demo (thẻ A123, B456, C789)
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Mở <http://localhost:8080>, đăng nhập `admin` / `admin123` (**đổi mật khẩu ngay**). `--host 0.0.0.0` là bắt buộc để ESP32 gọi được vào.

Tùy chọn: sao chép `.env.example` thành `.env` để đổi token, mật khẩu, bật Telegram/Email (xem `backend/README.md` mục 5).

### A.2. AI Service (webcam)

Mở terminal thứ hai:

```powershell
cd ai_service
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
.\start_ai.ps1                        # nối sẵn về backend :8080, camera 0
```

Lần đầu AI tự tải model `buffalo_l` (~280 MB) nên cần Internet. Thấy cửa sổ camera ghi "VUI LONG QUET THE" là sẵn sàng. Kiểm tra: <http://localhost:5050/api/ai/health> phải có `"status":"ok"`, `"pad_enabled":true`.

Camera khác webcam số 0: `.\start_ai.ps1 -Camera 1`. Webcam Windows chỉ một chương trình dùng một lúc; AI chỉ mở camera khi có phiên quét thẻ, nên bấm **Tắt camera** trên web trước khi quẹt thẻ.

### A.3. (Tùy chọn) Chạy AI bằng GPU NVIDIA

```powershell
pip uninstall -y onnxruntime
pip install onnxruntime-gpu==1.26.0
```

Phải đúng 1.26.0 (bản cuối build cho CUDA 12). Khi khởi động AI in `Provider đang chạy: ['CUDAExecutionProvider', ...]`. Cần torch bản CUDA 12 đã cài sẵn để có DLL, hoặc tự cài CUDA 12 + cuDNN 9. Không có GPU thì bỏ qua, AI tự chạy CPU.

### A.4. Đăng ký khuôn mặt và thử

1. Web → **Người dùng** → "Thêm người dùng mới": nhập mã SV, họ tên, mã thẻ (UID), mật khẩu; bấm **Bắt đầu quét khuôn mặt** rồi làm theo hướng dẫn trên camera: nhìn thẳng → quay đầu sang trái → quay đầu sang phải → chớp mắt (hệ thống tự chụp khi bạn làm đúng; cần bật AI Service trước) → **Thêm người dùng**.
2. Quẹt thẻ thử không cần ESP32:

```powershell
curl.exe -X POST http://localhost:8080/api/v1/access/card-scan -H "X-Device-Token: dev-device-token" -H "Content-Type: application/json" -d '{\"device_id\":\"test\",\"uid\":\"A123\"}'
```

Trả `202 ... scan_face` nghĩa là AI đã bắt đầu quét; nhìn vào webcam, làm theo thử thách (quay đầu / chớp mắt). Kết quả hiện ở **Nhật ký** và **Tổng quan**.

Camera trên web chỉ chạy ở `http://localhost` hoặc HTTPS.

## B. Đóng gói bằng Docker

Đã có `backend/Dockerfile`, `ai_service/Dockerfile` và `docker-compose.yml`.

### B.1. Giới hạn cần biết trước

- **Container không dùng được webcam USB trên Docker Desktop (Windows).** Trong Docker, AI phải lấy hình từ **URL ESP32-CAM** (bước C). Nếu muốn dùng webcam laptop (hoặc GPU), chạy AI bằng terminal (bước A.2) và chỉ đóng gói backend, xem B.3.
- Ảnh AI chạy CPU (không có GPU).

### B.2. Chạy cả hai trong Docker (nguồn hình là ESP32-CAM)

Tạo file `.env` cạnh `docker-compose.yml`:

```
AI_CAMERA=http://192.168.1.50:81/stream
LAB_SECRET_KEY=chuoi-ngau-nhien-dai-it-nhat-32-ky-tu
LAB_ADMIN_PASSWORD=mat-khau-manh
# LAB_SERVICE_TOKEN=...   LAB_DEVICE_TOKEN=...   (đổi khi triển khai thật)
# LAB_TELEGRAM_BOT_TOKEN=...  LAB_TELEGRAM_CHAT_ID=...
```

```powershell
docker compose up -d --build
docker compose logs -f ai            # lần đầu tải model ~280 MB, chờ "HỆ THỐNG AI SẴN SÀNG"
```

Web: <http://localhost:8080>. Dữ liệu (DB, ảnh nghi phạm) nằm trong volume `lab-data`, model AI trong `insightface-models`, nên không mất khi tạo lại container. Dừng: `docker compose down` (thêm `-v` nếu muốn xóa cả dữ liệu).

Trong Docker không có lệnh `seed_demo` (nó đọc vector từ thư mục `ai_service/embeddings`); hãy tạo người dùng trực tiếp trên web.

### B.3. Chỉ backend trong Docker, AI chạy bằng terminal (webcam / GPU)

```powershell
$env:LAB_AI_BASE_URL = "http://host.docker.internal:5050"
docker compose up -d --build backend
cd ai_service; .\start_ai.ps1         # AI trên máy thật, gửi kết quả về localhost:8080
```

## C. Nối phần cứng thật

### C.1. Hiện trạng (nói rõ để khỏi hiểu nhầm)

- **Đã có và đã thử:** backend, AI, web, giao thức ESP32 ↔ backend (card-scan, poll phiên, heartbeat, nút trong, lệnh mở từ xa), AI đọc hình từ URL.
- **Firmware cho mạch thật nằm ở thư mục `firmware/`** (đọc thẻ RC522, OLED, relay, tự nối lại WiFi, lệnh thử qua Serial, phát hình camera). Viết cho board **ESP32-S3 N16R8 có camera OV5640**. Đã thử trên mạch: board, camera, RC522 (đọc được chip), WiFi + backend, luồng hình; **OLED chưa lên**; chưa thử quẹt thẻ, relay, khóa. Cách nối dây, cấu hình và nạp: xem **[HUONG_DAN_NAP_FIRMWARE.md](HUONG_DAN_NAP_FIRMWARE.md)**.
- Backend không quan tâm UID lấy từ đâu (chuỗi hex in HOA, ví dụ `04A1B2C3`); firmware gửi `POST /api/v1/access/card-scan`.

### C.2. Linh kiện

| Linh kiện | Gợi ý |
|---|---|
| Board điều khiển **và** camera | **ESP32-S3 N16R8 có sẵn camera OV5640** (một board lo cả cửa lẫn camera) |
| Đầu đọc thẻ | RC522 (13,56 MHz, SPI) hoặc PN532 |
| Relay | Module relay 1 kênh cách ly opto, loại kích được bằng mức 3,3 V |
| Khóa | Khóa chốt điện từ / khóa từ **12 V** (chọn loại phù hợp cửa) |
| Nguồn | Adapter 12 V đủ dòng cho khóa + hạ áp 5 V cho mạch (nguồn khóa tách khỏi nguồn mạch) |
| Phụ | Diode 1N4007 (chống xung ngược), nút mở cửa phía trong, OLED SSD1306, PIR (tùy chọn) |

### C.3. Nối dây

Bảng chân đầy đủ (OLED, relay, RC522, nút trong) nằm ở **[HUONG_DAN_NAP_FIRMWARE.md](HUONG_DAN_NAP_FIRMWARE.md), mục 2**. Lưu ý: camera trên board chiếm GPIO 4–13 và 15–18, nên thiết bị khác phải nối vào chân ngoài nhóm đó.

Khóa qua relay:

```
+12V ──► COM (relay)         NO (relay) ──► (+) khóa ─── (−) khóa ──► GND 12V
                       diode 1N4007 song song với khóa: vạch (cathode) nối phía +
GND mạch ESP32 nối chung GND nguồn 12V (một điểm), IN relay ◄── GPIO 21
```

An toàn:
- Dùng chân **NO** để khóa chỉ mở khi relay đóng. Quyết định khóa "mất điện thì mở" hay "mất điện thì đóng" theo quy định thoát hiểm của phòng; **luôn có cách mở từ bên trong** (nút trong hoặc tay nắm cơ).
- Không cấp nguồn khóa từ chân 5 V của ESP32.
- Thử toàn bộ khi **chưa lắp vào cửa thật**.

### C.4. Camera của board làm nguồn hình cho AI

Camera OV5640 nằm ngay trên board; firmware `firmware/` tự phát luồng MJPEG, không cần nạp chương trình camera riêng. Sau khi nạp (xem [HUONG_DAN_NAP_FIRMWARE.md](HUONG_DAN_NAP_FIRMWARE.md)), log in địa chỉ luồng, ví dụ `http://192.168.1.57:81/stream`. Kiểm tra bằng trình duyệt ở `http://192.168.1.57/`, rồi cho AI dùng:

```powershell
cd ai_service
.\start_ai.ps1 -Camera "http://192.168.1.57:81/stream"
```

(hoặc `AI_CAMERA=...` trong `.env` của Docker). Mặt phải rộng ≥ 120 px trong khung hình (đứng cách camera khoảng 0,5–1 m), nếu không AI nhắc "LẠI GẦN". Hình từ camera này thường trễ hơn webcam và các ngưỡng nhận diện/chống giả **cần hiệu chỉnh lại**: dùng `ai_service/tools/eval_far_frr.py` và `calibrate_liveness.py` (xem docstring đầu mỗi file).

### C.5. Mạng và firmware thật

1. Máy chạy backend và ESP32 phải **cùng mạng LAN**. Lấy IP máy: `ipconfig` (vd `192.168.1.10`); nên đặt IP tĩnh hoặc giữ chỗ trên router.
2. Mở cổng 8080 cho mạng nội bộ (PowerShell quản trị): `New-NetFirewallRule -DisplayName "Lab backend" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow -Profile Private`. Cổng 5050 chỉ cần mở nếu AI ở máy khác.
3. Điền WiFi, IP backend và token vào `firmware/include/config_local.h` (sao chép từ `config_local.example.h`), rồi biên dịch và nạp: chi tiết ở [HUONG_DAN_NAP_FIRMWARE.md](HUONG_DAN_NAP_FIRMWARE.md). Nhớ **đổi token mặc định** ở cả hai phía.
4. Trong web, gán đúng UID thẻ cho người dùng (ô "Mã thẻ RFID"). UID in ra Serial Monitor khi bạn quẹt thẻ: dòng `[RC522] Doc the 04A1B2C3`.

### C.6. Kiểm tra từng lớp, đừng nối hết một lần

| # | Kiểm tra | Đạt khi |
|---|---|---|
| 1 | Mở `http://<ip-esp32-cam>:81/stream` bằng trình duyệt | Thấy hình mượt |
| 2 | `http://localhost:5050/api/ai/health` | `status: ok`, `camera_ok: true` |
| 3 | ESP32 bật nguồn, Tổng quan web | Thiết bị **Online**, có WiFi/IP |
| 4 | Web → Mở cửa từ xa (có lý do), **chưa nối khóa**, nghe relay "tách" | Relay đóng ~5 giây |
| 5 | Nối khóa, mở từ xa lại | Khóa nhả chốt, tự khóa lại |
| 6 | Quẹt thẻ đã đăng ký mặt, nhìn camera | OLED hướng dẫn, đúng người thì mở |
| 7 | Quẹt thẻ nhưng dùng ảnh điện thoại của chủ thẻ | Bị từ chối, chuông web + Telegram báo giả mạo |
| 8 | Rút nguồn ESP32 | Sau ~10 giây web báo "mất kết nối" |

### C.7. Trước khi dùng thật

- Đổi `LAB_SECRET_KEY`, `LAB_ADMIN_PASSWORD`, `LAB_SERVICE_TOKEN`, `LAB_DEVICE_TOKEN` (dùng chuỗi ngẫu nhiên dài).
- Backend nói chuyện HTTP thường trong LAN: đừng mở cổng 8080 ra Internet. Cần truy cập từ xa thì đặt sau VPN hoặc reverse proxy HTTPS.
- Vector khuôn mặt đang lưu chưa mã hóa trong DB; chỉ thu thập khi người dùng đồng ý và bảo vệ máy chủ.
- Sao lưu thư mục `backend/data` (hoặc volume `lab-data`).
- Bật Telegram/Email để nhận cảnh báo (backend/README mục 5).

## D. Sự cố thường gặp

| Hiện tượng | Cách xử lý |
|---|---|
| Quẹt thẻ báo "AI khong san sang" | AI chưa chạy, hoặc `LAB_AI_BASE_URL` sai (trong Docker phải là `http://ai:5050`) |
| Quét xong cửa không mở, web không có log | AI không gửi được về backend: sai `AI_SERVICE_TOKEN` hoặc `AI_BACKEND_EVENT_URL` |
| OLED "LOI MANG" / "LOI TOKEN" | ESP32 không tới được backend (IP, tường lửa, cùng LAN?) / sai `DEVICE_TOKEN` |
| `camera_ok: false` | Webcam đang bị chương trình khác giữ; hoặc URL ESP32-CAM sai/không truy cập được từ máy AI |
| Quét mặt chậm, chớp mắt mãi không qua | Chạy AI bằng GPU (A.3) hoặc giảm độ phân giải camera; xem `backend/README.md` mục 12 |
| Docker: AI "camera_ok: false" | Chưa đặt `AI_CAMERA` là URL ESP32-CAM (container không thấy webcam) |
| Camera trên web báo không dùng được | Mở bằng `http://localhost:8080` (hoặc HTTPS), không dùng IP LAN qua HTTP |

## E. Kết nối ThingsBoard (tùy chọn)

**Hiện có gì:** AI Service tự đẩy một bản tin lên ThingsBoard mỗi khi một phiên quét mặt kết thúc (qua HTTP, cổng thiết bị của ThingsBoard). ESP32 chưa gửi gì lên ThingsBoard: nó chỉ nói chuyện với backend; phần ESP32 → ThingsBoard mới là đề xuất (`docs/api-spec.md`, luồng 6).

### E.1. Có một máy chủ ThingsBoard (chọn một)

| Cách | Làm |
|---|---|
| **ThingsBoard Cloud** (không cài gì, cần Internet) | Đăng ký tại <https://thingsboard.cloud> (Bắc Mỹ) hoặc <https://eu.thingsboard.cloud> (châu Âu), không cần thẻ tín dụng. Địa chỉ gửi dữ liệu: `https://thingsboard.cloud/api/v1/<TOKEN>/telemetry` |
| **Tự cài bằng Docker** (chạy ngay trên máy bạn) | `cd docs\thingsboard`, chạy **một lần** `docker compose run --rm -e INSTALL_TB=true -e LOAD_DEMO=true thingsboard-ce`, rồi `docker compose up -d`. Đợi 1-2 phút, mở <http://localhost:9080>, đăng nhập `tenant@thingsboard.org` / `tenant` (đổi mật khẩu ngay). Địa chỉ gửi dữ liệu: `http://127.0.0.1:9080/api/v1/<TOKEN>/telemetry`. Cổng giao diện là **9080** (không phải 8080 mặc định của ThingsBoard) vì backend đang dùng 8080; cần khoảng 2-3 GB RAM trống |

### E.2. Tạo thiết bị và lấy token

Trong ThingsBoard: **Entities ▸ Devices ▸ dấu "+"** → đặt tên `ai-service` → **Add**. Bấm vào thiết bị vừa tạo → **Copy access token**. Token nằm ngay trong địa chỉ gửi dữ liệu (thay `<TOKEN>`), giữ kín như mật khẩu.

### E.3. Cho AI biết địa chỉ ThingsBoard

```powershell
cd ai_service
.\start_ai.ps1 -ThingsBoard "http://127.0.0.1:9080/api/v1/<TOKEN>/telemetry"
```

Khi khởi động AI in `ThingsBoard: http://127.0.0.1:9080/api/v1/***/telemetry` (token được che). AI chạy trong Docker thì đặt `AI_THINGSBOARD_URL=...` trong file `.env`.

### E.4. Kiểm tra

Gửi thử một bản tin giả (không cần quét mặt):

```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:9080/api/v1/<TOKEN>/telemetry" -ContentType "application/json" -Body '{"event_type":"access_event","result":"granted","reason":"ok","student_id":"B23DCAT296","confidence_score":0.89,"pad_score":0.95}'
```

Không báo lỗi là được. Trong ThingsBoard mở thiết bị `ai-service` ▸ tab **Latest telemetry**: phải thấy các khóa vừa gửi. Sau đó quẹt thẻ và quét mặt thật: mỗi phiên kết thúc (cho vào, từ chối, hết giờ) sinh một bản tin mới. Nếu AI in `[EVENT] Gửi ThingsBoard thất bại: ...` thì sai địa chỉ / token, hoặc ThingsBoard chưa chạy.

### E.5. Dữ liệu AI gửi lên

| Khóa | Có ở bản tin | Ý nghĩa |
|---|---|---|
| `event_type` | cả hai | `access_event` (kết quả một lần ra vào) hoặc `security_alert` (cảnh báo giả mạo / sai chủ thẻ) |
| `result` | `access_event` | `granted`, `denied`, `timeout`, `cancelled` |
| `reason` | cả hai | `ok`, `not_enrolled`, `timeout`, `spoof_pad`, `challenge_failed`, `face_mismatch`, ... |
| `alert_level`, `message` | `security_alert` | `high` hoặc `medium`; mô tả tiếng Việt |
| `student_id`, `camera_id`, `timestamp` | cả hai | mã SV, camera, thời điểm (UTC, dạng chữ) |
| `confidence_score`, `pad_score`, `duration_ms` | cả hai | độ khớp mặt, điểm chống giả mạo, thời gian quét (mili giây) |

`security_alert` được gửi khi `reason` là `spoof_pad` hoặc `challenge_failed` (mức `high`) hoặc `face_mismatch` (mức `medium`); mọi kết quả khác gửi dạng `access_event`.

### E.6. Làm dashboard

**Dashboards ▸ "+" ▸ Create new dashboard**, rồi **Edit mode ▸ Add new widget**, chọn thiết bị `ai-service` làm nguồn dữ liệu:

- **Timeseries table**: nhật ký (khóa `student_id`, `result`, `reason`, `confidence_score`).
- **Cards / Latest values**: giá trị gần nhất (`pad_score`, `confidence_score`).
- **Timeseries line chart**: `confidence_score`, `pad_score` theo thời gian.
- **Alarm widget**: cảnh báo. Tạo luật ở **Profiles ▸ Device profiles ▸ default ▸ Alarm rules**: sinh alarm khi `event_type` bằng `security_alert`.

Dashboard theo dõi trạng thái ESP32 (online, WiFi, relay) cần ESP32 gửi trực tiếp lên ThingsBoard, hiện **chưa có trong firmware**; trang Tổng quan của backend đã hiển thị các thông tin đó.

Tài liệu chi tiết hơn: `backend/README.md` (API, cấu hình, thông báo, báo cáo) và `docs/api-spec.md` (giao thức giữa các thành phần).
