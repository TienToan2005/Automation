# Backend quản lý ra vào phòng Lab

Backend trung tâm của hệ thống kiểm soát ra vào hai lớp (thẻ + khuôn mặt), kèm giao diện web cho admin và sinh viên.
Bám theo [`../docs/api-spec.md`](../docs/api-spec.md).

**Mục tiêu:** khởi động Wokwi → vào web → thấy thiết bị online, mở cửa từ xa, xem nhật ký thời gian thực.

## Mục lục

1. [Hệ thống gồm những gì](#1-hệ-thống-gồm-những-gì)
2. [Chạy demo từ đầu đến cuối](#2-chạy-demo-từ-đầu-đến-cuối)
3. [Luồng đi của dữ liệu](#3-luồng-đi-của-dữ-liệu)
4. [Điều khiển thiết bị từ web](#4-điều-khiển-thiết-bị-từ-web)
5. [Phân quyền](#5-phân-quyền)
6. [Cấu trúc mã nguồn](#6-cấu-trúc-mã-nguồn)
7. [Danh sách API](#7-danh-sách-api)
8. [Cấu hình](#8-cấu-hình)
9. [Kiểm thử](#9-kiểm-thử)
10. [Xử lý sự cố](#10-xử-lý-sự-cố)
11. [Giới hạn đã biết](#11-giới-hạn-đã-biết)

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

## 5. Phân quyền

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

## 6. Cấu trúc mã nguồn

Tổ chức theo lớp để mỗi file làm một việc. **Mỗi model một file** trong `models/`, đúng cách các dự án lớn vẫn làm.

```
backend/
├── app/
│   ├── main.py                 # Tạo FastAPI app, gắn router, khởi tạo DB + admin
│   ├── core/                   # Hạ tầng dùng chung, không biết gì về nghiệp vụ
│   │   ├── config.py           #   Settings (pydantic-settings, đọc LAB_* / .env)
│   │   ├── database.py         #   engine, SessionLocal, Base, get_db
│   │   ├── security.py         #   băm mật khẩu, tạo/giải mã JWT
│   │   └── clock.py            #   utcnow()
│   ├── models/                 # Bảng CSDL (SQLAlchemy), mỗi bảng một file
│   │   ├── user.py  card.py  access_log.py  alert.py
│   │   └── enums.py            #   Role, UserStatus, LogEvent, AccessResult...
│   ├── schemas/                # Dữ liệu vào/ra của API (pydantic)
│   │   ├── auth.py  user.py  access.py  device.py  common.py
│   ├── services/               # Nghiệp vụ, không phụ thuộc HTTP
│   │   ├── access_service.py   #   quẹt thẻ, nhận kết quả AI, trạng thái phiên, lệnh từ xa
│   │   ├── user_service.py     #   CRUD người dùng, thẻ, đăng ký khuôn mặt
│   │   ├── ai_client.py        #   gọi AI Service
│   │   ├── device_registry.py  #   trạng thái ESP32 + hàng đợi lệnh
│   │   └── audit.py            #   ghi thao tác quản trị vào nhật ký
│   ├── api/
│   │   ├── deps.py             #   dependency: current_user, require_admin, require_device, require_service
│   │   └── v1/                 #   router mỏng: nhận request → gọi service → trả response
│   │       ├── auth.py  users.py  access.py  devices.py  router.py
│   └── web/                    # Giao diện
│       ├── routes.py           #   các trang HTML
│       ├── templates/          #   Jinja2
│       └── static/             #   app.js, style.css
├── scripts/seed_demo.py        # Nạp dữ liệu demo
├── tests/                      # pytest
├── .env.example
└── requirements.txt
```

Quy ước phụ thuộc, chỉ đi một chiều: `api → services → models/core`. Router không chứa logic nghiệp vụ; service không import FastAPI Request/Response (chỉ ném `HTTPException` cho lỗi nghiệp vụ).

**Thêm một bảng mới:** tạo `models/xxx.py`, import vào `models/__init__.py`, thêm `schemas/xxx.py`, `services/xxx_service.py`, `api/v1/xxx.py` rồi `include_router` trong `router.py`.

Cơ sở dữ liệu mặc định là SQLite (`data/lab.db`, tự tạo). Đổi sang PostgreSQL bằng `LAB_DATABASE_URL` (cài thêm driver, ví dụ `psycopg`).
Chưa dùng Alembic: bảng được tạo bằng `create_all` lúc khởi động, sửa cấu trúc bảng đã có thì phải xóa `data/lab.db` hoặc tự migrate.

---

## 7. Danh sách API

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
| GET | `/api/v1/access-logs` | U | Lọc: `date_from, date_to, result, reason, student_id, event, limit, offset` (sinh viên chỉ thấy của mình) |
| GET | `/api/v1/snapshots/{name}` | A | Ảnh nghi phạm |
| GET | `/api/v1/alerts` | A | Cảnh báo (`include_resolved=true` để xem cả đã xử lý) |
| POST | `/api/v1/alerts/{id}/resolve` | A | Đánh dấu đã xử lý |
| GET | `/api/v1/system/status` | A | Số liệu hôm nay + trạng thái AI |
| GET | `/api/v1/devices` | A | Trạng thái các ESP32 |
| POST | `/api/v1/devices/{device_id}/commands` | A | Gửi lệnh `{command: open_lock\|reset_lockout, reason}` |
| POST | `/api/v1/door/emergency-open` | A | Mở cửa từ xa tới thiết bị mặc định `{reason}` |
| POST | `/api/v1/access/card-scan` | D | ESP32 gửi UID thẻ (luồng 1) |
| GET | `/api/v1/access/session/{id}` | D | ESP32 hỏi kết quả phiên quét mặt |
| POST | `/api/v1/access/session/{id}/cancel` | D | ESP32 hủy phiên |
| POST | `/api/v1/access/exit` | D | Ghi log nút mở cửa trong |
| POST | `/api/v1/device/heartbeat` | D | ESP32 báo trạng thái, nhận lệnh |
| POST | `/api/v1/access/verify` | S | AI báo kết quả phiên (luồng 3) |

Trang web: `/login`, `/dashboard`, `/users`, `/users/{id}` (admin); `/me` (sinh viên); `/logs` (cả hai); `/alerts` (admin).

---

## 8. Cấu hình

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

Phía AI Service: `AI_BACKEND_EVENT_URL`, `AI_SERVICE_TOKEN` (xem bước 3). Phía firmware: `BACKEND_URL`, `DEVICE_ID`, `DEVICE_TOKEN` ở đầu `Iot/wokwi/src/main.cpp`.

---

## 9. Kiểm thử

```bash
cd Iot/backend
python -m pytest -q
```

21 test, dùng DB tạm và AI giả, không cần webcam hay Wokwi. Bao phủ: phân quyền admin/sinh viên, đăng ký mặt, toàn bộ luồng quẹt thẻ → AI → mở cửa, các lý do từ chối, cảnh báo giả mạo, chống ghi trùng phiên, heartbeat, lệnh từ xa (giao một lần, offline, lý do ngắn).

**Chưa kiểm thử được tự động:** chạy firmware trong Wokwi và AI với webcam thật. Firmware đã biên dịch thành công (`pio run`) nhưng cần bạn chạy mô phỏng để xác nhận cuối cùng.

---

## 10. Xử lý sự cố

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

## 11. Giới hạn đã biết

- Trạng thái thiết bị và hàng đợi lệnh nằm trong **bộ nhớ** của tiến trình: khởi động lại backend thì mất (thiết bị tự xuất hiện lại sau heartbeat đầu tiên), và không chạy được nhiều worker. Cần Redis/DB nếu mở rộng.
- Lệnh từ xa trễ tối đa ~3 giây (chu kỳ heartbeat). Muốn tức thời cần MQTT hoặc WebSocket.
- Vector khuôn mặt lưu JSON **chưa mã hóa** trong DB.
- Đếm quẹt sai 3 lần chỉ có ở firmware; backend chưa chặn theo thẻ/thời gian.
- Chưa có migration (Alembic), phân trang cảnh báo, hay giới hạn tần suất đăng nhập.
- Mọi thời gian lưu UTC, giao diện hiển thị theo múi giờ Asia/Ho_Chi_Minh.
- Giới hạn của phần AI (ảnh tĩnh, video quay sẵn, ngưỡng chưa hiệu chỉnh trên camera thật) xem `Iot/docs/api-spec.md` mục 10.
