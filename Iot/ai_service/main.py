import base64
import os
import queue
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

import cv2
import numpy as np
import requests
import uvicorn
from fastapi import FastAPI, HTTPException
from insightface.app import FaceAnalysis
from pydantic import BaseModel
from typing import List, Optional

from liveness import LivenessChallenge, PadWindow, PassivePAD

# Console Windows mặc định cp1252 sẽ lỗi khi in tiếng Việt
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ================= CẤU HÌNH HỆ THỐNG (ghi đè bằng biến môi trường AI_*) =================
def _env(name, default):
    return os.environ.get(name, default)

def _env_bool(name, default):
    return _env(name, "1" if default else "0").lower() in ("1", "true", "yes")

_cam = _env("AI_CAMERA", "0")
CAMERA_SOURCE = int(_cam) if _cam.isdigit() else _cam      # 0 = webcam; hoặc URL stream của ESP32-CAM (OV5640)
CAMERA_ID = _env("AI_CAMERA_ID", "cam_cua_chinh_01")
# Backend (Thành viên 3): AI đẩy kết quả từng phiên (kèm ảnh nghi phạm). Để trống = tắt.
BACKEND_EVENT_URL = _env("AI_BACKEND_EVENT_URL", "")       # vd http://localhost:8080/api/v1/access/verify
SERVICE_TOKEN = _env("AI_SERVICE_TOKEN", "")               # gửi kèm header X-Service-Token
THINGSBOARD_URL = _env("AI_THINGSBOARD_URL", "")           # vd http://<tb-host>:8080/api/v1/<DEVICE_TOKEN>/telemetry
PAD_MODEL_DIR = _env("AI_PAD_MODEL_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "models"))
USE_GPU = _env_bool("AI_USE_GPU", False)
SHOW_WINDOW = _env_bool("AI_SHOW_WINDOW", True)
REQUIRE_PAD = _env_bool("AI_REQUIRE_PAD", True)            # True = thiếu model PAD thì từ chối (fail-closed)

SIMILARITY_THRESHOLD = float(_env("AI_SIM_THRESHOLD", "0.50"))
REQUIRED_FRAMES = 5      # số frame liên tiếp khớp mặt trước khi sang bước thách thức
MISMATCH_FRAMES = 20     # số frame liên tiếp có mặt nhưng sai chủ thể -> từ chối
SCAN_TIMEOUT_S = 16.0    # ESP32 chờ tối đa 17s (FACE_TIMEOUT_MS)
CHALLENGE_TIMEOUT_S = 8.0
MIN_FACE_WIDTH = int(_env("AI_MIN_FACE_WIDTH", "120"))     # "chặn cửa": mặt phải đủ lớn (đứng đúng khoảng cách) mới xử lý
PAD_THRESHOLD = float(_env("AI_PAD_THRESHOLD", "0.70"))    # trung bình điểm mặt thật tối thiểu
PAD_WINDOW = 6
PAD_MIN_FRAMES = 4
SNAPSHOT_WIDTH = 480
# ========================================================================================

state_lock = threading.Lock()

# Biến toàn cục để giao tiếp giữa API và AI Camera
ai_state = {
    "is_active": False,
    "session_id": None,
    "expected_id": None,
    "embedding": None,       # vector 512 chiều của phiên hiện tại
    "timeout_at": 0,
    "started_at": 0,
    "result": "idle",        # idle | scanning | granted | denied | timeout | cancelled
    "reason": "",            # ok | face_mismatch | spoof_pad | challenge_failed | not_enrolled | pad_unavailable | timeout | cancelled | superseded
    "phase": "idle",         # idle | verify | challenge | done
    "hint": "",              # no_face | move_closer | multiple_faces | ""
    "similarity": 0.0,
    "pad_score": 0.0,
    "challenge": None,       # {"type", "text", "text_vi", "deadline"}
}
known_embeddings = {}
face_app = None
pad = None
camera_ok = False


# ----------------------- Đẩy sự kiện sang Backend / ThingsBoard -----------------------
event_queue = queue.Queue(maxsize=200)

def get_iso_timestamp():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

def encode_snapshot(frame):
    if frame is None:
        return None
    h, w = frame.shape[:2]
    if w > SNAPSHOT_WIDTH:
        frame = cv2.resize(frame, (SNAPSHOT_WIDTH, int(h * SNAPSHOT_WIDTH / w)))
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return base64.b64encode(buf.tobytes()).decode() if ok else None

def publish_event(event):
    """Đưa vào hàng đợi; luồng nền gửi để không làm chậm vòng lặp camera."""
    try:
        event_queue.put_nowait(event)
    except queue.Full:
        print("[EVENT] Hàng đợi đầy, bỏ sự kiện", event["session_id"])

ALERT_LEVEL = {"spoof_pad": "high", "challenge_failed": "high", "face_mismatch": "medium"}
ALERT_MESSAGE = {
    "spoof_pad": "Phát hiện giả mạo khuôn mặt (ảnh in / ảnh hoặc video trên màn hình)",
    "challenge_failed": "Khuôn mặt đúng nhưng không thực hiện được thách thức người thật (nghi ảnh tĩnh)",
    "face_mismatch": "Khuôn mặt không khớp chủ thẻ (nghi dùng thẻ của người khác)",
}

def _event_worker():
    while True:
        ev = event_queue.get()
        if BACKEND_EVENT_URL:
            try:
                headers = {"X-Service-Token": SERVICE_TOKEN} if SERVICE_TOKEN else {}
                requests.post(BACKEND_EVENT_URL, json=ev, headers=headers, timeout=3)
            except requests.RequestException as e:
                print(f"[EVENT] Gửi backend thất bại: {e}")
        if THINGSBOARD_URL:
            try:
                reason = ev["reason"]
                if reason in ALERT_LEVEL:
                    tb = {"event_type": "security_alert", "alert_level": ALERT_LEVEL[reason],
                          "message": ALERT_MESSAGE[reason], "reason": reason, "student_id": ev["student_id"],
                          "camera_id": ev["camera_id"], "timestamp": ev["timestamp"]}
                else:
                    tb = {"event_type": "access_event", "result": ev["result"], "reason": reason,
                          "student_id": ev["student_id"], "camera_id": ev["camera_id"], "timestamp": ev["timestamp"]}
                tb.update(confidence_score=ev["confidence_score"], pad_score=ev["liveness"]["pad_score"],
                          duration_ms=ev["duration_ms"])
                requests.post(THINGSBOARD_URL, json=tb, timeout=3)
            except requests.RequestException as e:
                print(f"[EVENT] Gửi ThingsBoard thất bại: {e}")

def _build_event(frame, snapshot):
    ch = ai_state["challenge"]
    return {
        "camera_id": CAMERA_ID,
        "student_id": ai_state["expected_id"],
        "confidence_score": round(float(ai_state["similarity"]), 3),
        "timestamp": get_iso_timestamp(),
        "session_id": ai_state["session_id"],
        "result": ai_state["result"],
        "reason": ai_state["reason"],
        "liveness": {
            "passed": ai_state["result"] == "granted",
            "pad_score": round(float(ai_state["pad_score"]), 3),
            "challenge_type": ch["type"] if ch else None,
        },
        "duration_ms": int((time.time() - ai_state["started_at"]) * 1000),
        "snapshot_jpeg_base64": snapshot,
    }

def finish(result, reason, frame=None):
    """Kết thúc phiên (chỉ một lần). Ảnh chụp chỉ đính kèm khi bị từ chối / hết giờ."""
    with state_lock:
        if not ai_state["is_active"]:
            return False
        ai_state.update(is_active=False, result=result, reason=reason, phase="done", hint="")
        snap = encode_snapshot(frame) if result in ("denied", "timeout") else None
        event = _build_event(frame, snap)
    print(f"[SESSION] {event['student_id']} -> {result} ({reason}) sim={event['confidence_score']} pad={event['liveness']['pad_score']}")
    publish_event(event)
    return True


# --------------------------------- FASTAPI ---------------------------------
api_app = FastAPI(title="AI Access Control Service")

class TriggerRequest(BaseModel):
    student_id: str
    session_id: Optional[str] = None          # Backend sinh để đối chiếu log; bỏ trống thì AI tự sinh
    embedding: Optional[List[float]] = None   # vector 512 chiều lấy từ DB; bỏ trống thì dùng file embeddings/ cục bộ

class EmbedRequest(BaseModel):
    image_base64: str                         # ảnh JPEG/PNG (chấp nhận cả dạng data URL)

def _clean_embedding(values):
    emb = np.asarray(values, dtype=np.float32)
    if emb.shape != (512,) or not np.isfinite(emb).all() or np.linalg.norm(emb) == 0:
        raise HTTPException(status_code=400, detail="embedding must be 512 finite floats")
    return emb / np.linalg.norm(emb)

@api_app.post("/api/ai/trigger")
def trigger_scan(request: TriggerRequest):
    """Backend/ESP32 kích hoạt một phiên xác thực khuôn mặt sau khi thẻ hợp lệ."""
    student_id = request.student_id.strip()
    if not student_id:
        raise HTTPException(status_code=400, detail="Missing student_id")

    emb = _clean_embedding(request.embedding) if request.embedding is not None else known_embeddings.get(student_id)
    session_id = request.session_id or uuid.uuid4().hex[:12]

    # Phiên cũ còn chạy -> đóng lại để có log
    finish("cancelled", "superseded")

    with state_lock:
        now = time.time()
        ai_state.update(
            session_id=session_id, expected_id=student_id, embedding=emb, started_at=now,
            timeout_at=now + SCAN_TIMEOUT_S, similarity=0.0, pad_score=0.0, challenge=None, hint="",
            phase="verify" if emb is not None else "done",
        )
        if emb is None:
            ai_state.update(is_active=False, result="denied", reason="not_enrolled")
            event = _build_event(None, None)
        elif REQUIRE_PAD and (pad is None or not pad.enabled):
            ai_state.update(is_active=False, result="denied", reason="pad_unavailable", phase="done")
            event = _build_event(None, None)
        else:
            ai_state.update(is_active=True, result="scanning", reason="")
            event = None

    if event is not None:
        print(f"[API] {student_id}: từ chối ngay ({event['reason']})")
        publish_event(event)
        return {"status": "denied", "reason": event["reason"], "session_id": session_id,
                "message": "Student has no face enrollment" if event["reason"] == "not_enrolled" else "Anti-spoof model unavailable"}

    print(f"[API] Đã nhận tín hiệu thẻ từ: {student_id} (phiên {session_id}). Bắt đầu xác thực khuôn mặt...")
    return {"status": "success", "session_id": session_id, "message": "AI started scanning"}

@api_app.get("/api/ai/status")
def scan_status():
    """ESP32/Backend hỏi định kỳ kết quả của phiên hiện tại."""
    with state_lock:
        s = dict(ai_state)
    time_left = max(0, int(s["timeout_at"] - time.time())) if s["is_active"] else 0
    ch = s["challenge"]
    return {
        "session_id": s["session_id"],
        "student_id": s["expected_id"],
        "result": s["result"],
        "reason": s["reason"],
        "phase": s["phase"],
        "hint": s["hint"],
        "challenge": None if not ch or not s["is_active"] else {
            "type": ch["type"], "text": ch["text"], "text_vi": ch["text_vi"],
            "time_left": max(0, int(ch["deadline"] - time.time())),
        },
        "similarity": round(float(s["similarity"]), 3),
        "pad_score": round(float(s["pad_score"]), 3),
        "time_left": time_left,
    }

@api_app.post("/api/ai/cancel")
def cancel_scan():
    """ESP32 huỷ phiên quét (đã xác thực bằng nút mô phỏng, hết giờ, nút mở cửa trong...)"""
    if finish("cancelled", "cancelled"):
        print(f"[API] Huỷ phiên quét của: {ai_state['expected_id']}")
    return {"status": "success"}

@api_app.post("/api/ai/reload")
def reload_embeddings():
    """Nạp lại embeddings/ cục bộ (dùng khi đăng ký mới / thu hồi mà không gửi vector trong /trigger)."""
    known_embeddings.clear()
    known_embeddings.update(load_known_embeddings())
    return {"status": "success", "enrolled": sorted(known_embeddings)}

@api_app.get("/api/ai/health")
def health():
    return {
        "status": "ok" if face_app is not None else "starting",
        "camera_ok": camera_ok,
        "camera_id": CAMERA_ID,
        "pad_enabled": bool(pad and pad.enabled),
        "pad_models": pad.names if pad else [],
        "require_pad": REQUIRE_PAD,
        "gpu": USE_GPU,
        "local_enrolled_count": len(known_embeddings),
        "session_active": ai_state["is_active"],
        "thresholds": {"similarity": SIMILARITY_THRESHOLD, "pad": PAD_THRESHOLD, "min_face_width": MIN_FACE_WIDTH},
    }

@api_app.post("/api/ai/embed")
def embed_image(request: EmbedRequest):
    """Dashboard đăng ký người dùng: ảnh chụp -> vector 512 chiều (+ kiểm tra chất lượng và chống ảnh giả)."""
    if face_app is None:
        raise HTTPException(status_code=503, detail="Model not ready")
    data = request.image_base64.split(",", 1)[-1]
    try:
        raw = base64.b64decode(data, validate=False)
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("too large")
        img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("decode")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image")

    faces = face_app.get(img)
    if len(faces) == 0:
        return {"ok": False, "reason": "no_face"}
    if len(faces) > 1:
        return {"ok": False, "reason": "multiple_faces"}
    f = faces[0]
    width = float(f.bbox[2] - f.bbox[0])
    score = pad.score(img, f.bbox) if pad and pad.enabled else None
    base = {"face_width": round(width, 1), "det_score": round(float(f.det_score), 3),
            "pad_score": None if score is None else round(score, 3)}
    if width < MIN_FACE_WIDTH * 0.75 or f.det_score < 0.6:
        return {"ok": False, "reason": "low_quality", **base}
    if score is not None and score < PAD_THRESHOLD:
        return {"ok": False, "reason": "spoof_suspected", **base}
    return {"ok": True, "reason": "ok", "embedding": [round(float(v), 6) for v in f.normed_embedding], **base}

def run_server():
    uvicorn.run(api_app, host="0.0.0.0", port=5050, log_level="error")


# --------------------------------- XỬ LÝ KHUNG HÌNH ---------------------------------
def compute_similarity(emb1, emb2):
    return float(np.dot(emb1, emb2) / (np.linalg.norm(emb1) * np.linalg.norm(emb2)))

def load_known_embeddings():
    embeddings = {}
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'embeddings')
    if os.path.exists(folder):
        for file in os.listdir(folder):
            if file.endswith('.npy'):
                embeddings[file.split('.')[0]] = np.load(os.path.join(folder, file))
    return embeddings

class SessionCtx:
    """Trạng thái đếm frame của riêng một phiên (reset khi có phiên mới)."""
    def __init__(self, sid):
        self.sid = sid
        self.match = 0
        self.mismatch = 0
        self.pad = PadWindow(PAD_WINDOW, PAD_MIN_FRAMES)
        self.challenge = None

    def reset_liveness(self):
        self.pad.clear()
        self.challenge = None
        self.match = 0
        with state_lock:
            ai_state.update(challenge=None, phase="verify", pad_score=0.0)

def process_frame(frame, ctx):
    """Một bước xử lý cho phiên đang active. Trả về (faces_vẽ, chú thích debug)."""
    faces = face_app.get(frame)
    big = [f for f in faces if (f.bbox[2] - f.bbox[0]) >= MIN_FACE_WIDTH]
    drawn = []

    if not faces:
        hint = "no_face"
    elif not big:
        hint = "move_closer"
    elif len(big) > 1:
        hint = "multiple_faces"     # chống đi theo nhau: chỉ một người trong khung
    else:
        hint = ""
    with state_lock:
        ai_state["hint"] = hint
    if hint:
        ctx.match = 0
        return drawn, ""

    face = big[0]
    emb = ai_state["embedding"]
    sim = compute_similarity(face.normed_embedding, emb)
    with state_lock:
        ai_state["similarity"] = max(ai_state["similarity"], sim)

    if sim < SIMILARITY_THRESHOLD:
        drawn.append((face, False, f"Sai chu the ({sim:.2f})"))
        ctx.mismatch += 1
        if ctx.challenge is not None or len(ctx.pad.scores):
            ctx.reset_liveness()      # đổi người giữa chừng -> làm lại từ đầu
        ctx.match = 0
        if ctx.mismatch >= MISMATCH_FRAMES:
            finish("denied", "face_mismatch", frame)
        return drawn, ""

    ctx.mismatch = 0
    ctx.match += 1
    drawn.append((face, True, f"Trung khop ({sim:.2f})"))

    # ----- Mức 2: PAD thụ động trên cùng khuôn mặt đã khớp -----
    if pad.enabled:
        ctx.pad.add(pad.score(frame, face.bbox))
        with state_lock:
            ai_state["pad_score"] = ctx.pad.mean()
        if ctx.pad.full() and ctx.pad.mean() < PAD_THRESHOLD:
            finish("denied", "spoof_pad", frame)
            return drawn, ""
        pad_ok = ctx.pad.ready() and ctx.pad.mean() >= PAD_THRESHOLD
    else:
        pad_ok = not REQUIRE_PAD   # chế độ dev: AI_REQUIRE_PAD=0 và không có model

    # ----- Mức 1: thách thức ngẫu nhiên -----
    debug = ""
    if ctx.challenge is None:
        if ctx.match >= REQUIRED_FRAMES and pad_ok:
            ctx.challenge = LivenessChallenge(timeout_s=min(CHALLENGE_TIMEOUT_S, max(1.0, ai_state["timeout_at"] - time.time())))
            with state_lock:
                ai_state.update(phase="challenge", challenge={
                    "type": ctx.challenge.type, "text": ctx.challenge.text,
                    "text_vi": ctx.challenge.text_vi, "deadline": ctx.challenge.deadline})
            print(f"[LIVENESS] Thách thức: {ctx.challenge.type}")
    else:
        status = ctx.challenge.update(face)
        debug = f"{ctx.challenge.type}: {ctx.challenge.debug:+.2f}"
        if status == "passed":
            finish("granted", "ok", frame)
        elif status == "failed":
            finish("denied", "challenge_failed", frame)
    return drawn, debug

def draw_overlay(frame, drawn, debug):
    for face, ok, label in drawn:
        x1, y1, x2, y2 = map(int, face.bbox)
        color = (0, 255, 0) if ok else (0, 0, 255)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        cv2.putText(frame, label, (x1, max(15, y1 - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    with state_lock:
        s = dict(ai_state)
    cv2.putText(frame, f"DANG XAC MINH THE: {s['expected_id']}", (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 0), 2)
    cv2.putText(frame, f"Thoi gian: {max(0, int(s['timeout_at'] - time.time()))}s  PAD: {s['pad_score']:.2f}",
                (20, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
    if s["challenge"]:
        cv2.putText(frame, s["challenge"]["text"], (20, 110), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 200, 255), 3)
    elif s["hint"]:
        cv2.putText(frame, {"no_face": "KHONG THAY MAT", "move_closer": "LAI GAN HON",
                            "multiple_faces": "CHI 1 NGUOI TRONG KHUNG"}[s["hint"]], (20, 110),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 200, 255), 2)
    if debug:
        cv2.putText(frame, debug, (20, frame.shape[0] - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)

def init_models():
    global face_app, pad
    providers = ["CPUExecutionProvider"]
    ctx_id = -1
    if USE_GPU:
        import onnxruntime as ort
        if "CUDAExecutionProvider" in ort.get_available_providers():
            providers, ctx_id = ["CUDAExecutionProvider", "CPUExecutionProvider"], 0
        else:
            print("[GPU] Không có CUDAExecutionProvider (cần `pip install onnxruntime-gpu`) -> chạy CPU")
    face_app = FaceAnalysis(name='buffalo_l', providers=providers)
    face_app.prepare(ctx_id=ctx_id, det_size=(640, 640))
    pad = PassivePAD(PAD_MODEL_DIR, providers)
    if not pad.enabled:
        msg = "KHÔNG có model PAD trong " + PAD_MODEL_DIR + " (xem tools/export_pad_onnx.py)"
        print(("[PAD] " + msg + " -> mọi phiên sẽ bị từ chối") if REQUIRE_PAD else ("[PAD] CẢNH BÁO: " + msg + " -> CHẠY KHÔNG CHỐNG GIẢ MẠO ẢNH IN/MÀN HÌNH"))

def main():
    global camera_ok
    known_embeddings.update(load_known_embeddings())
    init_models()
    threading.Thread(target=_event_worker, daemon=True).start()
    threading.Thread(target=run_server, daemon=True).start()

    cap = cv2.VideoCapture(CAMERA_SOURCE)
    ctx = None
    print("HỆ THỐNG AI SẴN SÀNG (FastAPI Port 5050)... (Chờ quét thẻ)")

    try:
        while True:
            ret, frame = cap.read()
            camera_ok = bool(ret)
            if not ret:
                time.sleep(0.5)
                continue

            if not ai_state["is_active"]:
                ctx = None   # "chặn cửa": không chạy AI khi chưa có phiên
                if SHOW_WINDOW:
                    cv2.putText(frame, "VUI LONG QUET THE...", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)
                    cv2.imshow('Lab 2FA System', frame)
                    if cv2.waitKey(1) & 0xFF == ord('q'):
                        break
                else:
                    time.sleep(0.03)
                continue

            if ctx is None or ctx.sid != ai_state["session_id"]:
                ctx = SessionCtx(ai_state["session_id"])

            if time.time() > ai_state["timeout_at"]:
                print("!!! [BÁO ĐỘNG] Quá thời gian quy định không nhận diện được mặt hợp lệ.")
                finish("timeout", "timeout", frame)
                continue

            drawn, debug = process_frame(frame, ctx)
            if SHOW_WINDOW:
                draw_overlay(frame, drawn, debug)
                cv2.imshow('Lab 2FA System', frame)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        cv2.destroyAllWindows()

if __name__ == '__main__':
    main()
