#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <esp_wifi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include "config.h"
#if USE_RC522
#include <SPI.h>
#include <MFRC522.h>
#endif
#include "camera_stream.h"

// Kiem soat ra vao 2 lop (the + mat) cho MACH THAT. ESP32 chi noi chuyen voi BACKEND (FastAPI, cong 8080):
//   1. Quet the RC522 (hoac go ma qua Serial) -> POST /api/v1/access/card-scan
//        202 pending   -> sang buoc quet mat (backend da kich hoat AI)
//        403 forbidden -> hien ly do
//   2. Hoi GET /api/v1/access/session/<id> moi 500 ms: granted | denied | timeout | pending(+goi y len OLED)
//   3. Heartbeat POST /api/v1/device/heartbeat moi 3 s: bao trang thai, nhan lenh mo cua tu xa tu web
// Ngoai ra: doc the RC522, tu noi lai WiFi, relay an toan luc khoi dong, kiem tra linh kien luc bat, lenh thu qua
// Serial, tran thoi gian cap dien cho khoa, va PHAT HINH CAMERA OV5640 tren cung board (http://<ip>:81/stream)
// de AI Service doc, nen mot board lo ca cua lan camera.
#define FW_VERSION "1.0"

Adafruit_SSD1306 oled(128, 64, &Wire, -1);
bool oledOk = false;


#if USE_RC522
// PIN_RC522_RST < 0: chan RST cua module da noi thang 3V3 -> UNUSED_PIN (thu vien reset mem bang thanh ghi)
MFRC522 rfid(PIN_RC522_SS, PIN_RC522_RST < 0 ? MFRC522::UNUSED_PIN : (byte)PIN_RC522_RST);
bool rfidOk = false;
unsigned long lastRfidCheck = 0;
unsigned long lastCardAt = 0;
unsigned long lastSecurityFailAt = 0;
#endif

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
unsigned long lastWifiTry = 0;
String pendingUid = "";       // ma the dua vao tu lenh Serial "uid ..."
bool pirLast = false;
bool camOk = false;

// ---------------- Relay (an toan khi khoi dong) ----------------
#define RELAY_OFF_LEVEL (RELAY_ACTIVE_LEVEL == HIGH ? LOW : HIGH)

void relayInit() {
  digitalWrite(PIN_RELAY, RELAY_OFF_LEVEL);   // dat muc "nha" TRUOC khi bat che do output: relay khong bi hut luc cap dien
  pinMode(PIN_RELAY, OUTPUT);
}
void relaySet(bool on) { digitalWrite(PIN_RELAY, on ? RELAY_ACTIVE_LEVEL : RELAY_OFF_LEVEL); }

bool wifiOk() { return WiFi.status() == WL_CONNECTED; }
bool wifiConfigured() { return strncmp(WIFI_SSID, "TEN_WIFI", 8) != 0; }

// ---------------- HTTP toi backend ----------------
// Tra ve ma HTTP (>0) hoac -1 neu mat mang/loi ket noi.
int httpCall(bool post, const String &path, const String &body, String &out, int timeoutMs = 2000) {
  if (!wifiOk()) return -1;
  HTTPClient http;
  http.setConnectTimeout(timeoutMs);
  http.setTimeout(timeoutMs);
  http.begin(String(BACKEND_URL) + path);
  http.addHeader("X-Device-Token", DEVICE_TOKEN);
  http.addHeader("Content-Type", "application/json");
  int code = post ? http.POST(body) : http.GET();
  out = code > 0 ? http.getString() : "";
  http.end();
  return code;
}

// Thong bao ngan cho NGUOI DUNG: khong lo nguong / diem so / luat phat hien.
const char *denyText(const char *reason) {
  if (!strcmp(reason, "unknown_card")) return "The khong hop le";
  if (!strcmp(reason, "card_revoked")) return "The bi thu hoi";
  if (!strcmp(reason, "card_disabled")) return "The bi vo hieu";
  if (!strcmp(reason, "card_expired")) return "The het han";
  if (!strcmp(reason, "card_locked")) return "The tam khoa";
  if (!strcmp(reason, "rate_limited")) return "Thao tac qua nhanh";
  if (!strcmp(reason, "account_suspended")) return "Tai khoan bi khoa";
  if (!strcmp(reason, "not_enrolled")) return "Chua dang ky mat";
  if (!strcmp(reason, "ai_unavailable")) return "He thong ban, thu lai";
  if (!strcmp(reason, "face_mismatch")) return "Sai khuon mat";
  if (!strcmp(reason, "spoof_pad")) return "Khong xac thuc duoc";
  if (!strcmp(reason, "challenge_failed")) return "Khong qua thu thach";
  if (!strcmp(reason, "face_not_detected")) return "Khong thay khuon mat";
  if (!strcmp(reason, "face_quality_low")) return "Anh chua ro";
  if (!strcmp(reason, "face_inconclusive")) return "Chua xac dinh duoc";
  if (!strcmp(reason, "pad_uncertain")) return "Thu lai noi sang";
  if (!strcmp(reason, "timeout")) return "Het thoi gian";
  return "Bi tu choi";
}

// Cac lan bi tu choi TINH vao so lan sai lien tiep cua cua (du MAX_TRIES lan thi khoa tam LOCKOUT_MS):
//   - THE SAI: the la, thu hoi, vo hieu, het han;
//   - bang chung an ninh khi quet mat: sai mat, gia mao, khong qua thu thach.
// KHONG tinh: loi ky thuat (AI hong, mat mang), het gio, loi thu nhan (khong thay mat, anh kem), the dang bi khoa, quet qua nhanh.
bool countsTowardLockout(const char *reason) {
  return !strcmp(reason, "unknown_card") || !strcmp(reason, "card_revoked") || !strcmp(reason, "card_disabled") ||
         !strcmp(reason, "card_expired") || !strcmp(reason, "face_mismatch") || !strcmp(reason, "spoof_pad") ||
         !strcmp(reason, "challenge_failed");
}

// ---------------- OLED ----------------
void drawScreen(const char *l1, const char *l2 = "", const char *l3 = "") {
  if (!oledOk) return;
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
    if (!wifiConfigured()) {
      drawScreen("CHUA CAU HINH", "Sua WIFI_SSID trong", "config_local.h");
      return;
    }
#if USE_RC522
    if (!rfidOk) {
      drawScreen("RC522 LOI", "Kiem tra day SPI", buf);
      return;
    }
#endif
    drawScreen("QUET THE", "Dua the vao dau doc", buf);
  } else if (state == WAIT_FACE) {
    // Day la man hinh DUY NHAT nguoi dung nhin thay khi dung truoc cua, nen dong chu TO la viec can lam ngay.
    // "Trai/phai" tinh theo ben cua chinh nguoi dung (khong phai ben cua camera).
    const char *big = "NHIN VAO";
    String l2 = "Nhin thang vao camera";
    if (hintCode == "move_closer") {
      big = "LAI GAN";
      l2 = "Dung gan camera hon";
    } else if (hintCode == "no_face") {
      big = "KHONG THAY";
      l2 = "Dung truoc camera";
    } else if (hintCode == "quality_low") {
      big = "ANH KEM";
      l2 = "Them sang, dung gan hon";
    } else if (hintCode == "mismatch") {
      big = "SAI MAT";
      l2 = "Nhin thang, thu lai";
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
  bool relayChanged = (state == UNLOCKED) != (s == UNLOCKED);
  state = s;
  stateSince = millis();
  relaySet(s == UNLOCKED);
  if (relayChanged) lastHeartbeat = 0;     // bao backend ngay (nhip tim ke tiep) de ghi su kien cua mo / dong thuc su
  if (s == UNLOCKED) {
    drawScreen("MO CUA");
    Serial.println("[LOCK] UNLOCKED");
  } else if (s == LOCKOUT) {
    drawScreen("BI KHOA", "Sai qua nhieu lan");
    Serial.println("[LOCK] LOCKOUT");
  } else {
    hintCode = "";
    challengeType = "";
    challengeText = "";
    showState();
    Serial.println(s == WAIT_CARD ? "[LOCK] Cho quet the" : "[LOCK] Cho quet mat");
  }
}

// Mot lan bi tu choi. Chi cac ly do trong countsTowardLockout() moi tinh vao so lan sai (trong FAIL_WINDOW_MS gan day, thanh cong
// thi dem lai tu dau). Khoa tam theo TUNG THE (5/15/30 phut) do backend quan ly, bo dem nam trong DB.
void fail(const char *why, const char *reason = "") {
  Serial.printf("[AUTH] %s (%s)\n", why, reason);
  if (countsTowardLockout(reason)) {
    unsigned long now = millis();
    if (tries > 0 && now - lastSecurityFailAt > FAIL_WINDOW_MS) tries = 0;   // loi cu da qua cua so: dem lai tu dau
    lastSecurityFailAt = now;
    tries++;
    Serial.printf("[AUTH] lan sai lien tiep %u/%u\n", tries, MAX_TRIES);
    if (MAX_TRIES > 0 && tries >= MAX_TRIES) {
      tries = 0;
      setState(LOCKOUT);
      return;
    }
  }
  drawScreen("TU CHOI", why);
  delay(1500);
  setState(WAIT_CARD);
}

void unlock(unsigned long ms) {
  unlockMs = ms > MAX_UNLOCK_MS ? MAX_UNLOCK_MS : ms;   // tran cung bao ve cuon day khoa
  setState(UNLOCKED);
}

// ---------------- Luong nghiep vu ----------------
void submitCard(String uid) {
  uid.trim();
  uid.toUpperCase();
  if (uid.length() == 0) return;
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
    fail("Loi du lieu");              // loi ky thuat: khong tinh vao so lan sai
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
  const char *why = doc["data"]["reason"] | "";
  fail(denyText(why), why);
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
    const char *why = doc["reason"] | "";
    fail(denyText(why), why);
  } else if (!strcmp(status, "timeout") || !strcmp(status, "cancelled")) {
    const char *why = doc["reason"] | "";
    sessionId = "";
    Serial.printf("[AUTH] Phien quet mat ket thuc: %s (%s)\n", status, why);
    if (!strcmp(status, "timeout")) {       // noi ro vi sao het gio (khong thay mat / anh kem / chua xac dinh...), khong tinh la sai
      drawScreen("HET GIO", denyText(why));
      delay(1500);
    }
    setState(WAIT_CARD);
  } else {
    hintCode = String((const char *)(doc["hint"] | ""));
    challengeType = String((const char *)(doc["challenge_type"] | ""));
    challengeText = String((const char *)(doc["challenge_text"] | ""));
    showState();
  }
}

// Gui trang thai len backend va thuc hien lenh nhan ve tu web. Tra ve ma HTTP.
int sendHeartbeat() {
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
  int code = httpCall(true, "/api/v1/device/heartbeat", body, resp, 1500);
  if (code != 200) return code;

  JsonDocument doc;
  if (deserializeJson(doc, resp)) return code;
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
  return code;
}

// ---------------- WiFi ----------------
void wifiBegin() {
  WiFi.mode(WIFI_STA);
  WiFi.setHostname(DEVICE_ID);
  WiFi.setAutoReconnect(true);
  WiFi.persistent(false);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
}

void connectWifi() {
  if (!wifiConfigured()) {
    Serial.println("[WIFI] CHUA CAU HINH: sua WIFI_SSID / WIFI_PASS / BACKEND_URL trong include/config_local.h");
    return;
  }
  drawScreen("WIFI", "Dang ket noi...", WIFI_SSID);
  Serial.printf("[WIFI] Ket noi '%s' ", WIFI_SSID);
  wifiBegin();
  unsigned long t0 = millis();
  while (!wifiOk() && millis() - t0 < 15000) {
    delay(300);
    Serial.print('.');
  }
  Serial.println();
  if (wifiOk()) {
    WiFi.setSleep(false);                // bo che do ngu cua WiFi: giam tre khi hoi backend
    Serial.printf("[WIFI] OK, IP %s, RSSI %d dBm\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
  } else {
    Serial.println("[WIFI] That bai. Kiem tra: dung ten/mat khau? Song 2.4 GHz? (se tu thu lai moi 10 giay)");
  }
}

// ---------------- Dau doc the RC522 ----------------
#if USE_RC522
void rfidBegin() {
  SPI.begin(PIN_RC522_SCK, PIN_RC522_MISO, PIN_RC522_MOSI, PIN_RC522_SS);
  rfid.PCD_Init();
  byte v = rfid.PCD_ReadRegister(MFRC522::VersionReg);
  rfidOk = (v != 0x00 && v != 0xFF);      // 0x91/0x92 = chip that; 0x00 hoac 0xFF = khong thay chip (sai day / thieu nguon)
  Serial.printf("[RC522] VersionReg = 0x%02X -> %s\n", v, rfidOk ? "OK" : "KHONG DOC DUOC (kiem tra day SPI, nguon 3.3V, GND)");
}

// RC522 co the tu reset ben trong khi nguon 3.3V sut (camera + WiFi phat cung luc): VersionReg van doc duoc nhung cau hinh
// (TModeReg) va anten da mat nen khong doc duoc the. Chi doc VersionReg la khong du de phat hien.
bool rfidConfigOk() {
  return rfid.PCD_ReadRegister(MFRC522::TModeReg) == 0x80 && (rfid.PCD_ReadRegister(MFRC522::TxControlReg) & 0x03) == 0x03;
}

// Tra ve UID dang chu HOA (vd 04A1B2C3) hoac "" neu khong co the moi.
String rfidRead() {
  unsigned long now = millis();
  if (now - lastRfidCheck > 1000) {       // moi giay kiem tra: mat lien lac hoac mat cau hinh thi khoi dong lai chip
    lastRfidCheck = now;
    byte v = rfid.PCD_ReadRegister(MFRC522::VersionReg);
    bool ok = (v != 0x00 && v != 0xFF);
    if (ok && !rfidConfigOk()) {
      rfid.PCD_Init();
      Serial.printf("[RC522] Mat cau hinh/anten (nghi sut nguon), da khoi dong lai chip -> %s\n", rfidConfigOk() ? "OK" : "van loi");
    } else if (!ok) {
      rfid.PCD_Init();
    }
    if (ok != rfidOk) {
      rfidOk = ok;
      Serial.println(ok ? "[RC522] Da ket noi lai" : "[RC522] MAT LIEN LAC");
      showState();
    }
  }
  if (!rfidOk || now - lastCardAt < CARD_REPEAT_MS) return "";
  if (!rfid.PICC_IsNewCardPresent() || !rfid.PICC_ReadCardSerial()) return "";
  String uid;
  for (byte i = 0; i < rfid.uid.size; i++) {
    char b[3];
    snprintf(b, sizeof(b), "%02X", rfid.uid.uidByte[i]);
    uid += b;
  }
  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();
  lastCardAt = now;
  return uid;
}
#endif

// ---------------- Lenh thu qua Serial ----------------
#if ENABLE_SERIAL_TEST
void i2cScan() {
  Serial.println("[I2C] Quet bus...");
  int n = 0;
  for (uint8_t a = 1; a < 127; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) {
      Serial.printf("  thay thiet bi o 0x%02X%s\n", a, a == 0x3C || a == 0x3D ? "  (OLED)" : "");
      n++;
    }
  }
  if (!n) Serial.println("  KHONG thay thiet bi nao: kiem tra day SDA/SCL, VCC, GND cua OLED (hoac go 'i2cfind' de do chan)");
}

// Do chan cua OLED: thu moi cap (SDA, SCL) trong cac chan trong cua board, hoi dia chi 0x3C / 0x3D.
// KHONG thu cac chan dang co thiet bi khac (RC522 39-42, relay 21, nut 47, camera, USB, UART, chan khoi dong 0/45/46).
void i2cFind() {
  const int pins[] = {1, 2, 3, 14, 38, 48};
  const uint8_t addrs[] = {0x3C, 0x3D};
  const int n = sizeof(pins) / sizeof(pins[0]);
  Wire.end();                                      // tha chan 1 / 14 (Wire bat dien tro keo len trong, se doc sai)
  // Buoc 1: chan nao co dien tro keo len NGOAI (module OLED co nguon thi SDA, SCL deu co): doc muc HIGH du da bat dien tro keo xuong trong
  Serial.print("[I2CFIND] Chan co dien tro keo len ngoai: ");
  int pulled = 0;
  for (int i = 0; i < n; i++) {
    pinMode(pins[i], INPUT_PULLDOWN);
    delay(2);
    if (digitalRead(pins[i])) { Serial.printf("%d ", pins[i]); pulled++; }
  }
  Serial.println(pulled ? "" : "(khong co: OLED chua co nguon 3V3/GND hoac day SDA/SCL chua cham)");
  Serial.println("[I2CFIND] Buoc 2: thu moi cap SDA/SCL tren cac chan 1, 2, 3, 14, 38, 48 ...");
  int found = 0;
  for (int i = 0; i < n; i++) {
    for (int j = 0; j < n; j++) {
      if (i == j) continue;
      Wire.end();
      if (!Wire.begin(pins[i], pins[j], 100000)) continue;
      for (uint8_t a : addrs) {
        Wire.beginTransmission(a);
        if (Wire.endTransmission() == 0) {
          Serial.printf("[I2CFIND] THAY thiet bi 0x%02X: SDA=%d SCL=%d  -> dat PIN_SDA / PIN_SCL trong config.h\n", a, pins[i], pins[j]);
          found++;
        }
      }
    }
  }
  Wire.end();
  Wire.begin(PIN_SDA, PIN_SCL);
  if (!found) Serial.println("[I2CFIND] Khong thay OLED o cap chan nao: kiem tra VCC (3V3) va GND cua OLED, hoac day SDA/SCL dang o chan ngoai danh sach tren");
  else Serial.printf("[I2CFIND] Xong, %d ket qua. Sua config.h cho khop roi nap lai.\n", found);
}

void handleCommand(String line) {
  line.trim();
  String low = line;
  low.toLowerCase();
  if (low == "help" || low == "?") {
    Serial.println("Lenh: uid <MA>  gia lap quet the (vd uid A123) | open [ms]  thu relay (mac dinh 1500 ms)");
    Serial.println("      status  trang thai WiFi/backend | i2c  quet bus OLED | i2cfind  do chan SDA/SCL cua OLED | relay on/off  bat/tat relay tay");
    Serial.println("      cam bench | cam off/on | cam xclk <MHz> | cam size <vga|svga..> | cam q <chat luong> | cam flip <none|v|h|vh>  (do va chinh camera)");
  } else if (low.startsWith("uid ")) {
    pendingUid = line.substring(4);
  } else if (low.startsWith("open")) {
    unsigned long ms = low.length() > 5 ? low.substring(5).toInt() : 1500;
    if (ms < 100) ms = 1500;
    if (ms > 5000) ms = 5000;
    Serial.printf("[TEST] Relay hut %lu ms (neu khong nghe tieng 'tach': kiem tra RELAY_ACTIVE_LEVEL / jumper H-L / day IN)\n", ms);
    relaySet(true);
    delay(ms);
    relaySet(false);
  } else if (low == "relay on") {
    relaySet(true);
    Serial.println("[TEST] Relay BAT (nho gui 'relay off'; tu tat sau 10 giay)");
    unsigned long t0 = millis();
    while (millis() - t0 < 10000 && !(Serial.available())) delay(50);
    relaySet(false);
    Serial.println("[TEST] Relay TAT");
  } else if (low == "relay off") {
    relaySet(false);
    Serial.println("[TEST] Relay TAT");
  } else if (low == "status") {
    Serial.printf("[STATUS] v%s, chip %s, state %s, uptime %lus\n", FW_VERSION, ESP.getChipModel(), STATE_NAMES[state], millis() / 1000);
    Serial.printf("[STATUS] WiFi %s, IP %s, RSSI %d dBm, backend %s\n", wifiOk() ? "OK" : "MAT", WiFi.localIP().toString().c_str(), WiFi.RSSI(), BACKEND_URL);
    wifi_ps_type_t ps = WIFI_PS_NONE;
    esp_wifi_get_ps(&ps);
    Serial.printf("[STATUS] WiFi tiet kiem dien: %s, cong suat phat %d (x0.25 dBm)\n", ps == WIFI_PS_NONE ? "TAT (tot)" : "BAT (se tre)", (int)WiFi.getTxPower());
    Serial.printf("[STATUS] Camera %s, luong %s\n", camOk ? "OK" : "LOI/TAT", cameraStreamRunning() ? "dang chay (cong 81)" : "chua chay");
    int code = sendHeartbeat();
    Serial.printf("[STATUS] Heartbeat -> HTTP %d (%s)\n", code,
                  code == 200 ? "backend OK" : code == 401 ? "SAI DEVICE_TOKEN" : code < 0 ? "KHONG toi duoc backend (IP/tuong lua/backend chua chay)" : "loi la");
  } else if (low.startsWith("cam")) {
    String arg = low.length() > 3 ? low.substring(4) : String("");
    arg.trim();
    if (arg == "off") {
      cameraEnd();
      camOk = false;
      Serial.println("[CAMTEST] Camera TAT (go 'cam on' de bat lai)");
    } else if (arg == "on") {
      camOk = cameraBegin();
      Serial.println(camOk ? "[CAMTEST] Camera BAT" : "[CAMTEST] Camera LOI");
    } else if (arg == "bench") {
      if (camOk) cameraBench(5000); else Serial.println("[CAMTEST] Camera dang tat");
    } else if (arg.startsWith("xclk ")) {
      int mhz = arg.substring(5).toInt();
      if (mhz < 5 || mhz > 24) { Serial.println("[CAMTEST] XCLK 5..24 MHz"); }
      else {
        cameraEnd();
        cameraSetXclk(mhz);
        camOk = cameraBegin();
        Serial.printf("[CAMTEST] XCLK %d MHz -> camera %s\n", mhz, camOk ? "OK" : "LOI");
      }
    } else if (arg.startsWith("size ")) {
      Serial.printf("[CAMTEST] size %s -> %s\n", arg.substring(5).c_str(), cameraSetSize(arg.substring(5)) ? "OK" : "that bai (qvga|vga|svga|xga|hd)");
    } else if (arg.startsWith("flip ")) {
      String m = arg.substring(5);
      bool h = m.indexOf('h') >= 0, v = m.indexOf('v') >= 0;
      Serial.printf("[CAMTEST] lat hinh: ngang=%d doc=%d -> %s (ghi CAM_HMIRROR / CAM_VFLIP vao config.h neu dung)\n", h, v, cameraSetFlip(h, v) ? "OK" : "that bai");
    } else if (arg.startsWith("q ")) {
      Serial.printf("[CAMTEST] chat luong %d -> %s\n", (int)arg.substring(2).toInt(), cameraSetQuality(arg.substring(2).toInt()) ? "OK" : "that bai (4..63)");
    } else {
      Serial.println("[CAMTEST] cam off | on | bench | xclk <5-24> | size <qvga|vga|svga|xga|hd> | q <4-63> | flip <none|v|h|vh>");
    }
#if USE_RC522
  } else if (low == "rc522 break") {         // gia lap RC522 tu reset (de kiem chung co tu phuc hoi)
    rfid.PCD_WriteRegister(MFRC522::TModeReg, 0x00);
    rfid.PCD_AntennaOff();
    Serial.println("[TEST] Da pha cau hinh RC522; trong 1 giay phai thay dong 'Mat cau hinh/anten ... da khoi dong lai chip'");
#endif
  } else if (low == "exit?") {                 // doc muc chan nut trong: 1 = nha (dung), 0 = dang bi nhan / ket thap
    Serial.printf("[EXIT] GPIO %d = %d (1 = nha, 0 = nhan/ket thap)\n", PIN_EXIT, digitalRead(PIN_EXIT));
  } else if (low == "i2c") {
    i2cScan();
  } else if (low == "i2cfind") {
    i2cFind();
  } else if (line.length()) {
    Serial.println("Lenh khong hieu. Go 'help'.");
  }
}

void pollSerial() {
  static String buf;
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (buf.length()) handleCommand(buf);
      buf = "";
    } else if (buf.length() < 64) {
      buf += c;
    }
  }
}
#endif

bool pressed(uint8_t pin) { return digitalRead(pin) == LOW; }

#if USE_CAMERA
// May chu hinh chi chay duoc khi da co IP: bat khi WiFi len (ke ca khi noi lai sau luc khoi dong)
void startStreamIfReady() {
  if (!camOk || cameraStreamRunning() || !wifiOk()) return;
  if (cameraStreamStart()) {
    String ip = WiFi.localIP().toString();
    String key = CAM_STREAM_KEY[0] ? String("?key=") + CAM_STREAM_KEY : String("");
    Serial.printf("[CAM] Luong cho AI : http://%s:%d/stream%s\n", ip.c_str(), CAM_STREAM_PORT, key.c_str());
    Serial.printf("[CAM] Xem thu      : http://%s/%s   (anh tinh: /capture)\n", ip.c_str(), key.c_str());
  }
}
#endif

void setup() {
  relayInit();                           // viec DAU TIEN: dam bao khoa khong bi cap dien luc khoi dong
  Serial.begin(115200);
#if ARDUINO_USB_CDC_ON_BOOT
  for (unsigned long t = millis(); !Serial && millis() - t < 3000;) delay(10);   // USB gốc: đợi Serial Monitor mở để thấy log khởi động
#endif
  delay(300);
  Serial.printf("\n=== Kiem soat ra vao v%s | %s ===\n", FW_VERSION, ESP.getChipModel());
  Serial.printf("[PIN] relay=%d (hut khi %s)  OLED sda=%d scl=%d  nut trong=%d\n", PIN_RELAY,
                RELAY_ACTIVE_LEVEL == HIGH ? "HIGH" : "LOW", PIN_SDA, PIN_SCL, PIN_EXIT);
#if USE_RC522
  Serial.printf("[PIN] RC522 ss=%d sck=%d mosi=%d miso=%d rst=%s\n", PIN_RC522_SS, PIN_RC522_SCK, PIN_RC522_MOSI, PIN_RC522_MISO,
                PIN_RC522_RST < 0 ? "noi 3V3" : String(PIN_RC522_RST).c_str());
#endif
#if USE_EXIT_BUTTON
  pinMode(PIN_EXIT, INPUT_PULLUP);
#endif
#if USE_PIR
  pinMode(PIN_PIR, INPUT);
#endif
  Wire.begin(PIN_SDA, PIN_SCL);
#if USE_OLED
  // Adafruit_SSD1306::begin() tra true ke ca khi KHONG co man hinh, nen phai tu hoi dia chi (0x3C roi 0x3D) truoc
  const uint8_t oledAddrs[] = {0x3C, 0x3D};
  // Thử lại vài lần: màn hình cần chút thời gian sau khi có điện (đo thật: lần quét đầu lúc 0,8 s trượt, quét sau 7 s thấy)
  for (int attempt = 0; attempt < 8 && !oledOk; attempt++) {
    if (attempt) delay(250);
    for (uint8_t addr : oledAddrs) {
      Wire.beginTransmission(addr);
      if (Wire.endTransmission() == 0 && oled.begin(SSD1306_SWITCHCAPVCC, addr)) {
        oledOk = true;
        Serial.printf("[OLED] OK o 0x%02X (lan thu %d)\n", addr, attempt + 1);
        break;
      }
    }
  }
  if (!oledOk) Serial.println("[OLED] KHONG thay o 0x3C / 0x3D (go lenh 'i2cfind' de do chan SDA/SCL). Van chay, chi khong co man hinh");
  drawScreen("KHOI DONG", "v" FW_VERSION);
#endif
#if USE_RC522
  rfidBegin();
#endif
#if USE_CAMERA
  camOk = cameraBegin();
  Serial.println(camOk ? "[CAM] Camera OK" : "[CAM] Camera LOI: cua van chay, chi khong co hinh cho AI");
#endif
  connectWifi();
  if (wifiOk()) {
    int code = sendHeartbeat();
    for (int i = 0; i < 2 && code != 200 && code != 401; i++) {   // vua co IP thi lan goi dau hay hut (ARP/route chua san sang): thu lai
      delay(1000);
      code = sendHeartbeat();
    }
    Serial.printf("[BACKEND] %s -> HTTP %d (%s)\n", BACKEND_URL, code,
                  code == 200 ? "OK" : code == 401 ? "SAI DEVICE_TOKEN" : "KHONG toi duoc backend: kiem tra IP, backend dang chay, tuong lua Windows cong 8080");
  }
  setState(WAIT_CARD);
#if USE_CAMERA
  startStreamIfReady();
#endif
  Serial.println("San sang. Go 'help' de xem lenh thu.");
}

void loop() {
  delay(1);                         // nhường CPU cho WiFi / camera; đọc thẻ mỗi ~2 ms là quá đủ
  unsigned long now = millis();

#if ENABLE_SERIAL_TEST
  pollSerial();
#endif

#if USE_CAMERA
  startStreamIfReady();
#endif

  // Tu noi lai WiFi khi roi mang (khong chan vong lap)
  if (!wifiOk() && wifiConfigured() && now - lastWifiTry >= WIFI_RETRY_MS) {
    lastWifiTry = now;
    Serial.println("[WIFI] Mat ket noi, thu lai...");
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASS);
  }

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
#if USE_PIR
    bool pir = digitalRead(PIN_PIR);
    if (pir != pirLast) {
      pirLast = pir;
      Serial.println(pir ? "[PIR] Phat hien nguoi" : "[PIR] Het nguoi");
    }
#endif
#if USE_EXIT_BUTTON
    // Theo CANH BAM, khong theo muc: phai NHA nut (muc cao on dinh) roi bam lai moi mo them. Neu theo muc, giu nut hoac chan 47
    // bi ket / cham GND se tu mo cua lap di lap lai moi khi het UNLOCK_MS (da gap: moi ~5,5 giay mot lan mo).
    static bool exitArmed = false;               // CHUA san sang cho toi khi thay chan o muc cao: chan dang thap luc khoi dong thi KHONG mo cua
    static unsigned long exitHighSince = 0, exitLowSince = 0, lastExitAt = 0;
    static bool exitStuckReported = false;
    unsigned long tNow = millis();
    {   // nhat ky chan doan: moi lan chan 47 doi muc on dinh >= 30 ms (de thay nut co thuc su nha / nhan, hay tiep diem lung lay)
      static bool candidate = false, reported = false;
      static unsigned long changeAt = 0;
      bool nowPressed = pressed(PIN_EXIT);
      if (nowPressed != candidate) {
        candidate = nowPressed;
        changeAt = tNow;
      } else if (candidate != reported && tNow - changeAt >= 30) {
        reported = candidate;
        Serial.printf("[EXIT] chan %d: %s\n", PIN_EXIT, candidate ? "NHAN (thap)" : "NHA (cao)");
      }
    }
    if (!pressed(PIN_EXIT)) {
      if (exitHighSince == 0) exitHighSince = tNow;
      if (tNow - exitHighSince >= EXIT_RELEASE_MS) exitArmed = true;   // da nha du lau: san sang cho lan bam moi
      exitLowSince = 0;
      exitStuckReported = false;
    } else {
      exitHighSince = 0;
      if (exitLowSince == 0) exitLowSince = tNow;
      if (exitArmed && tNow - exitLowSince >= 30 && tNow - lastExitAt >= EXIT_MIN_INTERVAL_MS) {   // chong doi nut 30 ms
        exitArmed = false;
        lastExitAt = tNow;
        Serial.println("[EXIT] Nut mo cua trong");
        String out;
        httpCall(true, "/api/v1/access/exit", String("{\"device_id\":\"") + DEVICE_ID + "\"}", out, 1000);
        unlock(UNLOCK_MS);
        return;
      }
      if (!exitStuckReported && tNow - exitLowSince >= EXIT_STUCK_MS) {
        exitStuckReported = true;                  // giu qua lau / chan ket thap: bao mot lan, khong mo them
        Serial.println("[EXIT] CANH BAO: chan nut trong luon o muc thap (ket nut, day cham GND hoac dang giu). Khong mo them.");
      }
    }
#endif
  }

  if (state == WAIT_CARD) {
    if (pendingUid.length()) {                     // the gia lap tu Serial
      String uid = pendingUid;
      pendingUid = "";
      Serial.printf("[TEST] Gia lap quet the %s\n", uid.c_str());
      submitCard(uid);
      return;
    }
#if USE_RC522
    String uid = rfidRead();
    if (uid.length()) {
      Serial.printf("[RC522] Doc the %s\n", uid.c_str());
      submitCard(uid);
      return;
    }
#endif
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
