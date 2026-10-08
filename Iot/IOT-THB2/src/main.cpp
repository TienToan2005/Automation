// ESP32 + DHT22 + HC-SR04 (+ LED bao gui) -> JSON -> MQTT
// Topic: iot/lab2/<DEVICE_ID>/telemetry
// Payload: {"device_id","location","seq","ts"(epoch ms),"temperature","humidity","distance_cm","rssi"}
#include <Arduino.h>
#include <DHTesp.h>
#include <PubSubClient.h>
#include <WiFi.h>
#include <sys/time.h>

// ---- Cau hinh (sua cho phu hop) ----
const char* WIFI_SSID = "Wokwi-GUEST";  // Wokwi: Wokwi-GUEST, mat khau rong
const char* WIFI_PASSWORD = "";
const char* MQTT_HOST = "broker.hivemq.com";  // broker cong cong; collector phai dung cung host
const int MQTT_PORT = 1883;
const char* DEVICE_ID = "esp32-wokwi-at296";  // DOI thanh ten duy nhat tren broker cong cong
const char* LOCATION = "lab";
const unsigned long SEND_INTERVAL_MS = 2000;

const int DHT_PIN = 15;
const int TRIG_PIN = 5;
const int ECHO_PIN = 18;
const int LED_PIN = 2;

WiFiClient wifiClient;
PubSubClient mqttClient(wifiClient);
DHTesp dht;
char topic[80];
unsigned long lastSend = 0;
unsigned long seq = 0;

void connectWiFi() {
  Serial.print("Connecting WiFi");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD, 6);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println(" connected");
}

// ts gui di la epoch ms that => can NTP de do do tre end-to-end dung
void syncTime() {
  configTime(0, 0, "pool.ntp.org", "time.google.com");
  time_t now = 0;
  while (now < 1700000000) {
    delay(300);
    time(&now);
  }
  Serial.println("NTP synced");
}

int64_t nowMs() {
  struct timeval tv;
  gettimeofday(&tv, nullptr);
  return (int64_t)tv.tv_sec * 1000 + tv.tv_usec / 1000;
}

void connectMQTT() {
  while (!mqttClient.connected()) {
    String clientId = String(DEVICE_ID) + "-" + String((uint32_t)ESP.getEfuseMac(), HEX);
    Serial.print("Connecting MQTT...");
    if (mqttClient.connect(clientId.c_str())) {
      Serial.println(" connected");
    } else {
      Serial.printf(" failed, rc=%d. Retry in 2 s\n", mqttClient.state());
      delay(2000);
    }
  }
}

float readDistanceCm() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);
  unsigned long duration = pulseIn(ECHO_PIN, HIGH, 30000);
  if (duration == 0) return NAN;
  return duration * 0.0343f / 2.0f;
}

// Truong loi/NaN -> "null" de collector va buoc tien xu ly xu ly gia tri thieu
int appendField(char* buf, size_t size, size_t pos, const char* name, float v) {
  if (isfinite(v)) return snprintf(buf + pos, size - pos, ",\"%s\":%.2f", name, v);
  return snprintf(buf + pos, size - pos, ",\"%s\":null", name);
}

void setup() {
  Serial.begin(115200);
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
  dht.setup(DHT_PIN, DHTesp::DHT22);

  snprintf(topic, sizeof(topic), "iot/lab2/%s/telemetry", DEVICE_ID);
  connectWiFi();
  syncTime();
  mqttClient.setServer(MQTT_HOST, MQTT_PORT);
  mqttClient.setBufferSize(512);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) connectWiFi();
  if (!mqttClient.connected()) connectMQTT();
  mqttClient.loop();

  unsigned long now = millis();
  if (now - lastSend < SEND_INTERVAL_MS) return;
  lastSend = now;

  TempAndHumidity data = dht.getTempAndHumidity();
  float distance = readDistanceCm();
  if (dht.getStatus() != 0) {  // loi DHT: bao NaN
    data.temperature = NAN;
    data.humidity = NAN;
  }

  char payload[320];
  size_t n = snprintf(payload, sizeof(payload),
                      "{\"device_id\":\"%s\",\"location\":\"%s\",\"seq\":%lu,\"ts\":%lld",
                      DEVICE_ID, LOCATION, ++seq, (long long)nowMs());
  n += appendField(payload, sizeof(payload), n, "temperature", data.temperature);
  n += appendField(payload, sizeof(payload), n, "humidity", data.humidity);
  n += appendField(payload, sizeof(payload), n, "distance_cm", distance);
  snprintf(payload + n, sizeof(payload) - n, ",\"rssi\":%d}", WiFi.RSSI());

  bool ok = mqttClient.publish(topic, payload);
  Serial.printf("%s | publish=%s\n", payload, ok ? "OK" : "FAILED");

  digitalWrite(LED_PIN, HIGH);  // LED nhay moi lan gui
  delay(80);
  digitalWrite(LED_PIN, LOW);
}
