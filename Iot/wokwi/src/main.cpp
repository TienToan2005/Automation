#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Keypad.h>

// Kiem soat ra vao 2 lop: quet the -> quet mat -> mo khoa. ESP32 chi giao tiep voi BACKEND
// (Iot/backend, FastAPI cong 8080); backend moi la noi goi AI va quyet dinh mo cua.
//   1. Nhap ma the (toi da 12 ky tu, # xac nhan) -> POST /api/v1/access/card-scan
//        202 pending  -> chuyen sang quet mat (backend da kich hoat AI)
//        403 forbidden -> hien ly do
//   2. Poll GET /api/v1/access/session/<id> moi 500 ms: granted | denied | timeout | pending(+goi y)
//   3. Heartbeat POST /api/v1/device/heartbeat moi 3 s: bao trang thai, nhan lenh tu web
//        (open_lock = mo cua tu xa, reset_lockout = go khoa tam)
// Wokwi khong co dau doc RFID: ma the nhap bang ban phim, backend coi do la UID cua the.
#define PIN_SDA 3
#define PIN_SCL 8
#define PIN_RELAY 13
#define PIN_EXIT 9        // nut mo cua phia trong
#define PIN_PIR 47        // phat hien co nguoi

// Wokwi for VS Code: host.wokwi.internal tro toi may tinh dang chay backend.
#define WIFI_SSID "Wokwi-GUEST"
#define WIFI_PASS ""
#define BACKEND_URL "http://host.wokwi.internal:8080"
#define DEVICE_ID "door-esp32-01"
#define DEVICE_TOKEN "dev-device-token"   // phai trung LAB_DEVICE_TOKEN cua backend

const uint8_t MAX_TRIES = 3;
const unsigned long UNLOCK_MS = 8000;         // mo cua sau khi xac thuc xong
const unsigned long LOCKOUT_MS = 15000;
const unsigned long FACE_TIMEOUT_MS = 17000;  // AI timeout 16s + du phong mang
const unsigned long POLL_MS = 500;
const unsigned long HEARTBEAT_MS = 3000;

Adafruit_SSD1306 oled(128, 64, &Wire, -1);

// Ban phim 4x4 dung de nhap ma the (toi da 12 ky tu, chi gom 0-9 va A-D), # = xac nhan, * = xoa
const byte ROWS = 4, COLS = 4;
char keys[ROWS][COLS] = {
  {'1', '2', '3', 'A'},
  {'4', '5', '6', 'B'},
  {'7', '8', '9', 'C'},
  {'*', '0', '#', 'D'}};
byte rowPins[ROWS] = {1, 2, 42, 41};
byte colPins[COLS] = {40, 39, 38, 37};
Keypad keypad(makeKeymap(keys), rowPins, colPins, ROWS, COLS);

const uint8_t CARD_LEN = 12;   // toi da 12 ky tu (OLED hien duoc "Ma the: " + 12 ky tu)
String cardInput = "";

enum State { WAIT_CARD, WAIT_FACE, UNLOCKED, LOCKOUT };
State state = WAIT_CARD;
const char *STATE_NAMES[] = {"WAIT_CARD", "WAIT_FACE", "UNLOCKED", "LOCKOUT"};
uint8_t tries = 0;
unsigned long stateSince = 0;
unsigned long unlockMs = UNLOCK_MS;

// Phien quet mat hien tai (do backend cap)
String sessionId = "";
String studentName = "";
String hintCode = "";         // no_face | move_closer | multiple_faces (tu AI, qua backend)
String challengeType = "";    // turn_left | turn_right | blink (rong = chua toi buoc thach thuc)
String challengeText = "";
unsigned long lastPoll = 0;
unsigned long lastHeartbeat = 0;

bool wifiOk() { return WiFi.status() == WL_CONNECTED; }

// ---------------- HTTP toi backend ----------------
// Tra ve ma HTTP (>0) hoac -1 neu mat mang/loi ket noi.
int httpCall(bool post, const String &path, const String &body, String &out, int timeoutMs = 2000) {
  if (!wifiOk()) return -1;
  HTTPClient http;
  http.setTimeout(timeoutMs);
  http.begin(String(BACKEND_URL) + path);
  http.addHeader("X-Device-Token", DEVICE_TOKEN);
  http.addHeader("Content-Type", "application/json");
  int code = post ? http.POST(body) : http.GET();
  out = code > 0 ? http.getString() : "";
  http.end();
  return code;
}

const char *denyText(const char *reason) {
  if (!strcmp(reason, "unknown_card")) return "The la";
  if (!strcmp(reason, "card_revoked")) return "The bi thu hoi";
  if (!strcmp(reason, "account_suspended")) return "Tai khoan bi khoa";
  if (!strcmp(reason, "not_enrolled")) return "Chua dang ky mat";
  if (!strcmp(reason, "ai_unavailable")) return "AI khong san sang";
  if (!strcmp(reason, "face_mismatch")) return "Sai khuon mat";
  if (!strcmp(reason, "spoof_pad")) return "Phat hien gia mao";
  if (!strcmp(reason, "challenge_failed")) return "Khong qua thu thach";
  return "Bi tu choi";
}

// ---------------- OLED ----------------
void drawScreen(const char *l1, const char *l2 = "", const char *l3 = "") {
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);
  oled.setTextSize(1);
  oled.setCursor(0, 0);
  oled.print("KIEM SOAT RA VAO");
  oled.setCursor(104, 0);
  oled.print(wifiOk() ? "WiFi" : "----");
  oled.drawLine(0, 10, 127, 10, SSD1306_WHITE);
  oled.setTextSize(2);
  oled.setCursor(0, 16);
  oled.print(l1);
  oled.setTextSize(1);
  oled.setCursor(0, 40);
  oled.print(l2);
  oled.setCursor(0, 52);
  oled.print(l3);
  oled.display();
}

unsigned long faceSecondsLeft() {
  unsigned long elapsed = millis() - stateSince;
  return elapsed >= FACE_TIMEOUT_MS ? 0 : (FACE_TIMEOUT_MS - elapsed) / 1000 + 1;
}

void showState() {
  char buf[24];
  snprintf(buf, sizeof(buf), "Sai: %u/%u", tries, MAX_TRIES);
  if (state == WAIT_CARD) {
    String l2 = "Ma the: " + cardInput;
    drawScreen("QUET THE", l2.c_str(), buf);
  } else if (state == WAIT_FACE) {
    // Day la man hinh DUY NHAT nguoi dung nhin thay khi dung truoc cua, nen dong chu TO la viec can lam ngay.
    // "Trai/phai" tinh theo ben cua chinh nguoi dung (khong phai ben cua camera).
    // Uu tien: van de can sua (khong thay mat, qua xa...) > thach thuc cua AI > nhac nhin camera.
    const char *big = "NHIN VAO";
    String l2 = "Nhin thang vao camera";
    if (hintCode == "move_closer") {
      big = "LAI GAN";
      l2 = "Dung gan camera hon";
    } else if (hintCode == "no_face") {
      big = "KHONG THAY";
      l2 = "Dung truoc camera";
    } else if (hintCode == "multiple_faces") {
      big = "1 NGUOI";
      l2 = "Chi 1 nguoi trong khung";
    } else if (challengeType == "turn_left") {
      big = "QUAY TRAI";
      l2 = "<< " + challengeText;
    } else if (challengeType == "turn_right") {
      big = "QUAY PHAI";
      l2 = challengeText + " >>";
    } else if (challengeType == "blink") {
      big = "CHOP MAT";
      l2 = challengeText;
    }
    char l3[24];
    snprintf(l3, sizeof(l3), "%.12s %lus", studentName.c_str(), faceSecondsLeft());
    drawScreen(big, l2.c_str(), l3);
  }
}

// ---------------- Trang thai ----------------
void cancelSession() {
  if (sessionId.length() == 0) return;
  String out;
  httpCall(true, "/api/v1/access/session/" + sessionId + "/cancel", "{}", out, 1000);
  Serial.println("[AUTH] Huy phien quet mat");
  sessionId = "";
}

void setState(State s) {
  if (state == WAIT_FACE && s != WAIT_FACE) cancelSession();   // roi buoc quet mat khi chua co ket luan
  state = s;
  stateSince = millis();
  digitalWrite(PIN_RELAY, s == UNLOCKED ? HIGH : LOW);
  if (s == UNLOCKED) {
    drawScreen("MO CUA");
    Serial.println("[LOCK] UNLOCKED");
  } else if (s == LOCKOUT) {
    drawScreen("BI KHOA", "Sai qua nhieu lan");
    Serial.println("[LOCK] LOCKOUT");
  } else {
    cardInput = "";
    hintCode = "";
    challengeType = "";
    challengeText = "";
    showState();
    Serial.println(s == WAIT_CARD ? "[LOCK] Cho quet the" : "[LOCK] Cho quet mat");
  }
}

// Mot lan xac thuc that bai: tinh vao so lan sai chung.
void fail(const char *why) {
  tries++;
  Serial.printf("[AUTH] %s (%u/%u)\n", why, tries, MAX_TRIES);
  if (tries >= MAX_TRIES) {
    tries = 0;
    setState(LOCKOUT);
    return;
  }
  drawScreen("TU CHOI", why);
  delay(1500);
  setState(WAIT_CARD);
}

void unlock(unsigned long ms) {
  unlockMs = ms;
  setState(UNLOCKED);
}

// ---------------- Luong nghiep vu ----------------
void submitCard(const String &uid) {
  drawScreen("DANG DOC", "Gui backend...");
  JsonDocument req;
  req["device_id"] = DEVICE_ID;
  req["uid"] = uid;
  String body, resp;
  serializeJson(req, body);
  int code = httpCall(true, "/api/v1/access/card-scan", body, resp, 5000);
  Serial.printf("[CARD] uid=%s -> HTTP %d\n", uid.c_str(), code);

  if (code <= 0) {                       // mat mang: khong tinh la sai the
    drawScreen("LOI MANG", "Khong toi backend");
    delay(1500);
    setState(WAIT_CARD);
    return;
  }
  if (code == 401) {
    drawScreen("LOI TOKEN", "Sai device token");
    delay(1500);
    setState(WAIT_CARD);
    return;
  }

  JsonDocument doc;
  if (deserializeJson(doc, resp)) {
    fail("Loi du lieu");
    return;
  }
  if (code == 202) {
    sessionId = doc["data"]["session_id"] | "";
    studentName = doc["data"]["student_name"] | "";
    hintCode = "";
    challengeType = "";
    challengeText = "";
    lastPoll = millis();
    Serial.printf("[CARD] Hop le: %s, phien %s\n", studentName.c_str(), sessionId.c_str());
    setState(WAIT_FACE);
    return;
  }
  fail(denyText(doc["data"]["reason"] | ""));
}

// Hoi backend ket qua phien quet mat; cap nhat goi y len OLED.
void pollSession() {
  String resp;
  int code = httpCall(false, "/api/v1/access/session/" + sessionId, "", resp, 1500);
  if (code != 200) return;                // loi tam thoi: thu lai o lan sau, FACE_TIMEOUT_MS se chan
  JsonDocument doc;
  if (deserializeJson(doc, resp)) return;

  const char *status = doc["status"] | "";
  if (!strcmp(status, "granted")) {
    sessionId = "";                       // da co ket luan, khong can huy
    Serial.println("[AUTH] Mat hop le -> mo cua");
    tries = 0;
    unlock(UNLOCK_MS);
  } else if (!strcmp(status, "denied")) {
    sessionId = "";
    fail(denyText(doc["reason"] | ""));
  } else if (!strcmp(status, "timeout") || !strcmp(status, "cancelled")) {
    sessionId = "";
    Serial.println("[AUTH] Het thoi gian quet mat");
    setState(WAIT_CARD);
  } else {
    hintCode = String((const char *)(doc["hint"] | ""));
    challengeType = String((const char *)(doc["challenge_type"] | ""));
    challengeText = String((const char *)(doc["challenge_text"] | ""));
    showState();
  }
}

// Gui trang thai len backend va thuc hien lenh nhan ve tu web.
void sendHeartbeat() {
  JsonDocument req;
  req["device_id"] = DEVICE_ID;
  req["state"] = STATE_NAMES[state];
  req["relay"] = state == UNLOCKED ? 1 : 0;
  req["rssi"] = WiFi.RSSI();
  req["uptime_s"] = millis() / 1000;
  req["ip"] = WiFi.localIP().toString();
  req["tries"] = tries;
  String body, resp;
  serializeJson(req, body);
  if (httpCall(true, "/api/v1/device/heartbeat", body, resp, 1500) != 200) return;

  JsonDocument doc;
  if (deserializeJson(doc, resp)) return;
  for (JsonObject cmd : doc["commands"].as<JsonArray>()) {
    const char *c = cmd["command"] | "";
    if (!strcmp(c, "open_lock")) {
      unsigned long ms = cmd["duration_ms"] | 5000;
      Serial.printf("[CMD] Mo cua tu xa %lu ms: %s\n", ms, (const char *)(cmd["reason"] | ""));
      unlock(ms);
    } else if (!strcmp(c, "reset_lockout")) {
      Serial.println("[CMD] Go khoa tam");
      tries = 0;
      if (state == LOCKOUT) setState(WAIT_CARD);
    }
  }
}

void connectWifi() {
  drawScreen("WIFI", "Dang ket noi...", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASS, 6);
  unsigned long t0 = millis();
  while (!wifiOk() && millis() - t0 < 10000) delay(250);
  if (wifiOk())
    Serial.printf("[WIFI] OK, IP %s\n", WiFi.localIP().toString().c_str());
  else
    Serial.println("[WIFI] That bai -> khong the xac thuc (can mang)");
}

bool pressed(uint8_t pin) { return digitalRead(pin) == LOW; }

void setup() {
  Serial.begin(115200);
  pinMode(PIN_RELAY, OUTPUT);
  pinMode(PIN_PIR, INPUT);
  pinMode(PIN_EXIT, INPUT_PULLUP);
  Wire.begin(PIN_SDA, PIN_SCL);
  if (!oled.begin(SSD1306_SWITCHCAPVCC, 0x3C)) Serial.println("OLED khong tim thay");
  connectWifi();
  setState(WAIT_CARD);
  sendHeartbeat();
}

void loop() {
  unsigned long now = millis();

  if (now - lastHeartbeat >= HEARTBEAT_MS) {
    lastHeartbeat = now;
    sendHeartbeat();
  }

  if (state == UNLOCKED && now - stateSince >= unlockMs) setState(WAIT_CARD);

  if (state == LOCKOUT) {
    if (now - stateSince >= LOCKOUT_MS) {
      setState(WAIT_CARD);
    } else {
      char buf[24];
      snprintf(buf, sizeof(buf), "Cho %lus", (LOCKOUT_MS - (now - stateSince)) / 1000 + 1);
      drawScreen("BI KHOA", buf);
    }
    return;
  }

  if (state == WAIT_CARD || state == WAIT_FACE) {
    static bool lastPir = false;
    bool pir = digitalRead(PIN_PIR);
    if (pir != lastPir) {
      lastPir = pir;
      Serial.println(pir ? "[PIR] Phat hien nguoi" : "[PIR] Het nguoi");
    }

    if (pressed(PIN_EXIT)) {
      Serial.println("[EXIT] Nut mo cua trong");
      String out;
      httpCall(true, "/api/v1/access/exit", String("{\"device_id\":\"") + DEVICE_ID + "\"}", out, 1000);
      unlock(UNLOCK_MS);
      return;
    }
  }

  if (state == WAIT_CARD) {
    char k = keypad.getKey();
    if (!k) return;
    if (k == '*') {
      cardInput = "";
    } else if (k == '#') {
      if (cardInput.length() == 0) return;
      String uid = cardInput;
      submitCard(uid);
      return;
    } else if (cardInput.length() < CARD_LEN) {
      cardInput += k;
    }
    showState();
  } else if (state == WAIT_FACE) {
    if (now - stateSince >= FACE_TIMEOUT_MS) {
      Serial.println("[AUTH] Het thoi gian quet mat");
      setState(WAIT_CARD);
      return;
    }
    if (now - lastPoll >= POLL_MS) {
      lastPoll = now;
      pollSession();
    }
  }
}
