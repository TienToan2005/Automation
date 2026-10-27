#include "camera_stream.h"
#include "config.h"

#if USE_CAMERA
#include "esp_camera.h"
#include "esp_http_server.h"
#include "lwip/sockets.h"

static httpd_handle_t streamServer = nullptr;   // cổng 81: chỉ phục vụ /stream (một kết nối giữ máy chủ bận)
static httpd_handle_t pageServer = nullptr;     // cổng 80: trang xem thử + /capture, vẫn dùng được khi AI đang xem luồng

#define STR_(x) #x
#define STR(x) STR_(x)
#define PART_BOUNDARY "frame_lab_access_boundary"
static const char *STREAM_CONTENT_TYPE = "multipart/x-mixed-replace;boundary=" PART_BOUNDARY;
static const char *STREAM_BOUNDARY = "\r\n--" PART_BOUNDARY "\r\n";
#define STREAM_PART "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n"
static int g_xclkMhz = CAM_XCLK_MHZ;

bool cameraBegin() {
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = CAM_PIN_D0;
  c.pin_d1 = CAM_PIN_D1;
  c.pin_d2 = CAM_PIN_D2;
  c.pin_d3 = CAM_PIN_D3;
  c.pin_d4 = CAM_PIN_D4;
  c.pin_d5 = CAM_PIN_D5;
  c.pin_d6 = CAM_PIN_D6;
  c.pin_d7 = CAM_PIN_D7;
  c.pin_xclk = CAM_PIN_XCLK;
  c.pin_pclk = CAM_PIN_PCLK;
  c.pin_vsync = CAM_PIN_VSYNC;
  c.pin_href = CAM_PIN_HREF;
  c.pin_sccb_sda = CAM_PIN_SIOD;
  c.pin_sccb_scl = CAM_PIN_SIOC;
  c.pin_pwdn = CAM_PIN_PWDN;
  c.pin_reset = CAM_PIN_RESET;
  c.xclk_freq_hz = g_xclkMhz * 1000000;
  c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size = CAM_FRAMESIZE;
  c.jpeg_quality = CAM_JPEG_QUALITY;
  c.fb_count = 2;
  c.fb_location = CAMERA_FB_IN_PSRAM;
#if CAM_GRAB_LATEST
  c.grab_mode = CAMERA_GRAB_LATEST;           // luôn lấy khung MỚI NHẤT nhưng camera chụp liên tục kể cả lúc rảnh
#else
  c.grab_mode = CAMERA_GRAB_WHEN_EMPTY;       // chỉ chụp khi có người lấy hình: lúc rảnh camera nghỉ, WiFi không bị chậm
#endif

  if (!psramFound()) {                         // không có PSRAM (sai memory_type?): hạ cấu hình để còn chạy được
    Serial.println("[CAM] KHONG thay PSRAM: kiem tra board_build.arduino.memory_type trong platformio.ini. Ha xuong QVGA.");
    c.frame_size = FRAMESIZE_QVGA;
    c.fb_count = 1;
    c.fb_location = CAMERA_FB_IN_DRAM;
  }

  esp_err_t err = esp_camera_init(&c);
  if (err != ESP_OK) {
    Serial.printf("[CAM] Khoi tao that bai 0x%x. Camera cam chua chat / ban do chan camera trong config.h khong dung board nay\n", err);
    return false;
  }
  sensor_t *s = esp_camera_sensor_get();
  if (s) {
    Serial.printf("[CAM] Sensor PID=0x%04X (%s), PSRAM %u KB\n", s->id.PID, s->id.PID == OV5640_PID ? "OV5640" : "khac", (unsigned)(ESP.getPsramSize() / 1024));
    s->set_hmirror(s, CAM_HMIRROR);
    s->set_vflip(s, CAM_VFLIP);
  }
  return true;
}

void cameraEnd() { esp_camera_deinit(); }
void cameraSetXclk(int mhz) { g_xclkMhz = mhz; }

bool cameraSetSize(const String &name) {
  sensor_t *s = esp_camera_sensor_get();
  if (!s) return false;
  framesize_t fs;
  if (name == "qvga") fs = FRAMESIZE_QVGA;
  else if (name == "vga") fs = FRAMESIZE_VGA;
  else if (name == "svga") fs = FRAMESIZE_SVGA;
  else if (name == "xga") fs = FRAMESIZE_XGA;
  else if (name == "hd") fs = FRAMESIZE_HD;
  else return false;
  return s->set_framesize(s, fs) == 0;
}

bool cameraSetFlip(bool hmirror, bool vflip) {
  sensor_t *s = esp_camera_sensor_get();
  return s && s->set_hmirror(s, hmirror) == 0 && s->set_vflip(s, vflip) == 0;
}

bool cameraSetQuality(int q) {
  sensor_t *s = esp_camera_sensor_get();
  return s && q >= 4 && q <= 63 && s->set_quality(s, q) == 0;
}

void cameraBench(unsigned ms) {
  unsigned long t0 = millis();
  int n = 0;
  size_t bytes = 0;
  while (millis() - t0 < ms) {
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) break;
    n++;
    bytes += fb->len;
    esp_camera_fb_return(fb);
  }
  float sec = (millis() - t0) / 1000.0f;
  Serial.printf("[BENCH] %d khung trong %.1f giay = %.1f fps, trung binh %u byte/khung (chua tinh WiFi)\n", n, sec, sec > 0 ? n / sec : 0, n ? (unsigned)(bytes / n) : 0);
}

static bool authorized(httpd_req_t *req) {
  if (!CAM_STREAM_KEY[0]) return true;
  char q[96], v[48];
  if (httpd_req_get_url_query_len(req) == 0 || httpd_req_get_url_query_str(req, q, sizeof(q)) != ESP_OK) return false;
  if (httpd_query_key_value(q, "key", v, sizeof(v)) != ESP_OK) return false;
  return strcmp(v, CAM_STREAM_KEY) == 0;
}

static esp_err_t denied(httpd_req_t *req) {
  httpd_resp_set_status(req, "401 Unauthorized");
  return httpd_resp_send(req, "can ?key=...", HTTPD_RESP_USE_STRLEN);
}

static esp_err_t streamHandler(httpd_req_t *req) {
  if (!authorized(req)) return denied(req);
  esp_err_t res = httpd_resp_set_type(req, STREAM_CONTENT_TYPE);
  if (res != ESP_OK) return res;
  // Tắt Nagle: nếu không, các gói nhỏ (đường biên, tiêu đề) chờ ACK trễ của Windows (~200 ms) làm luồng chỉ còn vài khung/giây
  int one = 1;
  setsockopt(httpd_req_to_sockfd(req), IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
  char head[128];
  while (true) {
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) {
      res = ESP_FAIL;
    } else {
      // đường biên + tiêu đề phần gộp thành MỘT gói, rồi tới dữ liệu JPEG
      size_t n = snprintf(head, sizeof(head), "%s" STREAM_PART, STREAM_BOUNDARY, (unsigned)fb->len);
      res = httpd_resp_send_chunk(req, head, n);
      if (res == ESP_OK) res = httpd_resp_send_chunk(req, (const char *)fb->buf, fb->len);
      esp_camera_fb_return(fb);
    }
    if (res != ESP_OK) break;                  // client (AI) ngắt kết nối hoặc mạng nghẽn quá thời gian chờ gửi
  }
  return res;
}

static esp_err_t captureHandler(httpd_req_t *req) {
  if (!authorized(req)) return denied(req);
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) {
    httpd_resp_send_500(req);
    return ESP_FAIL;
  }
  httpd_resp_set_type(req, "image/jpeg");
  httpd_resp_set_hdr(req, "Cache-Control", "no-store");
  esp_err_t res = httpd_resp_send(req, (const char *)fb->buf, fb->len);
  esp_camera_fb_return(fb);
  return res;
}

static esp_err_t pageHandler(httpd_req_t *req) {
  if (!authorized(req)) return denied(req);
  static const char html[] =
      "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
      "<title>Camera cua</title><body style='margin:0;background:#111;color:#ccc;font:14px sans-serif;text-align:center'>"
      "<p>Hinh tu camera (AI doc cong " STR(CAM_STREAM_PORT) "). Hinh KHONG duoc bi soi guong / lon nguoc.</p>"
      "<img id=s style='max-width:100%'><script>"
      "document.getElementById('s').src='http://'+location.hostname+':" STR(CAM_STREAM_PORT) "/stream'+location.search;"
      "</script></body>";
  httpd_resp_set_type(req, "text/html");
  return httpd_resp_send(req, html, HTTPD_RESP_USE_STRLEN);
}

bool cameraStreamStart() {
  if (streamServer) return true;
  httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
  cfg.lru_purge_enable = true;

  httpd_uri_t stream = {"/stream", HTTP_GET, streamHandler, nullptr};
  cfg.server_port = CAM_STREAM_PORT;
  cfg.ctrl_port = 32768;
  if (httpd_start(&streamServer, &cfg) != ESP_OK) {
    Serial.println("[CAM] Khong chay duoc may chu luong (cong 81)");
    streamServer = nullptr;
    return false;
  }
  httpd_register_uri_handler(streamServer, &stream);

  httpd_uri_t index = {"/", HTTP_GET, pageHandler, nullptr};
  httpd_uri_t capture = {"/capture", HTTP_GET, captureHandler, nullptr};
  cfg.server_port = 80;
  cfg.ctrl_port = 32769;
  if (httpd_start(&pageServer, &cfg) == ESP_OK) {
    httpd_register_uri_handler(pageServer, &index);
    httpd_register_uri_handler(pageServer, &capture);
  } else {
    pageServer = nullptr;
    Serial.println("[CAM] Khong chay duoc trang xem thu (cong 80); luong cong 81 van dung duoc");
  }
  return true;
}

bool cameraStreamRunning() { return streamServer != nullptr; }

#else  // USE_CAMERA == 0
bool cameraBegin() { return false; }
void cameraEnd() {}
void cameraSetXclk(int) {}
bool cameraSetSize(const String &) { return false; }
bool cameraSetFlip(bool, bool) { return false; }
bool cameraSetQuality(int) { return false; }
void cameraBench(unsigned) {}
bool cameraStreamStart() { return false; }
bool cameraStreamRunning() { return false; }
#endif
