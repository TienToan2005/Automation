// Copy file này thành config_local.h (cùng thư mục) rồi sửa. config_local.h không bị đưa lên git.
#pragma once

#define WIFI_SSID "TEN_WIFI_2.4GHZ_CUA_BAN"
#define WIFI_PASS "MAT_KHAU_WIFI"
#define BACKEND_URL "http://192.168.1.10:8080"   // IP máy tính chạy backend (ipconfig)
#define DEVICE_TOKEN "dev-device-token"          // trùng LAB_DEVICE_TOKEN của backend

// Các tuỳ chọn khác (bỏ comment nếu cần):
// #define RELAY_ACTIVE_LEVEL LOW      // module relay để jumper ở L
// #define USE_RC522 0                 // chưa có đầu đọc thẻ: thử bằng lệnh Serial "uid A123"
// #define UNLOCK_MS 3000
