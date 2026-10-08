#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Keypad.h>

// Kiem soat ra vao 2 lop: quet the -> quet mat -> mo khoa.
// Wokwi khong co dau doc RFID / camera: ma the nhap bang ban phim.
// Buoc quet mat uu tien ai_service (Iot/ai_service/main.py, FastAPI port 5050):
//   POST /api/ai/trigger {student_id}  -> AI bat dau quet bang webcam
//   GET  /api/ai/status                -> scanning | granted | denied | timeout
//   POST /api/ai/cancel                -> huy phien quet
// Nut "Mat hop le"/"Mat la" chi de mo phong, dung duoc ca khi AI dang quet hoac AI offline.
#define PIN_SDA 3
#define PIN_SCL 8
#define PIN_RELAY 13
#define PIN_EXIT 9        // nut mo cua phia trong
#define PIN_PIR 47        // phat hien co nguoi
#define PIN_FACE_OK 11    // mo phong camera: mat hop le
#define PIN_FACE_BAD 10   // mo phong camera: mat la

// Wokwi for VS Code: host.wokwi.internal tro toi may tinh dang chay ai_service.
#define WIFI_SSID "Wokwi-GUEST"
#define WIFI_PASS ""
#define AI_BASE_URL "http://host.wokwi.internal:5050"

const uint8_t MAX_TRIES = 3;
const unsigned long UNLOCK_MS = 20000;
const unsigned long LOCKOUT_MS = 15000;
const unsigned long FACE_TIMEOUT_MS = 17000;  // ai_service timeout 15s + du phong mang
const unsigned long AI_POLL_MS = 500;
const uint8_t AI_MAX_ERRORS = 3;              // loi lien tiep -> chuyen sang che do nut mo phong

Adafruit_SSD1306 oled(128, 64, &Wire, -1);

// Ban phim 4x4 dung de nhap ma the (4 ky tu), '#' = xac nhan, '*' = xoa
const byte ROWS = 4, COLS = 4;
char keys[ROWS][COLS] = {
  {'1', '2', '3', 'A'},
  {'4', '5', '6', 'B'},
  {'7', '8', '9', 'C'},
  {'*', '0', '#', 'D'}};
byte rowPins[ROWS] = {1, 2, 42, 41};
byte colPins[COLS] = {40, 39, 38, 37};
Keypad keypad(makeKeymap(keys), rowPins, colPins, ROWS, COLS);

// Ma the -> ma sinh vien (ten file trong ai_service/embeddings/<student_id>.npy)
struct Card {
  const char *code;
  const char *studentId;
};
const Card CARDS[] = {
  {"A123", "B23DCAT286"},
  {"B456", "B23DCAT296"},
  {"C789", "B23DCAT999"},  // the hop le nhung chua dang ky mat -> AI tu choi
};
const uint8_t CARD_LEN = 4;
String cardInput = "";
const Card *currentCard = nullptr;

const Card *findCard(const String &c) {
  for (const Card &card : CARDS)
    if (c == card.code) return &card;
  return nullptr;
}

enum State { WAIT_CARD, WAIT_FACE, UNLOCKED, LOCKOUT };
State state = WAIT_CARD;
uint8_t tries = 0;
unsigned long stateSince = 0;

// ---------------- ai_service ----------------
enum AiResult { AI_PENDING, AI_GRANTED, AI_DENIED, AI_TIMEOUT, AI_ERROR };

bool aiOnline = false;   // AI dang quet cho phien hien tai
uint8_t aiErrors = 0;
unsigned long lastPoll = 0;

bool wifiOk() { return WiFi.status() == WL_CONNECTED; }

bool aiTrigger(const char *studentId) {
  if (!wifiOk()) return false;
  HTTPClient http;
  http.setTimeout(2000);
  http.begin(AI_BASE_URL "/api/ai/trigger");
  http.addHeader("Content-Type", "application/json");
  int code = http.POST(String("{\"student_id\":\"") + studentId + "\"}");
  http.end();
  Serial.printf("[AI] Trigger %s -> HTTP %d\n", studentId, code);
  return code == 200;
}

void aiCancel() {
  if (!wifiOk()) return;
  HTTPClient http;
  http.setTimeout(1000);
  http.begin(AI_BASE_URL "/api/ai/cancel");
  http.POST("");
  http.end();
  Serial.println("[AI] Huy phien quet");
}

AiResult aiPoll() {
  if (!wifiOk()) return AI_ERROR;
  HTTPClient http;
  http.setTimeout(1500);
  http.begin(AI_BASE_URL "/api/ai/status");
  int code = http.GET();
  String body = code == 200 ? http.getString() : "";
  http.end();
  if (code != 200) return AI_ERROR;

  JsonDocument doc;
  if (deserializeJson(doc, body)) return AI_ERROR;
  // Bo qua ket qua cua phien quet cu / sinh vien khac
  if (strcmp(doc["student_id"] | "", currentCard->studentId) != 0) return AI_PENDING;

  const char *r = doc["result"] | "";
  if (!strcmp(r, "granted")) {
    Serial.printf("[AI] Mat hop le (sim=%.2f)\n", doc["similarity"] | 0.0f);
    return AI_GRANTED;
  }
  if (!strcmp(r, "denied")) return AI_DENIED;
  if (!strcmp(r, "timeout")) return AI_TIMEOUT;
  return AI_PENDING;
}
// --------------------------------------------

bool pressed(uint8_t pin) { return digitalRead(pin) == LOW; }

void drawScreen(const char *l1, const char *l2 = "", const char *l3 = "") {
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);
  oled.setTextSize(1);
  oled.setCursor(0, 0);
  oled.print("KIEM SOAT RA VAO");
  oled.setCursor(110, 0);
  oled.print(wifiOk() ? "AI" : "--");
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
    char l2[24];
    if (aiOnline)
      snprintf(l2, sizeof(l2), "AI:%s %lus", currentCard->studentId, faceSecondsLeft());
    else
      snprintf(l2, sizeof(l2), "Mo phong: bam nut %lus", faceSecondsLeft());
    drawScreen("QUET MAT", l2, buf);
  }
}

void setState(State s) {
  if (state == WAIT_FACE && s != WAIT_FACE && aiOnline) {
    aiCancel();  // roi buoc quet mat khi AI chua ket luan (nut mo phong, het gio, nut trong)
    aiOnline = false;
  }
  state = s;
  stateSince = millis();
  digitalWrite(PIN_RELAY, s == UNLOCKED ? HIGH : LOW);
  if (s == UNLOCKED) {
    drawScreen("MO CUA", "The + Mat hop le");
    Serial.println("[LOCK] UNLOCKED");
  } else if (s == LOCKOUT) {
    drawScreen("BI KHOA", "Sai qua nhieu lan");
    Serial.println("[LOCK] LOCKOUT");
  } else {
    cardInput = "";
    showState();
    Serial.println(s == WAIT_CARD ? "[LOCK] Cho quet the" : "[LOCK] Cho quet mat");
  }
}

// Mot lan xac thuc that bai: tinh vao so lan sai chung cho ca the va mat.
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
  setState(WAIT_CARD);  // sai o buoc nao cung phai quet lai tu the
}

void grant(const char *how) {
  Serial.printf("[CAM] Mat hop le (%s)\n", how);
  tries = 0;
  setState(UNLOCKED);
}

void connectWifi() {
  drawScreen("WIFI", "Dang ket noi...", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASS, 6);
  unsigned long t0 = millis();
  while (!wifiOk() && millis() - t0 < 10000) delay(250);
  if (wifiOk())
    Serial.printf("[WIFI] OK, IP %s\n", WiFi.localIP().toString().c_str());
  else
    Serial.println("[WIFI] That bai -> chi dung nut mo phong");
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_RELAY, OUTPUT);
  pinMode(PIN_PIR, INPUT);
  pinMode(PIN_EXIT, INPUT_PULLUP);
  pinMode(PIN_FACE_OK, INPUT_PULLUP);
  pinMode(PIN_FACE_BAD, INPUT_PULLUP);
  Wire.begin(PIN_SDA, PIN_SCL);
  if (!oled.begin(SSD1306_SWITCHCAPVCC, 0x3C)) Serial.println("OLED khong tim thay");
  connectWifi();
  setState(WAIT_CARD);
}

void loop() {
  unsigned long now = millis();

  if (state == UNLOCKED && now - stateSince >= UNLOCK_MS) setState(WAIT_CARD);

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
      showState();
    }

    if (pressed(PIN_EXIT)) {
      Serial.println("[EXIT] Nut mo cua trong");
      setState(UNLOCKED);
      return;
    }
  }

  if (state == WAIT_CARD) {
    char k = keypad.getKey();
    if (!k) return;
    if (k == '*') {
      cardInput = "";
    } else if (k == '#') {
      drawScreen("DANG DOC", "The...");
      const Card *card = findCard(cardInput);
      if (card) {
        Serial.printf("[CARD] The hop le: %s -> %s\n", card->code, card->studentId);
        currentCard = card;
        aiErrors = 0;
        lastPoll = millis();
        aiOnline = aiTrigger(card->studentId);
        if (!aiOnline) Serial.println("[AI] Khong ket noi duoc ai_service -> dung nut mo phong");
        setState(WAIT_FACE);
        return;
      }
      Serial.printf("[CARD] The la: %s\n", cardInput.c_str());
      delay(800);
      fail("The la");
      return;
    } else if (cardInput.length() < CARD_LEN) {
      cardInput += k;
    }
    showState();
  } else if (state == WAIT_FACE) {
    // Nut mo phong luon duoc uu tien xu ly ngay (khong cho AI)
    if (pressed(PIN_FACE_OK)) {
      drawScreen("DANG QUET", "Camera mo phong...");
      delay(1500);
      grant("nut mo phong");
      return;
    }
    if (pressed(PIN_FACE_BAD)) {
      drawScreen("DANG QUET", "Camera mo phong...");
      delay(1500);
      fail("Mat la");
      return;
    }

    if (now - stateSince >= FACE_TIMEOUT_MS) {
      Serial.println("[AUTH] Het thoi gian quet mat");
      setState(WAIT_CARD);
      return;
    }

    if (aiOnline && now - lastPoll >= AI_POLL_MS) {
      lastPoll = now;
      switch (aiPoll()) {
        case AI_GRANTED:
          aiOnline = false;
          grant("ai_service");
          return;
        case AI_DENIED:
          aiOnline = false;
          fail("AI: Mat la");
          return;
        case AI_TIMEOUT:
          aiOnline = false;
          Serial.println("[AI] Het thoi gian quet mat");
          setState(WAIT_CARD);
          return;
        case AI_ERROR:
          if (++aiErrors >= AI_MAX_ERRORS) {
            aiOnline = false;
            Serial.println("[AI] Mat ket noi -> dung nut mo phong");
          }
          break;
        case AI_PENDING:
          aiErrors = 0;
          break;
      }
    }

    // Cap nhat dem nguoc tren OLED moi giay
    static unsigned long lastShown = 0;
    if (faceSecondsLeft() != lastShown) {
      lastShown = faceSecondsLeft();
      showState();
    }
  }
}
