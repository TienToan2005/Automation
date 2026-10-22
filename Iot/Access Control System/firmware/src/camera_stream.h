#pragma once
#include <Arduino.h>

// Camera OV5640 trên board ESP32-S3: khởi tạo và phát hình MJPEG cho AI Service.
//   http://<ip>:81/stream   luồng MJPEG liên tục (AI Service đọc luồng này)
//   http://<ip>/            trang xem thử trong trình duyệt
//   http://<ip>/capture     một ảnh JPEG (để kiểm tra nét, hướng, ánh sáng)
// Nếu đặt CAM_STREAM_KEY thì mọi địa chỉ phải kèm ?key=<chuỗi>.

bool cameraBegin();          // gọi một lần ở setup(); false nếu camera không khởi tạo được
void cameraEnd();            // tắt hẳn camera (lệnh thử "cam off": đo xem camera có làm WiFi chậm không)
void cameraSetXclk(int mhz); // đổi xung cấp cho cảm biến (có hiệu lực ở lần cameraBegin() kế tiếp)
bool cameraSetSize(const String &name);   // qvga | vga | svga | xga | hd (không cần khởi tạo lại)
bool cameraSetFlip(bool hmirror, bool vflip);  // lật hình tức thì (camera lắp ngược: bật cả hai = xoay 180 độ)
bool cameraSetQuality(int q);             // 4..63, số lớn = nén mạnh = nhẹ
void cameraBench(unsigned ms);            // lấy hình liên tục trong ms mili giây, in fps (không qua WiFi)
bool cameraStreamStart();    // gọi khi WiFi đã có IP; false nếu máy chủ không chạy được
bool cameraStreamRunning();
