# Lab Access Control System: RFID + Face Recognition with Liveness Detection

A two-factor door access system for a university lab. A student taps an RFID card, then proves they are the card owner in front of the camera. A central backend makes every access decision; the ESP32 door controller and the AI service only report to it.

**Stack:** ESP32-S3-CAM (C++/PlatformIO) · Python · FastAPI · SQLAlchemy · InsightFace · ONNX Runtime · MiniFASNet · Docker Compose · pytest

> Detailed documentation is in Vietnamese: [`backend/README.md`](backend/README.md) (API, configuration, troubleshooting) and [`docs/`](docs/) (run guide, firmware flashing, authentication business rules).

## How it works

```
 Browser (admin / student) ───────► Backend (FastAPI, :8080) ◄────────── ESP32-S3-CAM
                                        │      ▲                       RC522 card reader,
                                        │      │                       OLED, relay lock,
                          trigger +     │      │ verify result         exit button, camera
                          face vector   ▼      │ (+ suspect snapshot)
                                     AI service (:5050) ◄── webcam
```

1. The student taps a card on the RC522 reader. The ESP32 sends the card UID to the backend.
2. The backend checks the card and the account (revoked, expired, suspended, temporarily locked, face enrolled). It opens an authentication session and asks the AI service to verify the face, sending the stored face vector.
3. The AI service reads the camera, matches the face (InsightFace embeddings, cosine similarity), runs liveness checks, and reports the result to the backend.
4. The backend records the decision, raises an alert if spoofing is suspected, and the ESP32, which polls the session, drives the relay.

The OLED guides the user in real time ("LOOK AT CAMERA", "TURN HEAD RIGHT", "BLINK"). If the AI service is down, the door fails secure: card taps are denied, while the web UI, heartbeat and remote unlock keep working.

## Features

**AI service** (`ai_service/`)
- Face verification with InsightFace embeddings and a configurable similarity threshold.
- Two-level liveness (presentation attack detection):
  - Active challenge: a random head turn (left/right) or blink, measured from 68 3D facial landmarks.
  - Passive anti-spoofing: MiniFASNet ONNX models score each frame against printed photos and screen replay.
- Image-quality gates (face size, sharpness, single face) so that bad frames lead to "retry" rather than a false accusation.
- Guided face enrollment (look straight, turn left, turn right, blink) with per-step timeouts.
- ONNX Runtime on CPU or NVIDIA GPU, with configurable thread count and GPU memory limit.
- Tools to calibrate liveness thresholds on a real webcam (`tools/calibrate_liveness.py`) and to evaluate FAR/FRR (`tools/eval_far_frr.py`).

**Backend and web UI** (`backend/`)
- REST API (FastAPI, SQLAlchemy, SQLite by default, PostgreSQL via `LAB_DATABASE_URL`) with auto-generated docs at `/docs`.
- JWT authentication (HttpOnly cookie or Bearer), PBKDF2-SHA256 password hashing, role-based access (admin / student) enforced server-side on every endpoint.
- Separate tokens for the device (`X-Device-Token`) and the AI service (`X-Service-Token`).
- Card lifecycle: issue, revoke, expire, disable, temporary lock with escalating levels, and per-card/per-device rate limiting.
- Atomic authentication sessions (`pending` → `granted | denied | timeout | cancelled | error`): a session closes exactly once, results for unknown or foreign sessions are rejected, and card/account status is re-checked at the moment of unlocking.
- Remote door control through a device heartbeat/command queue (works behind NAT; commands expire after 30 s and are delivered once; every unlock requires a logged reason).
- Instant notifications: in-browser bell, optional Telegram and email, with severity levels and cooldown to avoid spam.
- Reports: daily/hourly charts, denial reasons, top users, alert statistics; export to Excel (`.xlsx`), CSV or PDF (via print).
- Audit log of administrative actions, security alerts with suspect snapshots, and optional ThingsBoard telemetry.

**Firmware** (`firmware/`, `wokwi/`)
- ESP32-S3 (PlatformIO): RC522 RFID, SSD1306 OLED, relay lock, exit button, OV5640 camera stream.
- Wokwi simulation project for testing the card flow without hardware.

## Quick start

Requirements: Python 3.11+, a webcam, and (for real hardware) VS Code with PlatformIO.

```bash
# 1. Backend with demo data
cd backend
pip install -r requirements.txt
python -m scripts.seed_demo
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Open <http://localhost:8080> (API docs at <http://localhost:8080/docs>).

```bash
# 2. AI service (uses the webcam)
cd ai_service
pip install -r requirements.txt
python main.py
```

```bash
# 3. Firmware (optional, real hardware)
cd firmware
pio run -e esp32s3cam -t upload
```

Or run backend and AI service with Docker Compose:

```bash
docker compose up -d --build
```

Step-by-step instructions, wiring, and troubleshooting: [`docs/HUONG_DAN_CHAY.md`](docs/HUONG_DAN_CHAY.md) and [`docs/HUONG_DAN_NAP_FIRMWARE.md`](docs/HUONG_DAN_NAP_FIRMWARE.md) (Vietnamese).

> **Security note:** The demo seed creates an `admin` account with a well-known password and uses development tokens (`dev-service-token`, `dev-device-token`). These are for local demos only. Change `LAB_SECRET_KEY`, the tokens and the admin password before any real deployment.

## Testing

```bash
cd backend && pytest
cd ai_service && pytest
```

Backend tests cover the access flow, authentication policy, notifications, reports, ThingsBoard integration and DB migration. AI service tests cover liveness, enrollment, enrollment guidance, camera handling and the verification policy.

## Project structure

```
access-control-system/
├── backend/        FastAPI backend + Jinja2 web UI (api → services → models/core)
├── ai_service/     Face verification, liveness, enrollment, calibration tools
├── firmware/       ESP32-S3 firmware (PlatformIO)
├── wokwi/          Wokwi simulation project
├── docs/           API spec, run guides, authentication business rules (Vietnamese)
└── docker-compose.yml
```

## Known limitations

- This is a university project. The design notes in [`docs/NGHIEP_VU_XAC_THUC.md`](docs/NGHIEP_VU_XAC_THUC.md) list what has been verified by code and tests and what still needs testing on real hardware. It does not claim spoof-proof security.
- In-browser notification history is kept in memory and is lost when the backend restarts.
- Database schema changes use a lightweight add-missing-columns step rather than Alembic migrations.
- Face images and embeddings are personal biometric data and are deliberately excluded from this repository (`.gitignore`).
