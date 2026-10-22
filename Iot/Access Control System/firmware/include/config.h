#pragma once
// =====================================================================================
//  CẤU HÌNH FIRMWARE MẠCH THẬT (board ESP32-S3 N16R8 + camera OV5640 + RC522 + OLED + relay).
//  Chỉ cần sửa file này, hoặc tạo config_local.h (xem bên dưới) để mật khẩu WiFi không lên git.
// =====================================================================================

#if __has_include("config_local.h")
#include "config_local.h"
#endif

// ---------- 1. WiFi + Backend (BẮT BUỘC đặt đúng) ----------
#ifndef WIFI_SSID
#define WIFI_SSID "TEN_WIFI_2.4GHZ"          // ESP32 CHỈ bắt được WiFi 2.4 GHz, không bắt được 5 GHz
#endif
#ifndef WIFI_PASS
#define WIFI_PASS "MAT_KHAU_WIFI"
#endif
#ifndef BACKEND_URL
#define BACKEND_URL "http://192.168.1.10:8080"   // IP máy chạy backend (gõ ipconfig), KHÔNG dùng localhost
#endif
#ifndef DEVICE_ID
#define DEVICE_ID "door-esp32-01"            // phải trùng LAB_DEFAULT_DEVICE_ID của backend nếu muốn mở cửa từ web
#endif
#ifndef DEVICE_TOKEN
#define DEVICE_TOKEN "dev-device-token"      // phải trùng LAB_DEVICE_TOKEN của backend
#endif

// ---------- 2. Linh kiện nào có trong mạch ----------
#ifndef USE_CAMERA
#define USE_CAMERA 1         // camera OV5640 trên board, phát hình cho AI qua http://<ip>:81/stream
#endif
#ifndef USE_RC522
#define USE_RC522 1          // đầu đọc thẻ RC522 (SPI). Không có thì đặt 0 và thử bằng lệnh Serial "uid ..."
#endif
#ifndef USE_OLED
#define USE_OLED 1           // OLED 0.96" SSD1306 I2C (địa chỉ 0x3C)
#endif
#ifndef USE_EXIT_BUTTON
#define USE_EXIT_BUTTON 1    // nút mở cửa phía trong (một đầu nối chân, một đầu nối GND). Không nối thì chân tự kéo lên, không sao
#endif
#ifndef USE_PIR
#define USE_PIR 0            // cảm biến PIR (hiện chỉ ghi log "có người")
#endif

// ---------- 3. Relay + khóa ----------
// Module relay có jumper "H/L" (high/low level trigger):
//   jumper ở H  -> relay hút khi chân IN = HIGH  -> đặt RELAY_ACTIVE_LEVEL HIGH
//   jumper ở L  -> relay hút khi chân IN = LOW   -> đặt RELAY_ACTIVE_LEVEL LOW
// Nếu vừa cấp điện mà relay "tách" (hút) ngay thì logic đang ngược: đổi giá trị này.
#ifndef RELAY_ACTIVE_LEVEL
#define RELAY_ACTIVE_LEVEL HIGH
#endif
#ifndef UNLOCK_MS
#define UNLOCK_MS 5000       // thời gian nhả khóa sau khi xác thực xong. Khóa chốt điện từ rẻ KHÔNG chịu được cấp điện lâu
#endif
#define MAX_UNLOCK_MS 15000  // trần cứng: dù backend ra lệnh bao lâu cũng không giữ quá mức này (bảo vệ cuộn dây)

// ---------- 4. Chân nối cho board ESP32-S3 N16R8 có camera ----------
// KHÔNG ĐƯỢC dùng các chân sau cho việc khác:
//   GPIO 4-13 và 15-18 : cổng camera (xem mục 5)
//   GPIO 35, 36, 37    : PSRAM octal (N16R8)         GPIO 26-32: flash
//   GPIO 19, 20        : USB gốc                     GPIO 43, 44: UART (cổng "UART" nạp/log)
//   GPIO 0, 3, 45, 46  : chân khởi động (strapping)  GPIO 2, 48: đèn LED trên board
// Các chân dưới đây KHỚP DÂY THẬT: OLED SDA=1 / SCL=14, relay IN=21 (kích HIGH), nút=47, RC522 SDA/SCK/MOSI/MISO=42/39/40/41.
#define PIN_SDA 1            // OLED
#define PIN_SCL 14
#define PIN_RELAY 21         // IN của module relay, kích mức HIGH (jumper H)
#define PIN_EXIT 47          // nút mở cửa phía trong: INPUT_PULLUP, một đầu nối chân, một đầu nối GND
#define PIN_PIR 2            // PIR (nếu USE_PIR 1)
// RC522 (SPI), cấp 3,3 V
#define PIN_RC522_SS   42    // chân "SDA" trên module RC522
#define PIN_RC522_SCK  39
#define PIN_RC522_MOSI 40
#define PIN_RC522_MISO 41
#define PIN_RC522_RST  -1    // -1 = chân RST của RC522 đã nối cố định vào 3V3, không dùng chân của board (MFRC522 khởi tạo với UNUSED_PIN)

// ---------- 5. Camera OV5640 (bản đồ chân của ESP32-S3-EYE / Freenove: phổ biến nhất cho loại board này) ----------
// Nếu camera báo lỗi "probe failed" / không có hình, bản đồ chân của board bạn khác: hỏi người bán bảng chân camera rồi sửa ở đây
// (và nhớ đổi lại chân RC522/relay/OLED nếu trùng).
#define CAM_PIN_PWDN  -1
#define CAM_PIN_RESET -1
#define CAM_PIN_XCLK  15
#define CAM_PIN_SIOD  4
#define CAM_PIN_SIOC  5
#define CAM_PIN_D0    11     // Y2
#define CAM_PIN_D1    9      // Y3
#define CAM_PIN_D2    8      // Y4
#define CAM_PIN_D3    10     // Y5
#define CAM_PIN_D4    12     // Y6
#define CAM_PIN_D5    18     // Y7
#define CAM_PIN_D6    17     // Y8
#define CAM_PIN_D7    16     // Y9
#define CAM_PIN_VSYNC 6
#define CAM_PIN_HREF  7
#define CAM_PIN_PCLK  13

#ifndef CAM_XCLK_MHZ
#define CAM_XCLK_MHZ 10                // xung cấp cho cảm biến: 20 MHz làm WiFi chập chờn trên nhiều board (đo thật: ping 424 ms khi camera chạy, 117 ms khi tắt)
#endif
#ifndef CAM_GRAB_LATEST
#define CAM_GRAB_LATEST 0              // 1 = camera chụp liên tục kể cả khi không ai xem (hình luôn mới nhưng tốn băng thông bộ nhớ, làm WiFi chậm); 0 = chỉ chụp khi có người lấy hình
#endif
#ifndef CAM_FRAMESIZE
#define CAM_FRAMESIZE FRAMESIZE_VGA    // 640x480 (800x600 nặng hơn ~1,5 lần, WiFi 2.4 GHz của board không kịp). VGA (640x480) nhanh hơn nhưng mặt nhỏ hơn: AI cần mặt rộng >= 120 px
#endif
#ifndef CAM_JPEG_QUALITY
#define CAM_JPEG_QUALITY 18            // 10 (nét, nặng) .. 63 (mờ, nhẹ). Khung nhẹ thì luồng qua WiFi nhanh hơn (mỗi vòng ping ESP32 chỉ gửi được ~5,7 KB)
#endif
#ifndef CAM_HMIRROR
#define CAM_HMIRROR 0                  // 1 nếu hình bị soi gương (chữ bị ngược). AI cần hình KHÔNG soi gương
#endif
#ifndef CAM_VFLIP
#define CAM_VFLIP 1                    // 1 nếu hình bị lộn ngược (do lắp camera ngược). Đã đo trên board này: cần 1, hình mới thẳng đứng
#endif
#ifndef CAM_STREAM_KEY
#define CAM_STREAM_KEY ""              // đặt chuỗi bất kỳ để chỉ ai có ?key=<chuỗi> mới xem được hình (hình khuôn mặt là dữ liệu nhạy cảm)
#endif
#define CAM_STREAM_PORT 81             // AI đọc http://<ip>:81/stream ; trang xem thử http://<ip>/ ; ảnh tĩnh http://<ip>/capture

// ---------- 6. Thời gian ----------
#define MAX_TRIES 3                   // sai quá số lần này thì khóa tạm
#define LOCKOUT_MS 15000
#define FACE_TIMEOUT_MS 31000         // phải lớn hơn AI_SCAN_TIMEOUT_S (30 giây) của AI Service
#define POLL_MS 500                  // chu kỳ hỏi backend kết quả quét mặt để cập nhật OLED (nhỏ hơn = OLED theo kịp cửa sổ AI hơn)
#define HEARTBEAT_MS 3000
#define CARD_REPEAT_MS 1500           // bỏ qua thẻ vừa quẹt trong khoảng này (chống đọc lặp)
#define WIFI_RETRY_MS 10000
#define ENABLE_SERIAL_TEST 1          // cho phép lệnh thử qua Serial (uid / open / status / i2c / help)
