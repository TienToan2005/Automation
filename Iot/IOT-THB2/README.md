# Bài thực hành số 2 – Thu thập, lưu trữ và tiền xử lý dữ liệu IoT

Pipeline: **ESP32 (DHT22 + HC-SR04) → MQTT → collector.py → InfluxDB (sensor_raw) → preprocess.py → InfluxDB (sensor_processed) → Grafana / Streamlit**

## Cấu trúc

| Đường dẫn | Nội dung |
|---|---|
| `src/main.cpp`, `platformio.ini`, `diagram.json`, `wokwi.toml` | Firmware ESP32 (PlatformIO) + mạch Wokwi |
| `python/simulator.py` | Giả lập thiết bị (có chèn outlier, null, trùng, mất bản tin) |
| `python/collector.py` | Subscribe MQTT, validate, ghi InfluxDB, đo độ trễ |
| `python/preprocess.py` | Làm sạch, outlier, resample, đặc trưng, chuẩn hóa |
| `python/app.py` | Ứng dụng Streamlit |
| `python/latency_report.py` | Thống kê độ trễ / tỉ lệ mất bản tin cho báo cáo |
| `docker-compose.yml`, `mosquitto/`, `grafana/` | Hạ tầng: Mosquitto, InfluxDB 2.7, Grafana (đã provisioning) |
| `docs/Bao_cao_Bai_TH2.docx` | Khung báo cáo Word |

## Yêu cầu

- Docker Desktop (đang chạy), Python 3.10+, (tuỳ chọn) VS Code + PlatformIO + Wokwi.

## Chạy nhanh (dùng simulator, không cần board)

```powershell
# 1. Cấu hình (đã có sẵn .env; nếu thiếu thì copy)
copy .env.example .env

# 2. Hạ tầng
docker compose up -d

# 3. Môi trường Python
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
cd python

# 4. Mở 3 terminal (đều đã activate venv, đứng trong python/):
python collector.py                       # terminal A: thu thập & ghi DB
python simulator.py --interval 1          # terminal B: giả lập thiết bị
python preprocess.py --range 1h --window 10s --loop 60   # terminal C: tiền xử lý lặp mỗi 60 s

# 5. Giám sát
streamlit run app.py                      # http://localhost:8501
# Grafana: http://localhost:3000  (admin/admin) -> dashboard "IoT Lab 2 - Giam sat"
# InfluxDB UI: http://localhost:8086 (admin / admin12345)
```

Đo độ trễ cho báo cáo: `python latency_report.py --range 1h`

## Chạy với ESP32 / Wokwi

Wokwi (đám mây) không truy cập được `localhost`, nên dùng broker công cộng:

1. Trong `src/main.cpp` đặt `DEVICE_ID` **duy nhất** (vd `esp32-<tên bạn>-01`); `MQTT_HOST` mặc định `broker.hivemq.com`.
2. Trong `.env` đặt `MQTT_HOST=broker.hivemq.com` và `MQTT_TOPIC=iot/lab2/<DEVICE_ID>/telemetry` (tránh nhận dữ liệu người khác trên broker công cộng).
3. Build: `pio run`, rồi mở `diagram.json` bằng extension Wokwi và Start Simulation.
4. Với board thật: đổi `WIFI_SSID`/`WIFI_PASS`, `MQTT_HOST` thành IP máy chạy Docker (broker Mosquitto trong compose), DHT22 SDA → GPIO15, HC-SR04 TRIG → GPIO5, ECHO → GPIO18, LED → GPIO2.

## Thiết kế chính

- **Topic**: `iot/lab2/<device_id>/telemetry`; payload JSON `{device_id, location, seq, ts(ms), temperature, humidity, distance_cm}`.
- **Schema**: `sensor_raw / environment` (tags `device_id`, `location`; fields `temperature, humidity, distance_cm, seq, latency_ms`); `sensor_processed / environment_clean` (tag `device_id`; fields gồm `*_roll`, `*_delta`, `*_norm`, `n_samples`, `n_outliers`).
- **Retention**: raw 30 ngày, processed 90 ngày (collector tự tạo bucket nếu thiếu).
- **Trùng lặp**: timestamp thiết bị làm timestamp point ⇒ gửi trùng sẽ ghi đè. **Mất tin**: phát hiện qua khoảng nhảy của `seq`.
- **Validate**: sai JSON, thiếu `device_id`/`ts`, `ts` lệch >24 h, giá trị ngoài giới hạn vật lý ⇒ bỏ; trường `null` bị bỏ riêng, các trường còn lại vẫn lưu.
- **Độ trễ**: `latency_ms = thời điểm collector nhận − ts thiết bị` (cần đồng hồ đồng bộ; simulator cùng máy nên chính xác).
- **Tiền xử lý**: dedupe → outlier IQR/Z-score → NaN → resample mean → nội suy theo thời gian (tối đa `--max-gap` cửa sổ) → rolling mean, delta → Min-Max.

## Xử lý sự cố

- `docker compose up` báo lỗi pipe: bật Docker Desktop.
- Grafana trống: kiểm tra collector/simulator đang chạy và khoảng thời gian dashboard.
- Streamlit báo không kết nối InfluxDB: kiểm tra `INFLUX_URL`/`INFLUX_TOKEN` trong `.env` (đổi token sau khi đã khởi tạo cần `docker compose down -v`).
