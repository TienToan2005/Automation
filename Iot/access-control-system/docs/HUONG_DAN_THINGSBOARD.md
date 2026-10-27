# Hướng dẫn xây dựng ThingsBoard cho hệ thống kiểm soát ra vào

Dành cho thành viên phụ trách **dashboard / giám sát**. Đọc xong có thể tự dựng ThingsBoard, nối dữ liệu từ hệ thống thật và làm dashboard
cùng cảnh báo mà không cần hỏi lại người viết code.

> Tên menu ThingsBoard theo bản 4.x (ThingsBoard Cloud và bản Docker 4.3). Giao diện có thể khác chút giữa các phiên bản, ý nghĩa các bước giữ nguyên.
> **Tuyệt đối không commit token thiết bị vào git, không dán token vào tài liệu hay nhóm chat công khai.** Token nằm ngay trong địa chỉ gửi dữ liệu.

## 1. Bức tranh tổng thể

```
ESP32 (cửa) ──HTTP──► Backend (cổng 8080) ──┐
                         │                  │  POST /api/v1/<TOKEN_BACKEND>/telemetry
                         ▼                  ├──────────────────────────────────────────► ThingsBoard
                  AI Service (cổng 5050) ───┘  POST /api/v1/<TOKEN_AI>/telemetry          (thiết bị: backend-door, ai-service)
```

Có **hai nguồn** gửi dữ liệu lên, mỗi nguồn dùng **một thiết bị riêng** trên ThingsBoard (token riêng):

| Thiết bị trên ThingsBoard | Ai gửi | Gửi khi nào | Bật bằng |
|---|---|---|---|
| `backend-door` | **Backend** | Mỗi dòng nhật ký ra vào mới: quẹt thẻ, kết quả quét mặt, lần thử sai, nút trong, mở cửa từ xa, thao tác quản trị, sự kiện cửa (relay đóng / nhả) | biến `LAB_THINGSBOARD_URL` trong file `.env` |
| `ai-service` | **AI Service** | Khi một phiên quét mặt kết thúc (cho vào, từ chối, hết giờ) hoặc có lần thử sai mặt | tham số `-ThingsBoard` của `start_ai.ps1` (hoặc biến `AI_THINGSBOARD_URL`) |

ESP32 **chưa** gửi trực tiếp lên ThingsBoard (nó chỉ nói chuyện với backend). Trạng thái ESP32 (online, WiFi, relay) hiện xem trên trang Tổng quan của web.

## 2. Danh sách việc cần tạo trên ThingsBoard

- [ ] 1 tài khoản / 1 máy chủ ThingsBoard (Cloud hoặc Docker, mục 3)
- [ ] **2 thiết bị**: `backend-door`, `ai-service` (mục 4), lấy **2 access token**
- [ ] 1 **Device profile** `door-profile` (tùy chọn, dùng để đặt luật cảnh báo, mục 8)
- [ ] 1 **Dashboard** `Cửa thông minh` với các widget ở mục 7
- [ ] Các **luật cảnh báo (alarm rule)** ở mục 8 (tùy chọn nhưng nên có)
- [ ] Tài khoản người dùng / link chia sẻ cho thành viên khác (mục 9)

## 3. Chọn máy chủ (một trong hai)

| Cách | Khi nào dùng | Cách làm |
|---|---|---|
| **ThingsBoard Cloud** | Nhanh nhất, cả nhóm cùng xem qua Internet, không cài gì | Đăng ký <https://thingsboard.cloud> (hoặc <https://eu.thingsboard.cloud>), không cần thẻ tín dụng. Địa chỉ gửi dữ liệu: `https://thingsboard.cloud/api/v1/<TOKEN>/telemetry` |
| **Tự cài bằng Docker** | Chạy riêng trên máy, không phụ thuộc Internet | `cd docs\thingsboard`, chạy **một lần** `docker compose run --rm -e INSTALL_TB=true -e LOAD_DEMO=true thingsboard-ce`, rồi `docker compose up -d`. Đợi 1–2 phút, mở <http://localhost:9080>, đăng nhập `tenant@thingsboard.org` / `tenant` (**đổi mật khẩu ngay**). Địa chỉ gửi dữ liệu: `http://127.0.0.1:9080/api/v1/<TOKEN>/telemetry`. Cổng **9080** (không phải 8080) vì backend đã dùng 8080. Cần 2–3 GB RAM trống |

Với bản Docker, máy khác trong nhóm vào bằng `http://<IP máy chạy ThingsBoard>:9080` (nhớ mở cổng 9080 trên tường lửa Windows).

## 4. Tạo thiết bị và lấy token

Lặp lại hai lần (một lần cho mỗi thiết bị):
1. Menu **Entities ▸ Devices**, bấm dấu **+** ▸ **Add new device**.
2. **Name** = `backend-door` (lần hai: `ai-service`). **Device profile** = `default` (hoặc `door-profile` nếu đã tạo ở mục 8). Bấm **Add**.
3. Bấm vào thiết bị vừa tạo ▸ **Copy access token**. Ghi lại **riêng cho từng thiết bị**, không nhầm token của thiết bị này sang thiết bị kia.

Địa chỉ gửi dữ liệu của mỗi thiết bị = `<máy chủ>/api/v1/<TOKEN>/telemetry`.

## 5. Nối hệ thống thật vào ThingsBoard

### 5.1. Backend (thiết bị `backend-door`)
Mở file `.env` ở **thư mục gốc dự án** (file này nằm trong `.gitignore`), thêm một dòng:

```
LAB_THINGSBOARD_URL=https://thingsboard.cloud/api/v1/<TOKEN_BACKEND>/telemetry
```

Rồi chạy lại backend:

```bash
docker compose up -d --build backend
```

Để trống biến này là tắt tính năng. Gửi ở luồng nền: ThingsBoard chậm hoặc mất mạng thì cửa vẫn chạy bình thường (lỗi chỉ ghi log, token không được in ra).

### 5.2. AI Service (thiết bị `ai-service`)

```bash
cd ai_service
.\start_ai.ps1 -Camera "http://<IP board>:81/stream" -ThingsBoard "https://thingsboard.cloud/api/v1/<TOKEN_AI>/telemetry"
```

Khi khởi động AI in địa chỉ đã **che token** (`.../api/v1/***/telemetry`).

### 5.3. Kiểm tra nhanh bằng tay (không cần chạy hệ thống)

```powershell
Invoke-RestMethod -Method Post -Uri "https://thingsboard.cloud/api/v1/<TOKEN_BACKEND>/telemetry" `
  -ContentType "application/json" `
  -Body '{"event":"face_verify","result":"granted","granted":1,"reason":"ok","device":"test","student_id":"B23DCAT264","confidence":0.85}'
```

Không báo lỗi là được. Trong ThingsBoard mở thiết bị ▸ tab **Latest telemetry**: phải thấy các khóa vừa gửi. Thiết bị chuyển sang **Active**.

## 6. Từ điển dữ liệu (telemetry)

### 6.1. Thiết bị `backend-door` (mỗi dòng nhật ký một bản tin)

| Khóa | Kiểu | Ý nghĩa |
|---|---|---|
| `event` | chuỗi | `card_scan`, `face_verify`, `exit_button`, `remote_open`, `admin_action`, `door_event` |
| `result` | chuỗi | `granted`, `denied`, `pending`, `timeout`, `cancelled` |
| `granted` | số 0/1 | 1 khi `result = granted` (tiện cho biểu đồ và thống kê) |
| `reason` | chuỗi | Lý do chi tiết, xem bảng dưới |
| `device` | chuỗi | Mã thiết bị (cửa hoặc camera) |
| `student_id` | chuỗi | Mã người dùng, rỗng nếu thẻ lạ |
| `confidence` | số | Độ khớp khuôn mặt (nếu có) |
| `pad_score` | số | Điểm chống giả mạo (nếu có) |

Thời gian: ThingsBoard lấy giờ **lúc nhận** bản tin.

Các `reason` thường gặp:

| Nhóm | `reason` |
|---|---|
| Thẻ / tài khoản | `unknown_card`, `card_revoked`, `card_disabled`, `card_expired`, `card_locked`, `account_suspended`, `rate_limited` |
| Hệ thống | `ai_unavailable`, `not_enrolled` |
| Khuôn mặt | `ok`, `face_mismatch`, `spoof_pad`, `challenge_failed`, `pad_uncertain`, `face_not_detected`, `face_quality_low`, `face_inconclusive`, `timeout`, `cancelled`, `superseded` |
| Quản trị / cửa | `exit_button`, `remote_open`, `admin`, `card_locked` (hệ thống khóa thẻ), `door_unlocked`, `door_locked` |

Chi tiết nghiệp vụ từng mã: `docs/NGHIEP_VU_XAC_THUC.md`.

### 6.2. Thiết bị `ai-service`

| Khóa | Ý nghĩa |
|---|---|
| `event_type` | `access_event` (kết quả thường) hoặc `security_alert` (có lý do cảnh báo) |
| `alert_level` | `high` hoặc `medium` (chỉ với `security_alert`) |
| `message` | Nội dung cảnh báo (chỉ với `security_alert`) |
| `result` | `granted`, `denied`, `timeout`, `cancelled` hoặc `attempt` (một lần thử sai mặt, phiên vẫn mở) |
| `reason`, `student_id`, `camera_id`, `timestamp` | như trên (`timestamp` là giờ AI tạo bản tin) |
| `confidence_score`, `pad_score`, `duration_ms` | độ khớp, điểm chống giả mạo, thời lượng phiên |

## 7. Dashboard đề xuất

### 7.1. Tạo dashboard và alias
1. Menu **Dashboards ▸ +** ▸ **Create new dashboard**, tên `Cửa thông minh`, mở nó rồi bấm biểu tượng **bút chì** (Edit).
2. Bấm **Entity aliases** ▸ **Add alias** hai lần:
   - Alias `backend`: Filter type **Single entity**, Type **Device**, chọn `backend-door`.
   - Alias `ai`: tương tự cho `ai-service`.
3. Bấm **Add widget** để thêm từng widget bên dưới (chọn *Datasource* = alias, rồi chọn các khóa).

### 7.2. Các widget nên có

| # | Widget (nhóm ▸ loại) | Alias | Khóa | Mục đích |
|---|---|---|---|---|
| 1 | Cards ▸ **Value card** (hoặc Simple card) | `backend` | `granted` | "Lần vào gần nhất": 1 = cho vào, 0 = từ chối |
| 2 | Tables ▸ **Timeseries table** | `backend` | `event`, `result`, `reason`, `student_id`, `confidence` | **Nhật ký** ra vào theo thời gian thực |
| 3 | Charts ▸ **Bar chart** (Timeseries bar) | `backend` | `granted`, aggregation **SUM** (ví dụ mỗi giờ) | Số lượt vào theo giờ |
| 4 | Charts ▸ **Timeseries line chart** | `backend` | `confidence`, `pad_score` | Theo dõi độ khớp và điểm chống giả mạo |
| 5 | Cards ▸ **Latest values** (hoặc Entities table) | `ai` | `alert_level`, `message`, `student_id` | Cảnh báo an ninh gần nhất từ AI |
| 6 | Alarm widgets ▸ **Alarms table** | cả hai | – | Danh sách cảnh báo (cần luật ở mục 8) |
| 7 | Gauge ▸ **Radial gauge** | `ai` | `duration_ms` | Thời gian phiên quét (đo độ nhanh của hệ thống) |

Gợi ý bố cục: hàng trên là widget 1, 5 và 7 (con số nhanh); giữa là biểu đồ 3 và 4; dưới cùng là bảng 2 và bảng cảnh báo 6.
Muốn lọc riêng theo `event = face_verify` hay `reason = spoof_pad`, dùng **Data key settings ▸ Post-processing** hoặc tạo thêm một widget lọc theo khóa `reason`.

## 8. Luật cảnh báo (Alarm rules)

Làm trên **Device profile**: menu **Profiles ▸ Device profiles ▸ `door-profile`** (hoặc `default`) ▸ tab **Alarm rules** ▸ **Add alarm rule**.

| Tên alarm | Mức | Điều kiện (khóa `reason`, kiểu String) | Ghi chú |
|---|---|---|---|
| `Giả mạo khuôn mặt` | Critical | `reason` **equal** `spoof_pad` | PAD chắc chắn giả |
| `Sai chủ thẻ` | Major | `reason` **equal** `face_mismatch` | Nghi dùng thẻ người khác |
| `Thẻ bị khóa tạm` | Major | `reason` **equal** `card_locked` | Hệ thống tự khóa thẻ sau nhiều lần sai |
| `Thẻ thu hồi được quẹt` | Critical | `reason` **equal** `card_revoked` | Thẻ báo mất bị dùng lại |
| `AI Service không khả dụng` | Warning | `reason` **equal** `ai_unavailable` | Lỗi hệ thống, không phải tấn công |

Mỗi luật có thể thêm **Clear condition** (ví dụ `granted = 1`) để tự xóa cảnh báo. Gửi cảnh báo ra email / Telegram: menu **Notification center** của ThingsBoard (tạo Recipients, Template, Rule). Hệ thống cũng đã có kênh Telegram / Email riêng ở **backend** (xem README backend).

> Các lỗi **thu nhận** (`face_not_detected`, `face_quality_low`, `face_inconclusive`, `pad_uncertain`, `timeout`) **không phải sự cố an ninh**: đừng tạo alarm cho chúng, chỉ hiển thị trên bảng.

## 9. Chia sẻ cho thành viên khác

| Mục đích | Cách làm |
|---|---|
| Cho **xem** dashboard không cần tài khoản | Dashboard ▸ **Make public** ▸ lấy link. Ai có link đều xem được, đừng dùng nếu dữ liệu nhạy cảm (có mã sinh viên) |
| Cho thành viên **đăng nhập xem hoặc chỉnh** | Menu **Customers** ▸ tạo Customer ▸ **Manage customer users** ▸ **Add user**; gán dashboard cho Customer đó. Người cần quyền quản trị thì tạo thêm Tenant admin trong **Users** (tùy bản) |
| **Lưu dashboard vào dự án** để người khác nhập lại | Dashboard ▸ **Export dashboard** (tệp `.json`), lưu vào `docs/thingsboard/dashboards/`. Người khác dùng **Dashboards ▸ + ▸ Import dashboard**. File export **không chứa token**, nhưng sau khi nhập phải **gán lại alias** (chọn đúng hai thiết bị của họ) |
| Chuyển luật cảnh báo | Device profile ▸ **Export** rồi **Import** ở máy chủ khác |

Khi nhiều người cùng dùng một ThingsBoard, **mỗi người tạo thiết bị của riêng mình** nếu muốn thử nghiệm, tránh làm bẩn dữ liệu của dashboard chính.

## 10. Việc có thể làm tiếp (đề xuất, chưa có trong code)

- ESP32 gửi trực tiếp trạng thái (online, RSSI, relay) lên thiết bị `esp32-door`: cần sửa firmware (`docs/api-spec.md`, luồng 6).
- Mở cửa từ xa từ nút trên dashboard dùng **RPC** của ThingsBoard gọi vào endpoint admin `POST /api/v1/devices/{id}/commands` của backend.
- Biểu đồ tỉ lệ từ chối theo giờ, top người dùng bị từ chối, bản đồ nhiệt giờ cao điểm (từ các khóa `reason`, `student_id`).

## 11. Xử lý lỗi thường gặp

| Hiện tượng | Nguyên nhân và cách xử lý |
|---|---|
| Thiết bị không chuyển Active, không có dữ liệu | Sai token hoặc sai máy chủ trong URL; thử lệnh ở mục 5.3 để tách lỗi |
| Chạy mục 5.3 được nhưng backend không gửi | `LAB_THINGSBOARD_URL` chưa nằm trong `.env` gốc hoặc chưa `docker compose up -d --build backend`; kiểm tra `docker logs backend` có dòng `Gui ThingsBoard that bai` không |
| AI không gửi | Chạy AI không có tham số `-ThingsBoard`; xem dòng `ThingsBoard: ...` khi khởi động |
| Thấy dữ liệu nhưng biểu đồ trống | Khoảng thời gian (**Time window**) của dashboard quá hẹp, đặt "Last 1 hour" hoặc "Last day" |
| Dữ liệu của hai nguồn lẫn nhau | Hai nơi đang dùng chung một token; mỗi nguồn một thiết bị |
| ThingsBoard Docker không lên | Thiếu RAM (cần 2–3 GB trống) hoặc cổng 9080 đã bị chiếm; xem `docker compose logs thingsboard-ce` |

## 12. Bảo mật khi làm việc nhóm
- Token thiết bị = mật khẩu gửi dữ liệu. Ai có token đều gửi được dữ liệu giả vào dashboard. Cấp token riêng cho từng môi trường và **đổi token (Regenerate)** nếu lỡ lộ.
- Dashboard công khai chỉ nên chứa dữ liệu đã ẩn danh (bỏ cột `student_id` khỏi widget công khai).
- Đổi mật khẩu mặc định của tài khoản ThingsBoard tự cài ngay sau khi đăng nhập lần đầu.
