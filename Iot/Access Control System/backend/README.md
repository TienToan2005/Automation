# Backend quản lý ra vào phòng Lab

Backend trung tâm của hệ thống kiểm soát ra vào hai lớp (thẻ + khuôn mặt), kèm giao diện web cho admin và sinh viên.
Bám theo [`../docs/api-spec.md`](../docs/api-spec.md).

**Mục tiêu:** khởi động Wokwi → vào web → thấy thiết bị online, mở cửa từ xa, xem nhật ký thời gian thực.

## Mục lục

1. [Hệ thống gồm những gì](#1-hệ-thống-gồm-những-gì)
2. [Chạy demo từ đầu đến cuối](#2-chạy-demo-từ-đầu-đến-cuối)
3. [Luồng đi của dữ liệu](#3-luồng-đi-của-dữ-liệu)
4. [Điều khiển thiết bị từ web](#4-điều-khiển-thiết-bị-từ-web)
5. [Thông báo tức thời](#5-thông-báo-tức-thời)
6. [Báo cáo](#6-báo-cáo)
7. [Phân quyền](#7-phân-quyền)
8. [Cấu trúc mã nguồn](#8-cấu-trúc-mã-nguồn)
9. [Danh sách API](#9-danh-sách-api)
10. [Cấu hình](#10-cấu-hình)
11. [Kiểm thử](#11-kiểm-thử)
12. [Xử lý sự cố](#12-xử-lý-sự-cố)
13. [Giới hạn đã biết](#13-giới-hạn-đã-biết)

---

## 1. Hệ thống gồm những gì

| Thành phần | Thư mục | Cổng | Vai trò |
|---|---|---|---|
| **Backend + Web** | `Iot/backend` | 8080 | Trung tâm: lưu người dùng/thẻ/khuôn mặt, ra quyết định, ghi nhật ký, phục vụ web |
| **AI Service** | `Iot/ai_service` | 5050 | Đọc webcam, so khớp mặt, chống giả mạo. Chỉ xác minh, **không** quyết định mở cửa |
| **ESP32 (Wokwi)** | `Iot/wokwi` | – | Bàn phím nhập mã thẻ, OLED, relay khóa, nút mở cửa trong |

```
 Trình duyệt ──────────────► Backend (8080) ◄────────── ESP32 trong Wokwi
   (admin/sinh viên)             │    ▲                   (card-scan, poll phiên,
                                 │    │                    heartbeat, exit)
                    trigger+vector    │ verify (kết quả + ảnh nghi phạm)
                                 ▼    │
                              AI Service (5050) ◄── webcam
```

Nguyên tắc: **ESP32 chỉ nói chuyện với Backend.** Backend gọi AI, AI báo kết quả lại Backend, Backend quyết định, ESP32 hỏi Backend rồi mới mở khóa.

---

## 2. Chạy demo từ đầu đến cuối

Yêu cầu: Python 3.11+, VS Code có extension **PlatformIO** và **Wokwi**, một webcam.

### Bước 1. Cài và nạp dữ liệu demo cho backend

```bash
cd Iot/backend
pip install -r requirements.txt
python -m scripts.seed_demo
```

`seed_demo` tạo admin và 3 sinh viên khớp với firmware, lấy khuôn mặt từ `ai_service/embeddings/*.npy` nên không cần chụp lại ảnh:

| Mã SV | Thẻ (gõ trên keypad Wokwi) | Khuôn mặt | Dùng để thử |
|---|---|---|---|
| `B23DCAT286` | `A123` | có | luồng thành công |
| `B23DCAT296` | `B456` | có | luồng thành công / thử người khác |
| `B23DCAT999` | `C789` | chưa | bị từ chối `not_enrolled` |

Tài khoản web: `admin` / `admin123` (admin), các sinh viên: mã SV / `123456`.

### Bước 2. Chạy backend

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

`--host 0.0.0.0` là **bắt buộc**: Wokwi truy cập backend qua `host.wokwi.internal`, không phải `localhost`.
Mở http://localhost:8080 (tài liệu API tự sinh: http://localhost:8080/docs).

### Bước 3. Chạy AI Service (nối về backend)

```bash
cd Iot/ai_service
pip install -r requirements.txt
set AI_BACKEND_EVENT_URL=http://localhost:8080/api/v1/access/verify
set AI_SERVICE_TOKEN=dev-service-token
python main.py
```

AI mặc định gửi kết quả về `http://localhost:8080/api/v1/access/verify` với token `dev-service-token`, khớp mặc định của backend, nên chạy cùng máy thì chỉ cần `python main.py`; lúc khởi động AI in dòng `[BACKEND] Gửi kết quả về ...` để xác nhận. Chỉ cần đặt biến khi backend ở máy khác hoặc đã đổi `LAB_SERVICE_TOKEN` (có thể dùng `.\start_ai.ps1 -Backend ... -Token ...`). Đặt `AI_BACKEND_EVENT_URL=""` (rỗng) thì AI in cảnh báo và ESP32 sẽ không bao giờ nhận được kết quả.
Trên PowerShell dùng `$env:AI_BACKEND_EVENT_URL="..."` thay cho `set`. Cửa sổ camera hiện lên, dòng chữ "VUI LONG QUET THE" nghĩa là AI sẵn sàng.

**Chạy bằng GPU NVIDIA (tùy chọn, nhanh hơn ~6 lần):** `pip uninstall -y onnxruntime && pip install onnxruntime-gpu==1.26.0`. Phải đúng 1.26.0 (bản cuối build cho CUDA 12); từ 1.27 là CUDA 13, không chạy với torch cu12x. Khi khởi động AI in `[ORT] Provider đang chạy: ['CUDAExecutionProvider', ...]` và `/api/ai/health` có `"gpu": true`. Mặc định `AI_USE_GPU=auto` (có CUDA thì dùng); `AI_USE_GPU=0` ép chạy CPU; `AI_GPU_MEM_MB` (mặc định 1536) giới hạn VRAM.
Nếu chưa muốn dùng AI, bỏ qua bước này: quẹt thẻ sẽ bị từ chối `ai_unavailable` nhưng web, heartbeat và mở cửa từ xa vẫn chạy.

### Bước 4. Chạy Wokwi

```bash
cd Iot/wokwi
pio run                    # hoặc VS Code: PlatformIO → Build
```

Rồi trong VS Code: `F1` → **Wokwi: Start Simulator**. Quan sát Serial Monitor, thấy `[WIFI] OK` là ESP32 đã nối mạng.
Firmware trỏ tới `http://host.wokwi.internal:8080` (sửa ở đầu `src/main.cpp` nếu đổi cổng). `DEVICE_TOKEN` trong firmware phải trùng `LAB_DEVICE_TOKEN` của backend.

### Bước 5. Thử các kịch bản

| Việc làm | Kết quả mong đợi |
|---|---|
| Vào web `/dashboard` sau khi Wokwi chạy vài giây | Thẻ "Thiết bị cửa" hiện `door-esp32-01` **Online**, trạng thái "Chờ quẹt thẻ" |
| Trên keypad Wokwi bấm `A` `1` `2` `3` `#` rồi nhìn webcam | OLED hiện hướng dẫn (ví dụ "QUAY DAU SANG PHAI"); làm đúng → "MO CUA", relay bật; web hiện log "Cho vào" |
| Gõ `C789` `#` | OLED "Chua dang ky mat"; web ghi log từ chối `not_enrolled` |
| Gõ mã lạ `9999` `#` | OLED "The la"; sai 3 lần → "BI KHOA" 15 giây; dashboard hiện nút **Gỡ khóa tạm** |
| Dashboard → nhập lý do → **Mở cửa** | Vài giây sau OLED "MO CUA", relay bật; log "Mở từ xa" kèm lý do |
| Bấm nút xanh "Nut mo cua trong" | Cửa mở; log "Nút trong" |
| Tắt Wokwi, đợi 10 giây | Dashboard chuyển thiết bị sang **Offline**; bấm Mở cửa báo lỗi 409 |
| Đưa ảnh điện thoại của chủ thẻ vào webcam | Bị từ chối (`spoof_pad` hoặc `challenge_failed`); trang Cảnh báo hiện ảnh nghi phạm |

Hai nút "Mat la" / "Mat hop le" trong sơ đồ Wokwi **không còn dùng** (trước đây dùng để giả lập camera). Mọi quyết định mở cửa giờ do backend đưa ra.

---

## 3. Luồng đi của dữ liệu

### 3.1. Mở cửa bằng thẻ + khuôn mặt

```
ESP32                    Backend                          AI Service            Webcam
  │ 1. gõ mã thẻ, bấm #      │                                  │                   │
  │── POST /access/card-scan ►│ tra thẻ → kiểm tra thẻ/tài khoản │                   │
  │   {device_id, uid}        │ /đã có khuôn mặt chưa            │                   │
  │                           │── POST /api/ai/trigger ─────────►│ (kèm vector 512   │
  │                           │   {student_id, session_id,       │  chiều lấy từ DB) │
  │◄── 202 {session_id,       │    embedding}                    │── đọc khung hình ─►│
  │     student_name}         │                                  │   so khớp + chống │
  │                           │                                  │   giả mạo + thử   │
  │ 2. OLED "QUET MAT"        │                                  │   thách thức      │
  │── GET /access/session/id ►│── GET /api/ai/status ───────────►│                   │
  │◄── pending + hint +       │◄─────────────────────────────────│                   │
  │    challenge_text (500ms) │                                  │                   │
  │                           │◄── POST /access/verify ──────────│ kết thúc phiên    │
  │                           │    {result, reason, ảnh nếu từ chối}                  │
  │                           │ ghi AccessLog, tạo Alert nếu nghi giả mạo,           │
  │                           │ kiểm tra lại tài khoản còn hoạt động                 │
  │── GET /access/session/id ►│                                                       │
  │◄── granted ───────────────│                                                       │
  │ 3. bật relay UNLOCK_MS    │                                                       │
```

Chi tiết quan trọng:

- **Từ chối ngay ở bước 1** (không gọi AI): `unknown_card`, `card_revoked`, `account_suspended`, `not_enrolled`, `ai_unavailable`. Mỗi lần đều ghi log; quẹt thẻ **đã thu hồi** còn tạo thêm cảnh báo mức cao.
- **Vector khuôn mặt luôn lấy từ DB** và gửi kèm mỗi lần gọi AI → xóa khuôn mặt hoặc khóa tài khoản có hiệu lực ngay, không cần đồng bộ file.
- **Kiểm tra lại lúc mở cửa:** nếu admin khóa tài khoản trong lúc đang quét mặt, kết quả `granted` của AI bị đổi thành `denied/account_suspended`.
- **Chống ghi trùng:** `/access/verify` nhận trùng `session_id` thì bỏ qua, AI có gửi lại cũng không sao.
- **Nghi giả mạo** (`spoof_pad`, `challenge_failed`, `face_mismatch`) → tạo `Alert` kèm ảnh chụp; admin xem ở trang Cảnh báo.
- ESP32 tự hết giờ sau 17 giây; khi rời bước quét mặt mà chưa có kết luận, nó gọi `/access/session/{id}/cancel` để backend báo AI hủy phiên.

**Người dùng biết phải làm gì bằng cách nào (triển khai thật).** Người đứng trước cửa chỉ nhìn thấy màn hình **OLED**, không thấy máy chủ AI. Hướng dẫn đi theo đường: AI → `GET /access/session/{id}` (backend lấy từ AI `/status`) → ESP32 poll mỗi 500 ms → OLED. Dòng chữ TO là việc cần làm ngay:

| OLED (dòng lớn) | Dòng nhỏ | Khi nào |
|---|---|---|
| `NHIN VAO` | Nhin thang vao camera | vừa quẹt thẻ, AI đang so khớp mặt |
| `LAI GAN` / `KHONG THAY` / `1 NGUOI` | gợi ý khắc phục | mặt quá xa / không thấy mặt / nhiều người trong khung |
| `QUAY PHAI` | `QUAY DAU SANG PHAI >>` | thách thức quay đầu (phải/trái tính theo bên của **chính người dùng**) |
| `QUAY TRAI` | `<< QUAY DAU SANG TRAI` | như trên |
| `CHOP MAT` | `HAY CHOP MAT` | thách thức chớp mắt |
| `MO CUA` / `TU CHOI` + lý do | | kết quả |

Các gợi ý vấn đề (xa, mất mặt...) được ưu tiên hơn thách thức. Dòng dưới cùng luôn hiện tên và số giây còn lại. Cửa sổ camera của AI chỉ dành cho lập trình viên lúc dev (hình soi gương, có fps); khi chạy thật đặt `AI_SHOW_WINDOW=0` để AI chạy ngầm không mở cửa sổ.

### 3.2. Mở cửa bằng nút bên trong

ESP32 bấm nút → `POST /access/exit` (ghi log) → mở relay ngay. Không cần mạng để mở khóa, chỉ cần mạng để ghi log.

### 3.3. Đăng ký khuôn mặt

```
Trình duyệt ── PUT /users/{id}/face (3-8 ảnh) ──► Backend ── POST /api/ai/embed (từng ảnh) ──► AI
                                                    │  AI kiểm tra: có mặt? một người? đủ lớn? không phải ảnh chụp màn hình?
                                                    │  gom các vector hợp lệ → trung bình → chuẩn hóa
                                                    ▼
                                         lưu vào users.face_embedding (JSON 512 số)
```

Cần ít nhất `LAB_MIN_FACE_IMAGES` (mặc định 3) ảnh hợp lệ. Ảnh lỗi trả về kèm lý do tiếng Việt để người dùng chụp lại.
Admin đăng ký cho bất kỳ ai ở trang chi tiết người dùng; sinh viên tự đăng ký ở trang **Hồ sơ của tôi** (chụp bằng webcam trình duyệt hoặc chọn file).

---

## 4. Điều khiển thiết bị từ web

Web không gọi được trực tiếp vào ESP32 (nó nằm sau NAT/Wokwi), nên dùng mô hình **thiết bị hỏi thăm** (heartbeat):

```
ESP32 ── POST /device/heartbeat {state, relay, rssi, uptime, ip, tries} ──► Backend   (mỗi 3 giây)
      ◄── {"commands": [ {command: "open_lock", duration_ms: 5000, reason: "..."} ]} ──
Admin bấm "Mở cửa" ──► POST /door/emergency-open ──► xếp lệnh vào hàng đợi của thiết bị
```

- Lệnh nằm trong hàng đợi tới khi ESP32 gửi heartbeat kế tiếp (trễ tối đa ~3 giây), giao **đúng một lần**, hết hạn sau `LAB_COMMAND_TTL_S` (30 giây) để lệnh cũ không bật cửa bất ngờ.
- Thiết bị không gửi heartbeat trong `LAB_DEVICE_OFFLINE_AFTER_S` (10 giây) bị coi là **offline**; gửi lệnh tới thiết bị offline trả lỗi 409 chứ không xếp hàng.
- Lệnh hỗ trợ: `open_lock` (bắt buộc lý do ≥ 5 ký tự, ghi vào nhật ký) và `reset_lockout` (gỡ khóa tạm sau 3 lần sai).
- Dashboard tự làm mới 3 giây/lần, hiển thị: online/offline, trạng thái (chờ thẻ / đang quét mặt / cửa mở / khóa tạm), khóa đóng hay mở, cường độ WiFi, uptime, IP.

---

## 5. Thông báo tức thời

Sự kiện quan trọng được đẩy tới admin **ngay khi xảy ra**, không cần mở trang Cảnh báo.

| Sự kiện | Mức | Khi nào |
|---|---|---|
| Phát hiện giả mạo (`spoof_pad`), không qua thử thách người thật (`challenge_failed`) | Cao | AI từ chối phiên quét mặt (kèm ảnh nghi phạm) |
| Thẻ đã thu hồi bị quẹt | Cao | Ngay khi quẹt |
| Sai khuôn mặt (nghi dùng thẻ người khác) | Trung bình | AI từ chối vì `face_mismatch` |
| Thiết bị mất kết nối | Trung bình | Không có heartbeat quá `LAB_DEVICE_OFFLINE_AFTER_S` giây |
| Cửa bị khóa tạm (sai nhiều lần) | Trung bình | ESP32 báo `LOCKOUT` lần đầu |
| AI Service không phản hồi | Trung bình | Quẹt thẻ nhưng không gọi được AI |
| Thiết bị kết nối / nối lại, admin mở cửa từ xa | Thông tin | Ngay khi xảy ra |

**Ba nơi nhận:**

1. **Chuông trên web (luôn bật, chỉ admin).** Trình duyệt hỏi `GET /api/v1/notifications?after=<id>` mỗi ~2 giây (10 giây khi tab bị ẩn). Có tin mới thì hiện **toast** (mức cao nằm lại 30 giây và kêu bíp; tắt bíp ở hộp "Âm báo"), chuông hiện số chưa đọc, tiêu đề tab thành `(2) Tổng quan`, và trang Tổng quan / Cảnh báo tự làm mới ngay thay vì đợi chu kỳ. Backend khởi động lại thì trình duyệt tự nối lại. Lịch sử chuông nằm trong RAM (200 tin gần nhất, mất khi khởi động lại backend); bản ghi lâu dài vẫn là nhật ký và cảnh báo trong DB.
2. **Telegram (tùy chọn).** Tạo bot với `@BotFather` để lấy token. Nhắn cho bot một tin bất kỳ (hoặc thêm bot vào nhóm rồi nhắn trong nhóm), mở `https://api.telegram.org/bot<TOKEN>/getUpdates` để đọc `chat.id` (nhóm có id âm). Cảnh báo có ảnh nghi phạm thì gửi kèm ảnh. Đặt:
   ```
   LAB_TELEGRAM_BOT_TOKEN=123456:ABC...
   LAB_TELEGRAM_CHAT_ID=123456789
   ```
3. **Email (tùy chọn).** Với Gmail cần bật xác minh 2 bước rồi tạo **mật khẩu ứng dụng** (không dùng mật khẩu đăng nhập). Cổng 587 dùng STARTTLS, cổng 465 dùng SSL:
   ```
   LAB_SMTP_HOST=smtp.gmail.com
   LAB_SMTP_USER=ten@gmail.com
   LAB_SMTP_PASSWORD=mat-khau-ung-dung
   LAB_SMTP_TO=nguoi1@example.com,nguoi2@example.com
   ```

Đặt vào `.env` (mẫu trong `.env.example`) rồi khởi động lại backend. Trên trang **Tổng quan** có thẻ "Thông báo tức thời" hiện kênh nào đang bật và nút **Gửi thông báo thử**: gửi tới mọi kênh đã cấu hình và báo rõ kênh nào lỗi (sai token, chat id, mật khẩu SMTP...).

**Quy tắc đẩy ra Telegram / Email** (chuông web luôn nhận đủ): chỉ từ mức `LAB_NOTIFY_MIN_LEVEL` (mặc định `medium`, đặt `info` để nhận cả tin thông tin); cùng một loại sự kiện (và cùng sinh viên) chỉ đẩy một lần trong `LAB_NOTIFY_COOLDOWN_S` giây (mặc định 30) để không bị spam; gửi ở luồng nền nên không làm chậm request; kênh lỗi (mất mạng, sai token) chỉ ghi log, không ảnh hưởng quẹt thẻ hay mở cửa. Token và mật khẩu bị che khỏi log và phản hồi lỗi. Đặt `LAB_PUBLIC_BASE_URL` (vd `http://192.168.1.10:8080`) để tin nhắn kèm link tới trang web.

Mã nguồn: `services/notifier.py` (toàn bộ câu chữ và quy tắc), `api/v1/notifications.py`, `web/static/notify.js`.

---

## 6. Báo cáo

Trang **Báo cáo** (`/reports`, chỉ admin) tổng hợp ra vào trong một khoảng ngày (mặc định 7 ngày gần nhất; nút nhanh Hôm nay / 7 ngày / 30 ngày / Tháng này; tối đa 366 ngày):

- **Chỉ số:** lượt vào hợp lệ, lượt từ chối + hết giờ, tỉ lệ thành công, số sinh viên khác nhau, nút trong / mở từ xa, **thời gian xác thực trung bình**, độ khớp mặt trung bình, số cảnh báo (còn bao nhiêu chưa xử lý).
- **Biểu đồ** theo ngày và theo giờ trong ngày (giờ cao điểm, giờ nhiều lượt bị từ chối), bảng lý do từ chối, sinh viên vào nhiều nhất / bị từ chối nhiều nhất, thống kê cảnh báo và thời gian xử lý trung bình.
- **Xuất file:** Excel `.xlsx` (7 trang: Tổng quan, Theo ngày, Theo giờ, Lý do từ chối, Sinh viên, Cảnh báo, Nhật ký; có biểu đồ), `nhat-ky.csv`, `canh-bao.csv`, và **In / Lưu PDF** bằng chức năng in của trình duyệt. CSV dùng UTF-8 có BOM nên Excel đọc đúng tiếng Việt; ô bắt đầu bằng `= + - @` được thêm dấu `'` phía trước để Excel không chạy như công thức (ghi chú do người dùng nhập). Excel cần `openpyxl` (có trong `requirements.txt`; thiếu thì nút Excel báo 501, CSV vẫn dùng được). Trang Excel "Nhật ký" tối đa 20.000 dòng, CSV tối đa `LAB_EXPORT_MAX_ROWS` (200.000).

**Cách đếm.** Một lượt quét thẻ + mặt chỉ tính **một lần**, ở dòng `face_verify` (dòng `card_scan` "Đang xác thực" chỉ là lúc bắt đầu phiên). Thẻ bị từ chối ngay ở bước thẻ (thẻ lạ, thu hồi, khóa, chưa đăng ký mặt, AI hỏng) không có dòng `face_verify` nên được tính là một lượt từ chối. Phiên bị hủy không tính. "Thời gian xác thực" lấy từ ghi chú `<số> ms` của dòng `face_verify`.

**Giờ địa phương.** DB lưu UTC, nhưng "một ngày" trong báo cáo và ở bộ lọc ngày của trang Nhật ký là ngày theo `LAB_UTC_OFFSET_HOURS` (mặc định 7): lượt vào lúc 00:30 sáng thứ Hai tính vào thứ Hai chứ không phải Chủ nhật. (Bộ lọc ngày của trang Nhật ký trước đây hiểu theo UTC nên lệch 7 tiếng.)

**Trang Cảnh báo** nay có phân trang (20 / trang), lọc theo mức độ và loại, và khi đánh dấu đã xử lý có thể **ghi chú** (vd "đã hỏi sinh viên, nhầm thẻ"). Hệ thống lưu người xử lý và thời điểm xử lý (dùng để tính thời gian xử lý trung bình). Hai cột mới (`resolved_at`, `resolution_note`) **tự được thêm vào DB cũ** khi khởi động (`add_missing_columns` trong `core/database.py`), không cần xóa `data/lab.db`.

Mã nguồn: `services/report_service.py`, `api/v1/reports.py`, `web/templates/reports.html`, hàm `barChart` trong `web/static/app.js` (biểu đồ SVG tự vẽ, không cần thư viện hay Internet).

---

## 7. Phân quyền

| Chức năng | Admin | Sinh viên |
|---|---|---|
| Quản lý người dùng, thẻ, khóa/mở khóa, xóa | có | không |
| Đăng ký / xóa khuôn mặt | của bất kỳ ai | chỉ của mình |
| Xem nhật ký ra vào | tất cả, kèm ảnh nghi phạm | chỉ của mình |
| Cảnh báo an ninh, trạng thái hệ thống | có | không |
| Xem thiết bị, mở cửa từ xa, gỡ khóa tạm | có | không |
| Đổi mật khẩu của mình | có | có |

- Phân quyền kiểm tra **ở server** trên từng endpoint (không chỉ ẩn nút trên giao diện).
- Người dùng được đọc lại từ DB ở mỗi request: khóa tài khoản hoặc đổi vai trò có hiệu lực ngay, kể cả khi JWT chưa hết hạn.
- Thiết bị và AI dùng token riêng (`X-Device-Token`, `X-Service-Token`), không dùng chung với đăng nhập người dùng.
- Mật khẩu băm PBKDF2-SHA256 (200.000 vòng, salt ngẫu nhiên). JWT lưu trong cookie `HttpOnly`, `SameSite=Lax`.
- Không cho admin tự khóa, tự xóa hoặc tự hạ quyền chính mình.

---

## 8. Cấu trúc mã nguồn

Tổ chức theo lớp để mỗi file làm một việc. **Mỗi model một file** trong `models/`, đúng cách các dự án lớn vẫn làm.

```
backend/
├── app/
│   ├── main.py                 # Tạo FastAPI app, gắn router, khởi tạo DB + admin
│   ├── core/                   # Hạ tầng dùng chung, không biết gì về nghiệp vụ
│   │   ├── config.py           #   Settings (pydantic-settings, đọc LAB_* / .env)
│   │   ├── database.py         #   engine, SessionLocal, Base, get_db
│   │   ├── security.py         #   băm mật khẩu, tạo/giải mã JWT
│   │   └── clock.py            #   utcnow(), giờ địa phương (UTC+7)
│   ├── models/                 # Bảng CSDL (SQLAlchemy), mỗi bảng một file
│   │   ├── user.py  card.py  access_log.py  alert.py
│   │   └── enums.py            #   Role, UserStatus, LogEvent, AccessResult...
│   ├── schemas/                # Dữ liệu vào/ra của API (pydantic)
│   │   ├── auth.py  user.py  access.py  device.py  notification.py  common.py
│   ├── services/               # Nghiệp vụ, không phụ thuộc HTTP
│   │   ├── access_service.py   #   quẹt thẻ, nhận kết quả AI, trạng thái phiên, lệnh từ xa
│   │   ├── user_service.py     #   CRUD người dùng, thẻ, đăng ký khuôn mặt
│   │   ├── ai_client.py        #   gọi AI Service
│   │   ├── device_registry.py  #   trạng thái ESP32 + hàng đợi lệnh
│   │   ├── notifier.py         #   thông báo tức thời: chuông web, Telegram, Email
│   │   ├── report_service.py   #   số liệu báo cáo, xuất CSV / Excel
│   │   └── audit.py            #   ghi thao tác quản trị vào nhật ký
│   ├── api/
│   │   ├── deps.py             #   dependency: current_user, require_admin, require_device, require_service
│   │   └── v1/                 #   router mỏng: nhận request → gọi service → trả response
│   │       ├── auth.py  users.py  access.py  devices.py  notifications.py  reports.py  router.py
│   └── web/                    # Giao diện
│       ├── routes.py           #   các trang HTML
│       ├── templates/          #   Jinja2
│       └── static/             #   app.js (tiện ích, biểu đồ), notify.js (chuông), style.css
├── scripts/seed_demo.py        # Nạp dữ liệu demo
├── tests/                      # pytest
├── .env.example
└── requirements.txt
```

Quy ước phụ thuộc, chỉ đi một chiều: `api → services → models/core`. Router không chứa logic nghiệp vụ; service không import FastAPI Request/Response (chỉ ném `HTTPException` cho lỗi nghiệp vụ).

**Thêm một bảng mới:** tạo `models/xxx.py`, import vào `models/__init__.py`, thêm `schemas/xxx.py`, `services/xxx_service.py`, `api/v1/xxx.py` rồi `include_router` trong `router.py`.

Cơ sở dữ liệu mặc định là SQLite (`data/lab.db`, tự tạo). Đổi sang PostgreSQL bằng `LAB_DATABASE_URL` (cài thêm driver, ví dụ `psycopg`).
Chưa dùng Alembic: bảng được tạo bằng `create_all` lúc khởi động. Cột **mới** thêm vào model được `add_missing_columns` tự thêm vào DB cũ; đổi kiểu hoặc xóa cột thì phải xóa `data/lab.db` hoặc tự migrate.

---

## 9. Danh sách API

Xác thực: **U** = đăng nhập (cookie hoặc `Authorization: Bearer`), **A** = admin, **D** = `X-Device-Token`, **S** = `X-Service-Token`.

| Method | Đường dẫn | Quyền | Mô tả |
|---|---|---|---|
| POST | `/api/v1/auth/login` | – | Đăng nhập `{student_id, password}` |
| POST | `/api/v1/auth/logout` | – | Đăng xuất |
| GET | `/api/v1/auth/me` | U | Thông tin của mình |
| POST | `/api/v1/auth/me/password` | U | Đổi mật khẩu |
| GET / POST | `/api/v1/users` | A | Danh sách / tạo người dùng (kèm `card_uid` tùy chọn) |
| GET | `/api/v1/users/{id}` | U (chính chủ hoặc A) | Chi tiết |
| PUT / DELETE | `/api/v1/users/{id}` | A | Sửa / xóa (offboarding) |
| POST | `/api/v1/users/{id}/suspend`, `/activate` | A | Khóa (kèm lý do) / mở khóa |
| POST | `/api/v1/users/{id}/cards` | A | Cấp thẻ `{uid}` |
| POST | `/api/v1/cards/{id}/revoke` | A | Thu hồi thẻ, có hiệu lực ngay |
| PUT | `/api/v1/users/{id}/face` | U (chính chủ hoặc A) | Đăng ký mặt, multipart `images` (1-8 ảnh) |
| DELETE | `/api/v1/users/{id}/face` | U (chính chủ hoặc A) | Xóa dữ liệu mặt |
| GET | `/api/v1/access-logs` | U | Lọc: `date_from, date_to` (ngày địa phương, hoặc ISO có giờ = UTC), `result, reason, student_id, event, limit, offset` (sinh viên chỉ thấy của mình) |
| GET | `/api/v1/snapshots/{name}` | A | Ảnh nghi phạm |
| GET | `/api/v1/alerts` | A | Cảnh báo: `include_resolved, level, reason, limit, offset`; tổng số ở header `X-Total-Count` |
| POST | `/api/v1/alerts/{id}/resolve` | A | Đánh dấu đã xử lý, body tùy chọn `{note}` |
| GET | `/api/v1/notifications` | A | Thông báo tức thời: `?after=<id>` (mới hơn id đó) hoặc `?limit=` (gần nhất); trả `{boot, last_id, items}` |
| POST | `/api/v1/notifications/test` | A | Gửi thông báo thử tới chuông web và mọi kênh ngoài đã cấu hình, trả kết quả từng kênh |
| GET | `/api/v1/reports/summary` | A | Số liệu báo cáo: `date_from, date_to` (ngày địa phương `YYYY-MM-DD`, gồm cả hai đầu) |
| GET | `/api/v1/reports/export/report.xlsx` | A | Sổ Excel nhiều trang, cùng tham số ngày |
| GET | `/api/v1/reports/export/logs.csv` | A | Nhật ký CSV: thêm `result, event, reason, student_id` |
| GET | `/api/v1/reports/export/alerts.csv` | A | Cảnh báo CSV |
| GET | `/api/v1/system/status` | A | Số liệu hôm nay + trạng thái AI + kênh thông báo đang bật |
| GET | `/api/v1/devices` | A | Trạng thái các ESP32 |
| POST | `/api/v1/devices/{device_id}/commands` | A | Gửi lệnh `{command: open_lock\|reset_lockout, reason}` |
| POST | `/api/v1/door/emergency-open` | A | Mở cửa từ xa tới thiết bị mặc định `{reason}` |
| POST | `/api/v1/access/card-scan` | D | ESP32 gửi UID thẻ (luồng 1) |
| GET | `/api/v1/access/session/{id}` | D | ESP32 hỏi kết quả phiên quét mặt |
| POST | `/api/v1/access/session/{id}/cancel` | D | ESP32 hủy phiên |
| POST | `/api/v1/access/exit` | D | Ghi log nút mở cửa trong |
| POST | `/api/v1/device/heartbeat` | D | ESP32 báo trạng thái, nhận lệnh |
| POST | `/api/v1/access/verify` | S | AI báo kết quả phiên (luồng 3) |

Trang web: `/login`, `/dashboard`, `/users`, `/users/{id}`, `/alerts`, `/reports` (admin); `/me` (sinh viên); `/logs` (cả hai).

---

## 10. Cấu hình

Đặt bằng biến môi trường `LAB_*` hoặc file `.env` (mẫu: `.env.example`).

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `LAB_SECRET_KEY` | khóa dev | Khóa ký JWT, **phải đổi khi triển khai** (≥ 32 ký tự) |
| `LAB_SERVICE_TOKEN` | `dev-service-token` | AI Service dùng khi gọi backend |
| `LAB_DEVICE_TOKEN` | `dev-device-token` | ESP32 dùng khi gọi backend |
| `LAB_AI_BASE_URL` | `http://localhost:5050` | Địa chỉ AI Service |
| `LAB_DEFAULT_DEVICE_ID` | `door-esp32-01` | Thiết bị nhận lệnh `emergency-open` |
| `LAB_UNLOCK_MS` | `5000` | Thời gian mở khóa khi admin mở từ xa |
| `LAB_DEVICE_OFFLINE_AFTER_S` | `10` | Quá ngần này giây không heartbeat = offline |
| `LAB_COMMAND_TTL_S` | `30` | Lệnh chưa giao sau ngần này giây thì hủy |
| `LAB_MIN_FACE_IMAGES` | `3` | Số ảnh hợp lệ tối thiểu khi đăng ký mặt |
| `LAB_ADMIN_ID` / `LAB_ADMIN_PASSWORD` | `admin` / `admin123` | Tài khoản admin tạo lần chạy đầu |
| `LAB_DATABASE_URL` | SQLite `data/lab.db` | Chuỗi kết nối SQLAlchemy |
| `LAB_UTC_OFFSET_HOURS` | `7` | Múi giờ của báo cáo, bộ lọc ngày và tin nhắn |
| `LAB_NOTIFY_MIN_LEVEL` | `medium` | Mức tối thiểu để đẩy ra Telegram / Email (`info`, `medium`, `high`) |
| `LAB_NOTIFY_COOLDOWN_S` | `30` | Cùng loại sự kiện chỉ đẩy ra ngoài một lần trong khoảng này |
| `LAB_PUBLIC_BASE_URL` | trống | Địa chỉ web để tin nhắn kèm link (vd `http://192.168.1.10:8080`) |
| `LAB_TELEGRAM_BOT_TOKEN`, `LAB_TELEGRAM_CHAT_ID` | trống | Bật kênh Telegram khi đủ cả hai |
| `LAB_SMTP_HOST`, `_PORT` (587), `_STARTTLS` (true), `_USER`, `_PASSWORD`, `_FROM`, `_TO` | trống | Bật kênh Email khi có `HOST` và `TO` (và `FROM` hoặc `USER`); `TO` nhiều người ngăn cách bằng dấu phẩy |
| `LAB_EXPORT_MAX_ROWS` | `200000` | Số dòng tối đa khi xuất nhật ký CSV |

Phía AI Service: `AI_BACKEND_EVENT_URL`, `AI_SERVICE_TOKEN` (xem bước 3). Phía firmware: `BACKEND_URL`, `DEVICE_ID`, `DEVICE_TOKEN` ở đầu `Iot/wokwi/src/main.cpp`.

---

## 11. Kiểm thử

```bash
cd Iot/backend
python -m pytest -q
```

62 test, dùng DB tạm và AI giả, không cần webcam hay Wokwi. Bao phủ: phân quyền admin/sinh viên, đăng ký mặt (cả đăng ký trực tiếp bằng webcam), toàn bộ luồng quẹt thẻ → AI → mở cửa, các lý do từ chối, cảnh báo giả mạo, chống ghi trùng phiên, heartbeat, lệnh từ xa (giao một lần, offline, lý do ngắn); **thông báo** (chuông web, con trỏ `after`, chuyển trạng thái thiết bị, Telegram và Email bằng giả lập: mức tối thiểu, chống lặp, lỗi mạng không làm hỏng request, che token); **báo cáo** (đếm theo ngày / giờ địa phương và đúng ở hai biên ngày, CSV có BOM và chống injection, Excel, phân trang cảnh báo, ghi chú xử lý, migration cột). Test không bao giờ gửi tin thật dù `.env` có token.

**Chưa kiểm thử được tự động:** gửi tin tới Telegram / Email thật (chỉ kiểm thử bằng giả lập; hãy dùng nút "Gửi thông báo thử" trên Tổng quan để xác nhận cấu hình của bạn), và chạy firmware trong Wokwi / AI với webcam thật. Firmware đã biên dịch thành công (`pio run`) nhưng cần bạn chạy mô phỏng để xác nhận cuối cùng.

---

## 12. Xử lý sự cố

| Hiện tượng | Nguyên nhân thường gặp |
|---|---|
| Serial Monitor in `[WIFI] That bai` | Wokwi chưa nối mạng; kiểm tra extension Wokwi và quyền dùng gateway |
| Dashboard không thấy thiết bị | Backend chưa chạy với `--host 0.0.0.0`; sai `BACKEND_URL`; tường lửa chặn cổng 8080 |
| OLED hiện "LOI TOKEN" | `DEVICE_TOKEN` trong firmware khác `LAB_DEVICE_TOKEN` |
| Quẹt thẻ luôn "AI khong san sang" | AI Service chưa chạy hoặc `LAB_AI_BASE_URL` sai (kiểm tra http://localhost:5050/api/ai/health) |
| Quét mặt xong cửa không mở, web không có log | AI chưa đặt `AI_BACKEND_EVENT_URL` / `AI_SERVICE_TOKEN` đúng, backend trả 401 |
| "Thiết bị đang offline" khi mở từ xa | Chưa có heartbeat trong 10 giây gần nhất: Wokwi đang dừng hoặc mất mạng |
| Bật camera trên web báo "device in use" / AI báo `can't grab frame` | Hai chương trình tranh nhau một webcam. AI Service chỉ mở camera khi có phiên quét mặt (đặt `AI_KEEP_CAMERA_OPEN=1` để giữ mở suốt). Bấm **Tắt camera** trên web trước khi quẹt thẻ; chạy lại AI Service sau khi sửa |
| Nhập mã thẻ trên Wokwi bị "The la" dù đã tạo thẻ | Xem dòng `[CARD] uid=...` trong Serial Monitor: UID gửi đi phải trùng chính xác UID trong web. Keypad chỉ có `0-9` và `A-D` (không có chữ khác), tối đa 12 ký tự. Backend tự đổi UID sang chữ HOA khi so khớp |
| ESP32 "Het thoi gian quet mat" sau 17 giây, web chỉ có dòng `Quẹt thẻ / Đang xác thực`, không có `Quét mặt` | Kết quả của AI không tới backend. Xem console AI: cảnh báo `Chưa đặt AI_BACKEND_EVENT_URL` hoặc `Backend từ chối ... HTTP 401` (sai token). Dùng `start_ai.ps1` |
| Quét mặt "đơ", chớp mắt mãi không qua | AI xử lý quá chậm nên bỏ lỡ cú chớp (~0,2 giây). Đã tối ưu: giới hạn 4 luồng ONNX (`AI_ORT_THREADS`), bộ phát hiện cỡ 320 (`AI_DET_SIZE`), đọc camera ở luồng riêng; đo được ~3,7 khung hình/giây (trước đó ~0,3). Xem fps/giá trị đo trên cửa sổ camera; nếu vẫn không qua, nới `AI_BLINK_CLOSE_RATIO` (mặc định 0.70, thử 0.80) |
| Web ghi `challenge_failed` dù độ khớp cao (0.8+) | Không làm được thách thức quay đầu/chớp mắt. Cửa sổ camera hiện kiểu **soi gương** (phải của bạn ở bên phải màn hình). Ngưỡng mặc định đã hiệu chỉnh theo số đo thật trên webcam (chớp mắt 0.88/0.93, quay đầu 0.15). Camera hoặc gương mặt khác thì chạy `python tools/calibrate_liveness.py` trong `ai_service`, rồi đặt các biến `AI_*` nó đề xuất (số đo thô lưu ở `tools/calibration_last.csv`). Test: `python -m pytest tests -q` trong `ai_service`. Console AI in `[PERF] ... fps` sau mỗi phiên; dưới 2 fps là quá chậm |
| Đăng ký mặt báo `spoof_suspected` | Ảnh chụp từ màn hình hoặc ảnh in; chụp trực tiếp bằng webcam |
| Đã sửa model mà lỗi cột không tồn tại | Chưa có migration; xóa `data/lab.db` rồi chạy lại `seed_demo` |

---

## 13. Giới hạn đã biết

- Trạng thái thiết bị và hàng đợi lệnh nằm trong **bộ nhớ** của tiến trình: khởi động lại backend thì mất (thiết bị tự xuất hiện lại sau heartbeat đầu tiên), và không chạy được nhiều worker. Cần Redis/DB nếu mở rộng.
- Lệnh từ xa trễ tối đa ~3 giây (chu kỳ heartbeat). Muốn tức thời cần MQTT hoặc WebSocket.
- Vector khuôn mặt lưu JSON **chưa mã hóa** trong DB.
- Đếm quẹt sai 3 lần chỉ có ở firmware; backend chưa chặn theo thẻ/thời gian.
- Chưa có Alembic (chỉ có `add_missing_columns` tự thêm cột mới vào DB cũ) và chưa giới hạn tần suất đăng nhập.
- Lịch sử chuông thông báo nằm trong RAM (mất khi khởi động lại backend, không chia sẻ giữa nhiều worker). Chuông cập nhật bằng cách hỏi 2 giây/lần chứ chưa phải đẩy thật sự (WebSocket / SSE).
- Telegram và Email cần Internet; thông báo chỉ gửi một lần, không có hàng đợi thử lại khi mất mạng.
- Báo cáo đếm theo từng dòng nhật ký (chưa có "ra"), nên chưa tính được thời gian ở trong phòng hay số người đang trong phòng.
- Mọi thời gian lưu UTC, giao diện hiển thị theo múi giờ Asia/Ho_Chi_Minh.
- Giới hạn của phần AI (ảnh tĩnh, video quay sẵn, ngưỡng chưa hiệu chỉnh trên camera thật) xem `Iot/docs/api-spec.md` mục 10.
