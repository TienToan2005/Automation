# Hướng dẫn chạy hệ thống kiểm soát cửa (từ máy tính tới phần cứng thật)

Đi theo thứ tự: **(A) chạy trên máy tính với webcam → (B) thêm ESP32 mô phỏng Wokwi → (C) đóng gói Docker → (D) thay bằng phần cứng thật** (ESP32-CAM, relay, khóa). Mỗi bước chạy được độc lập, nên xong bước nào thì kiểm tra bước đó rồi mới sang bước sau.

## 0. Tổng quan

```
 Trình duyệt (admin/sinh viên) ──► Backend :8080 ◄── ESP32 (Wokwi hoặc thật)
                                     │  ▲               keypad/RFID, relay, OLED
                      trigger+vector │  │ verify (kết quả)
                                     ▼  │
                                  AI Service :5050 ◄── camera (webcam hoặc ESP32-CAM qua URL)
```

| Thành phần | Thư mục | Cổng |
|---|---|---|
| Backend + Web quản lý | `backend` | **8080** (không phải 8000) |
| AI Service (nhận diện + chống giả mạo) | `ai_service` | 5050 |
| Firmware ESP32 (Wokwi) | `wokwi` | – |

Quy tắc: ESP32 chỉ nói chuyện với Backend; Backend gọi AI; AI báo kết quả về Backend; Backend quyết định mở cửa.

## 1. Cài đặt một lần

| Cần | Ghi chú |
|---|---|
| Python 3.11+ | Đã thử trên 3.13 |
| VS Code + extension **PlatformIO** và **Wokwi** | Chỉ cho bước B |
| Docker Desktop | Chỉ cho bước C |
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

Mở <http://localhost:8080>, đăng nhập `admin` / `admin123` (**đổi mật khẩu ngay**). `--host 0.0.0.0` là bắt buộc để Wokwi và ESP32 thật gọi được vào.

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

1. Web → **Người dùng** → "Thêm người dùng mới": nhập mã SV, họ tên, mã thẻ (UID), mật khẩu; bấm **Bật camera** → **Chụp & Quét khuôn mặt** ít nhất 3 lần (nhìn thẳng, hơi xoay mặt, đủ sáng) → **Thêm người dùng**.
2. Quẹt thẻ thử không cần ESP32:

```powershell
curl.exe -X POST http://localhost:8080/api/v1/access/card-scan -H "X-Device-Token: dev-device-token" -H "Content-Type: application/json" -d '{\"device_id\":\"test\",\"uid\":\"A123\"}'
```

Trả `202 ... scan_face` nghĩa là AI đã bắt đầu quét; nhìn vào webcam, làm theo thử thách (quay đầu / chớp mắt). Kết quả hiện ở **Nhật ký** và **Tổng quan**.

Camera trên web chỉ chạy ở `http://localhost` hoặc HTTPS.

## B. Thêm ESP32 mô phỏng bằng Wokwi

> **Điều kiện:** `wokwi/src/main.cpp` hiện còn dấu xung đột merge (`<<<<<<<`, `>>>>>>>`) nên **không biên dịch được**. Hãy giải quyết xung đột (giữ nhánh "Updated upstream": luồng gọi qua backend) rồi mới làm tiếp.

```powershell
cd wokwi
pio run                               # biên dịch firmware
```

VS Code: mở thư mục `wokwi` → `F1` → **Wokwi: Start Simulator**. Serial Monitor thấy `[WIFI] OK` là ESP32 đã nối mạng (firmware gọi `http://host.wokwi.internal:8080`).

| Việc làm | Kết quả |
|---|---|
| Bấm `A` `1` `2` `3` `#` trên keypad rồi nhìn webcam | OLED hướng dẫn thử thách; đúng thì "MO CUA", relay bật |
| `C789` `#` | "Chua dang ky mat" |
| Mã lạ, sai 3 lần | "BI KHOA" 15 giây |
| Web → Tổng quan → Mở cửa (kèm lý do) | Vài giây sau cửa mở |

`DEVICE_TOKEN` trong firmware phải trùng `LAB_DEVICE_TOKEN` của backend.

## C. Đóng gói bằng Docker

Đã có `backend/Dockerfile`, `ai_service/Dockerfile` và `docker-compose.yml`.

### C.1. Giới hạn cần biết trước

- **Container không dùng được webcam USB trên Docker Desktop (Windows).** Trong Docker, AI phải lấy hình từ **URL ESP32-CAM** (bước D). Nếu muốn dùng webcam laptop (hoặc GPU), chạy AI bằng terminal (bước A.2) và chỉ đóng gói backend, xem C.3.
- Ảnh AI chạy CPU (không có GPU).

### C.2. Chạy cả hai trong Docker (nguồn hình là ESP32-CAM)

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

### C.3. Chỉ backend trong Docker, AI chạy bằng terminal (webcam / GPU)

```powershell
$env:LAB_AI_BASE_URL = "http://host.docker.internal:5050"
docker compose up -d --build backend
cd ai_service; .\start_ai.ps1         # AI trên máy thật, gửi kết quả về localhost:8080
```

## D. Chuyển sang phần cứng thật

### D.1. Hiện trạng (nói rõ để khỏi hiểu nhầm)

- **Đã có và đã thử:** backend, AI, web, giao thức ESP32 ↔ backend (card-scan, poll phiên, heartbeat, nút trong, lệnh mở từ xa), AI đọc hình từ URL.
- **Chưa có:** firmware cho phần cứng thật. Firmware hiện tại viết cho Wokwi (ESP32-S3 + keypad 4x4 + OLED, WiFi `Wokwi-GUEST`) và chưa được thử trên mạch thật. Đầu đọc RFID (RC522/PN532) **chưa có code**: hiện thẻ được "quẹt" bằng cách gõ keypad. Backend không quan tâm UID lấy từ đâu (chữ HOA, chuỗi bất kỳ), nên chỉ cần firmware mới đọc UID rồi gửi `POST /api/v1/access/card-scan`.
- Các bước dưới đây là hướng dẫn nối dây và cấu hình; phần firmware RFID cần viết thêm.

### D.2. Linh kiện

| Linh kiện | Gợi ý |
|---|---|
| Vi điều khiển cửa | ESP32-S3 DevKitC-1 (như firmware hiện tại) hoặc ESP32 thường |
| Camera | **ESP32-CAM (AI-Thinker)** + cáp/đế nạp FTDI hoặc board MB |
| Đầu đọc thẻ | RC522 (13,56 MHz, SPI) hoặc PN532 |
| Relay | Module relay 1 kênh cách ly opto, loại kích được bằng mức 3,3 V |
| Khóa | Khóa chốt điện từ / khóa từ **12 V** (chọn loại phù hợp cửa) |
| Nguồn | Adapter 12 V đủ dòng cho khóa + hạ áp 5 V cho mạch (nguồn khóa tách khỏi nguồn mạch) |
| Phụ | Diode 1N4007 (chống xung ngược), nút mở cửa phía trong, OLED SSD1306, PIR (tùy chọn) |

### D.3. Nối dây theo firmware hiện tại

| Chức năng | Chân ESP32-S3 |
|---|---|
| Relay (HIGH = mở khóa) | GPIO 13 |
| Nút mở cửa trong (về GND, dùng pull-up) | GPIO 9 |
| PIR | GPIO 47 |
| OLED I2C: SDA / SCL | GPIO 3 / GPIO 8 |
| Keypad hàng / cột | 1, 2, 42, 41 / 40, 39, 38, 37 |

Khóa qua relay:

```
+12V ──► COM (relay)         NO (relay) ──► (+) khóa ─── (−) khóa ──► GND 12V
                       diode 1N4007 song song với khóa: vạch (cathode) nối phía +
GND mạch ESP32 nối chung GND nguồn 12V (một điểm), IN relay ◄── GPIO 13
```

An toàn:
- Dùng chân **NO** để khóa chỉ mở khi relay đóng. Quyết định khóa "mất điện thì mở" hay "mất điện thì đóng" theo quy định thoát hiểm của phòng; **luôn có cách mở từ bên trong** (nút trong hoặc tay nắm cơ).
- Không cấp nguồn khóa từ chân 5 V của ESP32.
- Thử toàn bộ khi **chưa lắp vào cửa thật**.

### D.4. ESP32-CAM làm nguồn hình cho AI

1. Arduino IDE → cài board `esp32` → **File ▸ Examples ▸ ESP32 ▸ Camera ▸ CameraWebServer**.
2. Bỏ comment `#define CAMERA_MODEL_AI_THINKER`, điền `ssid`/`password` WiFi nhà bạn, nạp (nối IO0 với GND khi nạp, bỏ ra rồi bấm reset khi chạy).
3. Serial Monitor in địa chỉ IP, ví dụ `192.168.1.50`. Mở `http://192.168.1.50` để xem thử; luồng video MJPEG là `http://192.168.1.50:81/stream`.
4. Đặt độ phân giải khoảng VGA (640x480) cho mượt; gắn camera ngang tầm mặt, đủ sáng, không ngược sáng.
5. Cho AI dùng URL đó:

```powershell
cd ai_service
.\start_ai.ps1 -Camera "http://192.168.1.50:81/stream"
```

(hoặc `AI_CAMERA=...` trong `.env` của Docker). Mặt phải rộng ≥ 120 px trong khung hình (đứng cách camera khoảng 0,5–1 m), nếu không AI nhắc "LẠI GẦN". Lưu ý hình từ ESP32-CAM thường trễ hơn webcam và các ngưỡng nhận diện/chống giả **cần hiệu chỉnh lại** cho camera mới: dùng `ai_service/tools/eval_far_frr.py` và `calibrate_liveness.py` (xem docstring đầu mỗi file).

### D.5. Mạng và firmware thật

1. Máy chạy backend và ESP32 phải **cùng mạng LAN**. Lấy IP máy: `ipconfig` (vd `192.168.1.10`); nên đặt IP tĩnh hoặc giữ chỗ trên router.
2. Mở cổng 8080 cho mạng nội bộ (PowerShell quản trị): `New-NetFirewallRule -DisplayName "Lab backend" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow -Profile Private`. Cổng 5050 chỉ cần mở nếu AI ở máy khác.
3. Trong firmware, đổi các dòng đầu `main.cpp`:
   - `WIFI_SSID`, `WIFI_PASS` thành WiFi thật; bỏ tham số kênh `6` ở `WiFi.begin(...)` (đó là kênh của Wokwi).
   - `BACKEND_URL` thành `http://192.168.1.10:8080` (thay cho `host.wokwi.internal`).
   - `DEVICE_TOKEN` khớp `LAB_DEVICE_TOKEN`, và **đổi token mặc định** ở cả hai phía.
4. Đọc thẻ: thay đoạn nhập keypad bằng đọc UID RC522/PN532 (gửi dạng chuỗi hex in HOA, ví dụ `04A1B2C3`), rồi gọi cùng hàm `submitCard(uid)` hiện có. Trong web, gán đúng UID đó cho người dùng (ô "Mã thẻ RFID").
5. Biên dịch và nạp: `pio run -t upload` (chọn đúng cổng COM), theo dõi bằng `pio device monitor`.

### D.6. Kiểm tra từng lớp, đừng nối hết một lần

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

### D.7. Trước khi dùng thật

- Đổi `LAB_SECRET_KEY`, `LAB_ADMIN_PASSWORD`, `LAB_SERVICE_TOKEN`, `LAB_DEVICE_TOKEN` (dùng chuỗi ngẫu nhiên dài).
- Backend nói chuyện HTTP thường trong LAN: đừng mở cổng 8080 ra Internet. Cần truy cập từ xa thì đặt sau VPN hoặc reverse proxy HTTPS.
- Vector khuôn mặt đang lưu chưa mã hóa trong DB; chỉ thu thập khi người dùng đồng ý và bảo vệ máy chủ.
- Sao lưu thư mục `backend/data` (hoặc volume `lab-data`).
- Bật Telegram/Email để nhận cảnh báo (backend/README mục 5).

## E. Sự cố thường gặp

| Hiện tượng | Cách xử lý |
|---|---|
| Quẹt thẻ báo "AI khong san sang" | AI chưa chạy, hoặc `LAB_AI_BASE_URL` sai (trong Docker phải là `http://ai:5050`) |
| Quét xong cửa không mở, web không có log | AI không gửi được về backend: sai `AI_SERVICE_TOKEN` hoặc `AI_BACKEND_EVENT_URL` |
| OLED "LOI MANG" / "LOI TOKEN" | ESP32 không tới được backend (IP, tường lửa, cùng LAN?) / sai `DEVICE_TOKEN` |
| `camera_ok: false` | Webcam đang bị chương trình khác giữ; hoặc URL ESP32-CAM sai/không truy cập được từ máy AI |
| Quét mặt chậm, chớp mắt mãi không qua | Chạy AI bằng GPU (A.3) hoặc giảm độ phân giải camera; xem `backend/README.md` mục 12 |
| Docker: AI "camera_ok: false" | Chưa đặt `AI_CAMERA` là URL ESP32-CAM (container không thấy webcam) |
| Camera trên web báo không dùng được | Mở bằng `http://localhost:8080` (hoặc HTTPS), không dùng IP LAN qua HTTP |

Tài liệu chi tiết hơn: `backend/README.md` (API, cấu hình, thông báo, báo cáo) và `docs/api-spec.md` (giao thức giữa các thành phần).
