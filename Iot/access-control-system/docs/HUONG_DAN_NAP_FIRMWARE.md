# Hướng dẫn nạp firmware cho mạch thật (ESP32-S3-CAM N16R8 + RC522 + OLED + relay + khóa)

Phần cứng của bạn: **một board ESP32-S3 N16R8 có sẵn camera OV5640**, đầu đọc thẻ **RC522**, **OLED**, **module relay**, **khóa chốt điện từ**, nguồn tổ ong + mạch hạ áp **LM2596**. Firmware nằm ở thư mục **`firmware/`** và làm **hai việc trên cùng một board**:

1. **Điều khiển cửa:** đọc thẻ RC522, hiện hướng dẫn lên OLED, gọi backend, đóng/mở relay.
2. **Phát hình cho AI:** camera OV5640 phát luồng MJPEG tại `http://<ip-board>:81/stream`; AI Service đọc luồng này để quét mặt.

> **Trạng thái:** đã thử trên mạch thật: chip đúng loại N16R8; camera OV5640 lên; RC522 trả đúng mã chip (`VersionReg = 0x82`) với chân mới; WiFi + backend thông (`HTTP 200`); luồng hình chạy. **OLED chưa lên** (chưa tìm ra chân SDA/SCL, xem mục 6 bước 2). **Chưa thử:** quẹt thẻ, relay, luồng thẻ, khóa.

## 0. Thứ tự làm

1. Đọc mục 1 (an toàn điện) và mục 2 (sơ đồ chân, khớp dây thật của bạn).
2. Cài công cụ, xác định cổng USB và module (mục 3), điền WiFi (mục 4), nạp (mục 5).
3. Kiểm tra theo thứ tự ở mục 6: **camera trước**, rồi OLED, RC522, relay, backend, luồng thẻ, AI, và **nối khóa sau cùng**.

## 1. An toàn điện (đọc trước khi cấp điện)

| Kiểm tra | Vì sao |
|---|---|
| **Phần 220 V của nguồn tổ ong phải bọc kín** (nắp che domino hoặc băng cách điện). Không chạm vào mạch khi đang cắm điện; rút phích trước khi sửa dây | Điện lưới nguy hiểm tính mạng. Ảnh mạch bạn gửi có nhiều dây quanh domino của nguồn |
| **Chỉnh LM2596 bằng đồng hồ vạn năng TRƯỚC khi nối board / relay / khóa**: vặn biến trở đến đúng điện áp (5 V cho board, hoặc định mức của khóa) | Mạch hạ áp thường ra điện áp bất kỳ; quá áp làm cháy board |
| **Nguồn 5 V cho board phải đủ dòng (≥ 1 A)**. Cổng USB của máy tính có thể không đủ khi vừa phát hình vừa chạy WiFi vừa đóng relay | Thiếu dòng làm board tự reset (log `Brownout detector was triggered`) |
| **Diode 1N4007 song song cuộn khóa**, vạch bạc (cathode) về phía dây (+). Trong ảnh tôi không thấy diode | Cuộn khóa phát xung ngược khi ngắt, làm board reset hoặc hỏng relay |
| **GND chung** giữa board, phần tín hiệu của module relay, mạch hạ áp / nguồn | Không chung GND thì tín hiệu điều khiển relay không ổn định |
| **Jumper H/L của relay để ở H** (khớp `RELAY_ACTIVE_LEVEL HIGH`) | Để sai thì relay hút lúc bật nguồn (xem mục 6 bước 4) |
| **RC522 chỉ cấp 3,3 V**, tuyệt đối không cấp 5 V | 5 V làm hỏng RC522 |
| **Cắm hoặc rút cáp camera (FPC) khi đã TẮT điện** | Cắm lúc đang cấp điện có thể hỏng camera / board |
| **Khi cắm USB để nạp, đừng cấp thêm 5 V ngoài vào chân 5V của board** (hoặc tháo dây 5 V đó ra) | Hai nguồn cùng cấp có thể đẩy ngược điện về cổng USB của máy tính |
| **Nối khóa SAU CÙNG** | Lỗi firmware hoặc dây sai không ảnh hưởng tới khóa |
| **Khóa chốt điện từ rẻ thường chỉ chịu cấp điện vài giây** | Firmware mặc định nhả 5 giây và có trần cứng 15 giây (`MAX_UNLOCK_MS`) |

## 2. Sơ đồ chân (khớp dây thật của bạn)

Camera OV5640 trên board dùng sẵn các chân **GPIO 4–13 và 15–18** (14 chân), nên thiết bị khác phải nối vào chân ngoài nhóm đó. Sơ đồ dưới đây khớp dây thật của bạn, và firmware (`config.h`) đã chỉnh theo:

**Chân tuyệt đối không dùng cho việc khác:**

| Chân | Lý do |
|---|---|
| 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15, 16, 17, 18 | Camera (dữ liệu D0–D7, VSYNC, HREF, PCLK, XCLK, SIOD, SIOC) |
| 35, 36, 37 | PSRAM octal của module N16R8 |
| 19, 20 | USB gốc của chip |
| 43, 44 | UART (cổng nạp / xem log) |
| 0, 3, 45, 46 | Chân khởi động (strapping; GPIO 3 chọn nguồn JTAG). Đừng để mạch ngoài kéo các chân này xuống thấp lúc bật nguồn |
| 2, 48 | Đèn LED trên board |

**Chân firmware dùng (đều là chân trống trên header của board bạn):**

| Linh kiện | Chân linh kiện | GPIO trên board |
|---|---|---|
| **OLED** SSD1306 I2C | VCC / GND | 3V3 / GND |
| | SDA | **1** |
| | SCL | **14** |
| **Relay** | VCC | 5V (chân 5V của board hoặc 5 V từ mạch hạ áp) |
| | GND | GND |
| | IN | **21** (kích mức HIGH: jumper H/L để ở **H**) |
| **RC522** | 3.3V / GND | 3V3 / GND |
| | SDA (là chân SS) | **42** |
| | SCK | **39** |
| | MOSI | **40** |
| | MISO | **41** |
| | RST | nối cố định vào **3V3** (firmware không dùng chân RST của board) |
| | IRQ | để trống |
| **Nút mở cửa trong** (tùy chọn) | một đầu (chân tự kéo lên: `INPUT_PULLUP`) | **47** |
| | đầu kia | GND |
| **PIR** (tùy chọn, bật bằng `USE_PIR 1`) | OUT | **2** |

Đã nối chân khác? Không cần nối lại dây: **sửa `PIN_*` trong `firmware/include/config.h`** cho khớp (chỉ cần tránh các chân ở bảng trên). Dây nào đang nằm ở chân thuộc bảng "không dùng" (4–13, 15–18, ...) thì phải chuyển sang chân trống.

Khóa qua relay (dùng chân **NO** để khóa chỉ nhận điện khi relay hút):

```
Nguồn khóa (+) ──► COM (relay)        NO (relay) ──► (+) khóa
(−) khóa ───────────────────────────────────────────► (−) nguồn khóa
                diode 1N4007 song song cuộn khóa (vạch bạc về phía +)
```

**Giả định về camera:** bảng chân camera trong `config.h` là bản đồ của ESP32-S3-EYE / Freenove (XCLK=15, SIOD=4, SIOC=5, ...), loại phổ biến nhất cho board này và khớp với việc header của bạn ghi sẵn chân 4–18. Nếu camera không lên (mục 6 bước 1), hỏi người bán bảng chân camera của board và sửa `CAM_PIN_*`.

## 3. Công cụ, cổng USB và loại module

1. **VS Code + extension PlatformIO**. Mở Terminal PlatformIO bằng biểu tượng PlatformIO ▸ *New Terminal* để có lệnh `pio`.
2. **Board có hai cổng USB-C, và môi trường nạp phụ thuộc vào cổng bạn cắm:**
   - **Cổng UART** (qua chip CH340/CH343/CP2102, mã USB `1A86` hoặc `10C4`; máy bạn hiện `USB-Enhanced-SERIAL CH343 (COM6)`): dùng môi trường **`esp32s3cam`**, là môi trường **mặc định**. Cổng này **tự vào chế độ nạp, không cần bấm BOOT/RST**, nên nên dùng.
   - **Cổng USB gốc của chip** (mã USB `303A`): dùng môi trường **`esp32s3cam_usb`**. Lần nạp đầu có thể phải vào chế độ nạp thủ công (xem mục 5).
   Xem cổng nào đang có: *Device Manager ▸ Ports (COM & LPT)* hoặc lệnh `pio device list`. Không thấy cổng COM nào thì đổi cáp (cáp phải là cáp **dữ liệu**) hoặc thử cổng kia; cổng UART cần driver CH340 / CP210x.
3. **Xác nhận loại module:** đọc chữ in trên khối kim loại: `ESP32-S3-WROOM-1-N16R8` nghĩa là 16 MB flash + 8 MB PSRAM octal, đúng với cấu hình mặc định trong `platformio.ini`. Nếu là loại khác (N8R8, N16R2, N8R2), xem ghi chú đầu file `platformio.ini` để đổi `flash_size` / `memory_type`. **Chọn sai thì board khởi động lặp đi lặp lại.**
4. Tuỳ chọn, hỏi thẳng chip (thay `COM5`):

```powershell
& "$env:USERPROFILE\.platformio\penv\Scripts\python.exe" "$env:USERPROFILE\.platformio\packages\tool-esptoolpy\esptool.py" --port COM6 chip_id
```

## 4. Cấu hình WiFi và backend

1. Sao chép `firmware/include/config_local.example.h` thành `firmware/include/config_local.h` (file này không bị đưa lên git) rồi sửa:

```cpp
#define WIFI_SSID "TenWiFi2.4GHz"
#define WIFI_PASS "matkhau"
#define BACKEND_URL "http://192.168.1.10:8080"   // IP máy tính chạy backend
#define DEVICE_TOKEN "dev-device-token"          // trùng LAB_DEVICE_TOKEN của backend
```

2. **WiFi phải là 2,4 GHz** (ESP32 không bắt được 5 GHz).
3. **IP backend:** chạy `ipconfig` trên máy tính, lấy `IPv4 Address` của card WiFi. Máy tính và board phải ở **cùng mạng**; WiFi công cộng / của trường có "cách ly thiết bị" sẽ chặn, khi đó dùng hotspot điện thoại hoặc router riêng.
4. **Backend phải đang chạy** (Docker hoặc terminal) và Windows phải cho cổng 8080 từ mạng nội bộ (PowerShell quản trị):

```powershell
New-NetFirewallRule -DisplayName "Lab backend 8080" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow -Profile Private
```

Kiểm tra nhanh từ điện thoại cùng WiFi: mở `http://192.168.1.10:8080/login`.

## 5. Nạp firmware

**Cách A: VS Code.** *File ▸ Open Folder* chọn thư mục **`firmware`** (thư mục có `platformio.ini`). Ở thanh trạng thái dưới cùng phải ghi `env:esp32s3cam` (môi trường mặc định, cho cổng UART; cắm cổng USB gốc thì bấm vào đó chọn `esp32s3cam_usb`). Bấm biểu tượng mũi tên **→ (Upload)**, rồi biểu tượng phích cắm **(Serial Monitor)**.

**Cách B: dòng lệnh** (từ thư mục gốc dự án, trong Terminal PlatformIO):

```powershell
pio run -d firmware -e esp32s3cam -t upload --upload-port COM6
pio device monitor -d firmware -p COM6 -b 115200 --echo
```

Lần đầu PlatformIO tự tải thư viện nên cần Internet.

**Lần nạp đầu qua cổng USB gốc có thể phải vào chế độ nạp thủ công:** nếu board đang chạy một chương trình khác, nó không tự chuyển sang chế độ nạp. Làm như sau: giữ nút **BOOT**, bấm nhả nút **RST/EN**, rồi nhả BOOT. Lúc này cổng COM biến mất và hiện lại, có thể với **số COM khác** (chạy `pio device list` để xem, mã USB sẽ là `303A:1001`). Bấm Upload với đúng số COM đó. Từ lần sau, nạp tự động, không cần giữ nút (môi trường `esp32s3cam_usb` đã đặt `board_upload.after_reset = watchdog_reset` nên nạp xong board tự chạy). Nếu sau khi nạp board im lặng, Serial Monitor chỉ hiện `waiting for download` hoặc không có gì, bấm **RST** một lần.

**Nếu nạp lỗi:**

| Thông báo / hiện tượng | Cách xử lý |
|---|---|
| `Failed to connect` / `Wrong boot mode` / `Timed out waiting for packet header` / `No serial data received` / `Invalid head of packet (0x1B)` | Vào chế độ nạp thủ công như trên (giữ BOOT, bấm RST, nhả BOOT), kiểm tra lại số COM bằng `pio device list`, rồi Upload lại. Lỗi `0x1B` nghĩa là board đang chạy một chương trình khác và in log ra cổng (ví dụ ESP-IDF, mã USB `303A:4001`) nên không tự vào chế độ nạp |
| `could not open port COM5` | Đóng Serial Monitor hoặc chương trình khác đang giữ cổng; rút cắm lại cáp |
| Không có cổng COM | Cáp chỉ sạc, thiếu driver, hoặc cắm nhầm cổng (thử cổng còn lại) |
| Nạp xong nhưng board khởi động lặp lại (boot loop) | Sai loại module: kiểm tra `memory_type` / `flash_size` (mục 3 bước 3). Rút dây khỏi chân 0, 45, 46 nếu có |

## 6. Kiểm tra từng bước (Serial Monitor 115200)

Sau khi nạp, bấm **RST/EN**. Log đúng trông như sau (các dòng không có linh kiện tương ứng sẽ báo lỗi, không làm dừng):

```
=== Kiem soat ra vao v1.0 | ESP32-S3 ===
[PIN] relay=21 (hut khi HIGH)  OLED sda=1 scl=14  nut trong=47
[PIN] RC522 ss=42 sck=39 mosi=40 miso=41 rst=noi 3V3
[OLED] OK o 0x3C
[RC522] VersionReg = 0x92 -> OK
[CAM] Sensor PID=0x5640 (OV5640), PSRAM 8192 KB
[CAM] Camera OK
[WIFI] OK, IP 192.168.1.57, RSSI -55 dBm
[BACKEND] http://192.168.1.10:8080 -> HTTP 200 (OK)
[CAM] Luong cho AI : http://192.168.1.57:81/stream
[CAM] Xem thu      : http://192.168.1.57/   (anh tinh: /capture)
[LOCK] Cho quet the
San sang. Go 'help' de xem lenh thu.
```

Gõ lệnh trực tiếp vào cửa sổ Serial Monitor rồi Enter:

| Lệnh | Tác dụng |
|---|---|
| `help` | Liệt kê lệnh |
| `status` | WiFi, IP, camera, và kết quả gọi backend (`HTTP 200`, `401` sai token, `-1` không tới được) |
| `i2c` | Quét bus I2C, phải thấy OLED ở `0x3C` |
| `i2cfind` | Dò chân SDA/SCL của OLED: in các chân có điện trở kéo lên, rồi thử mọi cặp chân trống (1, 2, 3, 14, 38, 48) và báo cặp nào có OLED (không thử chân 21, 39–42, 47 vì đang có thiết bị khác) |
| `cam bench` | Đo khung hình/giây của riêng camera (chưa tính WiFi) |
| `cam xclk 10` (hoặc 16, 20) / `cam size vga` (hoặc svga, xga) / `cam q 18` | Đổi xung camera / độ phân giải / độ nén ngay lúc chạy, không cần nạp lại. Thử xong ghi giá trị tốt vào `config_local.h` (`CAM_XCLK_MHZ`, `CAM_FRAMESIZE`, `CAM_JPEG_QUALITY`) |
| `cam off` / `cam on` | Tắt / bật camera (để đo xem camera có làm WiFi chậm không) |
| `open` hoặc `open 2000` | Hút relay thử 1,5 giây (hoặc số mili giây nêu, tối đa 5 giây) |
| `relay on` / `relay off` | Bật / tắt relay bằng tay (tự tắt sau 10 giây) |
| `uid A123` | Giả lập quẹt thẻ mã `A123` (không cần đầu đọc) |

**Thứ tự kiểm tra (mỗi bước đạt mới sang bước sau; khóa chưa nối):**

| # | Kiểm tra | Đạt khi | Nếu không |
|---|---|---|---|
| 1 | **Camera** (làm đầu tiên, chỉ cần board + WiFi + camera) | Log có `[CAM] Sensor PID=0x5640 (OV5640)`. Mở `http://<ip>/` trong trình duyệt thấy hình; hình **không soi gương, không lộn ngược**, đủ sáng | `Khoi tao that bai 0x105`: không thấy camera: cắm lại cáp FPC khi đã tắt điện (khóa chốt phải gài chặt, mặt có đồng của cáp áp vào chân tiếp xúc của khe) hoặc bản đồ chân camera khác (hỏi người bán, sửa `CAM_PIN_*`). `KHONG thay PSRAM`: sai `memory_type`. Hình soi gương / ngược: đặt `CAM_HMIRROR` / `CAM_VFLIP` bằng 1 trong `config_local.h` |
| 2 | OLED | Màn hình sáng, hiện "QUET THE" | Log khởi động phải ra `[OLED] OK o 0x3C`. Nếu ra `KHONG thay o 0x3C / 0x3D`: gõ `i2cfind` để dò chân, rồi kiểm tra VCC (3V3), GND, SDA (chân 1), SCL (chân 14). Loại OLED 7 chân là giao tiếp SPI, không dùng được với firmware này. Chữ lệch hoặc rác ở mép: màn 1,3" thường là chip SH1106, không phải SSD1306 |
| 3 | RC522 | Log `VersionReg = 0x91`, `0x92` hoặc `0x82 -> OK` (module của bạn trả `0x82`); quẹt thẻ ra dòng `[RC522] Doc the 04A1B2C3` | `0x00` hoặc `0xFF`: sai dây SPI (đặc biệt SCK/MISO/MOSI), thiếu 3,3 V hoặc GND. OLED hiện "RC522 LOI" |
| 4 | Relay | Gõ `open`: nghe tiếng "tách", đèn relay sáng khoảng 1,5 giây **rồi nhả** | Hút ngay lúc bật nguồn, nhả khi `open`: logic ngược, chuyển jumper sang H hoặc đặt `RELAY_ACTIVE_LEVEL LOW`. Không hút: kiểm tra VCC 5 V, GND chung, dây IN (chân 21) |
| 5 | WiFi + backend | `status` báo `HTTP 200`. Web ▸ Tổng quan thấy thiết bị **Online** | `-1`: sai IP, backend chưa chạy, tường lửa chặn 8080, WiFi cách ly thiết bị. `401`: sai `DEVICE_TOKEN` |
| 6 | **AI đọc camera** | Chạy AI với luồng của board (bên dưới); `http://localhost:5050/api/ai/health` có `"camera_ok": true` | `false`: sai IP, máy AI không tới được board, hoặc luồng đang bị mở bởi một chương trình khác (mỗi lần chỉ một người xem luồng cổng 81) |
| 7 | Luồng thẻ | Trên web đăng ký người dùng với **mã thẻ đúng UID** vừa đọc ở bước 3 (đăng ký mặt xong), quẹt thẻ: OLED hiện "QUET MAT" kèm hướng dẫn | "The la": UID trên web khác UID đọc được. "AI khong san sang": AI Service chưa chạy hoặc `LAB_AI_BASE_URL` sai |
| 8 | Mở cửa | Làm đúng thử thách: OLED "MO CUA", relay hút, web ghi "Cho vào" | Xem nhật ký trên web để biết lý do từ chối |
| 9 | Nối khóa | Lặp bước 8: khóa nhả chốt rồi tự khóa lại sau 5 giây. Thử nút "Mở cửa" trên web (lý do ≥ 5 ký tự) | Khóa không nhả: kiểm tra điện áp khóa, dây COM/NO. Board tự reset khi khóa nhả: thiếu diode, hoặc nguồn yếu (thêm tụ 470–1000 µF gần khóa và gần board) |

**Cho AI dùng camera của board** (thay IP bằng IP in ra ở log):

```powershell
cd ai_service
.\start_ai.ps1 -Camera "http://192.168.1.57:81/stream"
```

Nếu AI chạy trong Docker: đặt `AI_CAMERA=http://192.168.1.57:81/stream` trong file `.env`. Nếu bạn đặt `CAM_STREAM_KEY`, thêm `?key=<chuỗi>` vào cuối địa chỉ.

**Chỉnh hình cho AI nhận diện tốt:**
- AI cần mặt rộng **≥ 120 px** trong khung hình. Mặc định phát 800×600 (SVGA): đứng cách camera khoảng 0,5–1 m. Muốn mượt hơn đặt `CAM_FRAMESIZE FRAMESIZE_VGA`, nhưng mặt sẽ nhỏ hơn.
- Camera OV5640 rẻ thường **lấy nét bằng cách xoay ống kính** (không tự lấy nét): vừa xem `http://<ip>/capture` vừa xoay nhẹ cho nét ở khoảng 0,5–1 m.
- Gắn ngang tầm mặt, đủ sáng, không ngược sáng. Hình **không được soi gương** (AI hiểu "quay trái / phải" theo hình không soi gương).
- Tốc độ khung hình thực tế chưa đo; thử thách chớp mắt cần từ vài khung hình/giây trở lên. Màn hình AI hiển thị fps ở góc.
- Ngưỡng nhận diện / chống giả đã hiệu chỉnh cho webcam laptop; với camera này nên đo lại bằng `ai_service/tools/eval_far_frr.py`.
- **Đã đo trên board của bạn (21/10):** camera chạy 11 khung/giây ở `CAM_XCLK_MHZ 10` (20 MHz cho 22 khung/giây nhưng làm WiFi chập chờn: ping tới board 424 ms khi camera chụp liên tục, 117 ms khi tắt, còn ~54 ms với cấu hình hiện tại). Qua WiFi luồng đạt khoảng 7–8 khung/giây, bị giới hạn bởi độ trễ WiFi (mỗi vòng ping ESP32 chỉ gửi được khoảng 5,7 KB). Muốn nhanh hơn: nối máy chạy AI bằng **dây mạng hoặc WiFi 5 GHz** (máy và board cùng 2,4 GHz làm ping cao gấp đôi), hoặc tăng `CAM_JPEG_QUALITY` (số lớn = khung nhẹ hơn). Hai lỗi đã sửa: gói nhỏ bị chờ ACK trễ của Windows (bật `TCP_NODELAY`, gộp tiêu đề vào một gói: luồng từ 3,6 lên 7,3 khung/giây) và camera chụp liên tục kể cả lúc rảnh (`CAM_GRAB_LATEST 0`: chỉ chụp khi có người lấy hình).

## 7. Dùng lâu dài

- **Cấp nguồn:** sau khi chạy ổn, cấp 5 V ổn định (≥ 1 A) từ mạch hạ áp vào chân 5V của board và **rút cáp USB**, không cắm cả hai cùng lúc.
- **Nhiệt:** board vừa phát hình vừa chạy WiFi sẽ nóng lên. Đừng bịt kín, nên có thông gió.
- **Bảo mật luồng hình:** mặc định ai cùng mạng WiFi cũng xem được `http://<ip>/`. Đặt `CAM_STREAM_KEY` để chỉ người có khóa mới xem; hình khuôn mặt là dữ liệu nhạy cảm.
- **Mất WiFi:** firmware tự thử nối lại mỗi 10 giây. Lúc mất mạng cửa **không mở bằng thẻ** (quyết định nằm ở backend); nút mở cửa phía trong vẫn dùng được.
- **Đổi mật khẩu / token mặc định** ở cả backend và `config_local.h`.
- Tắt lệnh thử qua Serial khi triển khai thật (`ENABLE_SERIAL_TEST 0`): lệnh `open` mở được cửa cho bất kỳ ai cắm cáp USB vào board.

## 8. Các lỗi thường gặp

| Hiện tượng | Nguyên nhân thường gặp |
|---|---|
| OLED hiện "CHUA CAU HINH" | Chưa tạo `config_local.h` hoặc chưa đổi `WIFI_SSID` |
| `[WIFI] That bai` | Sai tên / mật khẩu, WiFi 5 GHz, board ở xa router |
| Quẹt thẻ báo "LOI MANG" / "LOI TOKEN" | Mất WiFi hoặc không tới được backend / `DEVICE_TOKEN` khác `LAB_DEVICE_TOKEN` |
| Quét mặt xong cửa không mở, web không có log | AI không gửi được kết quả về backend (xem `HUONG_DAN_CHAY.md`, mục D) |
| Board reset khi phát hình hoặc khi relay hút | Nguồn yếu (dùng nguồn 5 V ≥ 1 A), thiếu diode chống xung, dây chạm nhau trên breadboard |
| Hình giật / trễ nhiều | WiFi yếu; giảm `CAM_FRAMESIZE`, tăng `CAM_JPEG_QUALITY` (số lớn hơn = nhẹ hơn) |
| Cửa mở sai thời điểm khi vừa cấp nguồn | Logic relay ngược (mục 6 bước 4) |

Mã nguồn: `firmware/src/main.cpp` (logic cửa), `firmware/src/camera_stream.cpp` (camera + luồng hình), `firmware/include/config.h` (mọi cấu hình và chân), `firmware/platformio.ini` (môi trường `esp32s3cam`, `esp32s3cam_usb`).
