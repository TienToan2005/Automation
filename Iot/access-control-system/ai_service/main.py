import base64
from collections import deque
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

import enroll_guide
import enrollment
import liveness
from liveness import LivenessChallenge, PadWindow, PassivePAD, face_ear, face_turn_offset

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
# Backend (Iot/backend): AI đẩy kết quả từng phiên (kèm ảnh nghi phạm). Mặc định trỏ vào backend chạy cùng máy
# với token dev (khớp LAB_SERVICE_TOKEN mặc định của backend) để chạy được ngay. Khi triển khai thật phải đặt
# AI_SERVICE_TOKEN riêng; đặt AI_BACKEND_EVENT_URL="" (rỗng) để tắt hẳn việc gửi.
BACKEND_EVENT_URL = _env("AI_BACKEND_EVENT_URL", "http://127.0.0.1:8080/api/v1/access/verify")   # 127.0.0.1, không dùng localhost (Windows thử IPv6 trước: chậm ~2 giây)
SERVICE_TOKEN = _env("AI_SERVICE_TOKEN", "dev-service-token")   # gửi kèm header X-Service-Token
THINGSBOARD_URL = _env("AI_THINGSBOARD_URL", "")           # vd http://<tb-host>:8080/api/v1/<DEVICE_TOKEN>/telemetry
PAD_MODEL_DIR = _env("AI_PAD_MODEL_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "models"))
# GPU: "auto" (mặc định) = dùng CUDA nếu onnxruntime-gpu có CUDAExecutionProvider, ngược lại chạy CPU; 1 = bắt buộc; 0 = tắt.
GPU_MODE = _env("AI_USE_GPU", "auto").lower()
# Trần bộ nhớ GPU cho vùng đệm (arena) của ONNX Runtime. RTX 3050 chỉ có 4 GB và Windows/trình duyệt đã chiếm ~0.7 GB;
# buffalo_l + 2 model PAD cần khoảng 0.5-1 GB nên 1536 MB là đủ, dư chỗ cho các chương trình khác.
GPU_MEM_MB = int(_env("AI_GPU_MEM_MB", "1536"))
SHOW_WINDOW = _env_bool("AI_SHOW_WINDOW", True)
REQUIRE_PAD = _env_bool("AI_REQUIRE_PAD", True)            # True = thiếu model PAD thì từ chối (fail-closed)
KEEP_CAMERA_OPEN = _env_bool("AI_KEEP_CAMERA_OPEN", False) # False = chỉ mở webcam khi có phiên quét mặt (nhả lúc rảnh)
# ONNX Runtime mặc định dùng hết số luồng CPU và chúng tranh chấp nhau (đo thực tế trên máy 20 luồng:
# mặc định ~1 fps, 4 luồng ~5 fps). Chớp mắt chỉ kéo dài ~0.2s nên cần tốc độ xử lý vài fps trở lên.
ORT_THREADS = int(_env("AI_ORT_THREADS", str(min(4, os.cpu_count() or 4))))   # 0 = để ONNX Runtime tự quyết
# Kích thước ảnh đưa vào bộ phát hiện mặt. 320 nhanh hơn ~3 lần so với 640 (139 ms -> ~40 ms) mà vẫn đủ cho
# webcam vì mặt phải rộng >= AI_MIN_FACE_WIDTH. Tăng lên 640 nếu cần bắt mặt nhỏ/xa (đổi lại chậm hơn).
DET_SIZE = int(_env("AI_DET_SIZE", "320"))
# Độ nét tối thiểu (phương sai Laplacian của vùng mặt, xem enrollment.sharpness) khi đăng ký mặt trên web.
# Giá trị đo thực tế được trả về trong /api/ai/embed ("sharpness") để chỉnh ngưỡng cho webcam của bạn.
MIN_SHARPNESS = float(_env("AI_MIN_SHARPNESS", "60"))
# In mỗi khung hình của phiên quét đăng ký (số đo tư thế, độ nét...) ra console để hiệu chỉnh ngưỡng với webcam của bạn.
ENROLL_DEBUG = _env_bool("AI_ENROLL_DEBUG", False)

SIMILARITY_THRESHOLD = float(_env("AI_SIM_THRESHOLD", "0.50"))
APPEARANCE_JUMP = float(_env("AI_APPEARANCE_JUMP", "0.18"))   # độ khớp nhảy quá mức này (so với trung vị các khung thẳng gần nhất) = diện mạo đổi


def appearance_changed(sim, front_sims, turn_progress):
    """Diện mạo đổi đột ngột lúc nhìn thẳng (bỏ/đeo khẩu trang, kính, tay che...). Cần >= 3 mẫu thẳng để so.
    turn_progress = độ lệch đầu đang đo của thử thách quay (0 nếu không phải thử thách quay): đầu vừa nhúc nhích quay
    (mới được ~1/4 ngưỡng) thì độ khớp tụt là bình thường, không tính là đổi diện mạo."""
    if len(front_sims) < 3 or turn_progress >= liveness.TURN_DELTA * 0.25:
        return False
    return abs(sim - float(np.median(front_sims))) > APPEARANCE_JUMP


def classify_similarity(sim):
    """match | inconclusive | mismatch. Chỉ độ khớp thấp RÕ RỆT mới là mismatch; vùng giữa không buộc tội ai
    (khẩu trang, góc mặt, ánh sáng làm độ khớp tụt nhưng chưa chứng tỏ là người khác)."""
    if sim >= SIMILARITY_THRESHOLD:
        return "match"
    return "mismatch" if sim < MISMATCH_CONFIRM_SIM else "inconclusive"


def face_verifiable(det_score, sharpness):
    """Khung đủ chất lượng để dùng kết luận khớp / không khớp. Khung rất mờ hoặc dò mặt kém tin cậy thì bỏ qua."""
    return det_score >= VERIFY_MIN_DET_SCORE and sharpness >= VERIFY_MIN_SHARPNESS


def pad_verdict(window_full, mean):
    """pending (chưa đủ khung) | spoof (chắc chắn giả) | uncertain (chưa đủ tin cậy, KHÔNG buộc tội) | ok."""
    if not window_full:
        return "pending"
    if mean < SPOOF_CONF_THRESHOLD:
        return "spoof"
    return "uncertain" if mean < PAD_THRESHOLD else "ok"


def classify_timeout(faces_seen, quality_low, inconclusive, pad_uncertain, matched):
    """Hết giờ mà chưa có kết luận: gọi đúng tên điều gì đã xảy ra để backend không tính nhầm là tấn công."""
    if pad_uncertain:
        return "pad_uncertain"
    if faces_seen == 0:
        return "face_not_detected"
    if quality_low * 2 >= faces_seen:
        return "face_quality_low"
    if inconclusive > 0 and not matched:
        return "face_inconclusive"
    return "timeout"


def report_attempt(ctx, reason, frame):
    """Ghi một LẦN THỬ SAI (kèm ảnh) về backend mà KHÔNG đóng phiên: người dùng vẫn quét tiếp được tới hết giờ.
    Giãn cách ATTEMPT_MIN_INTERVAL_S để camera nhanh không làm đầy nhật ký. Trả về True nếu đã gửi."""
    now = time.time()
    if now - ctx.last_attempt_at < ATTEMPT_MIN_INTERVAL_S:
        return False
    ctx.last_attempt_at = now
    with state_lock:
        if not ai_state["is_active"]:
            return False
        event = _build_event(frame, encode_snapshot(frame))
        event.update(result="attempt", reason=reason)
    print(f"[ATTEMPT] {event['student_id']} -> {reason} (chỉ ghi log, phiên vẫn mở) sim={event['confidence_score']}")
    publish_event(event)
    return True


def timeout_reason(ctx):
    if ctx is None:
        return "timeout"
    return classify_timeout(ctx.faces_seen, ctx.quality_low, ctx.inconclusive, ctx.pad_uncertain, ctx.last_match > 0)


def pad_applicable(challenge):
    """PAD chỉ chấm điểm khung nhìn thẳng. Mô hình PAD quen mặt chính diện: lúc đang quay đầu theo thử thách (mặt nghiêng)
    nó cho điểm thấp, nếu vẫn tính thì người thật bị từ chối nhầm là giả mạo. Ảnh in không quay đầu được nên không lợi dụng được."""
    return challenge is None or not challenge.head_turned


def default_skip_frames(source):
    """Số khung đầu bỏ khi mở camera. Camera ESP32 (URL) giữ khung CŨ trong bộ đệm từ phiên trước, và các khung đầu chưa lấy
    sáng làm bộ chống giả (PAD) chấm thấp oan: bỏ vài khung. Webcam trong máy thì không cần."""
    return 0 if isinstance(source, int) else int(_env("AI_CAMERA_SKIP_FRAMES", "3"))
IDENTITY_FRESH_S = 4.0   # thử thách xong phải có khung khớp chủ thẻ trong ngần này giây gần nhất
REQUIRED_FRAMES = 5      # số frame liên tiếp khớp mặt trước khi sang bước thách thức
MISMATCH_FRAMES = 20     # số frame liên tiếp có mặt nhưng sai chủ thể -> từ chối
SCAN_TIMEOUT_S = float(_env("AI_SCAN_TIMEOUT_S", "30"))        # ESP32 chờ SCAN_TIMEOUT_S + 1s (FACE_TIMEOUT_MS)
CHALLENGE_TIMEOUT_S = float(_env("AI_CHALLENGE_TIMEOUT_S", "20"))
MIN_FACE_WIDTH = int(_env("AI_MIN_FACE_WIDTH", "120"))     # "chặn cửa": mặt phải đủ lớn (đứng đúng khoảng cách) mới xử lý
PAD_THRESHOLD = float(_env("AI_PAD_THRESHOLD", "0.70"))    # trung bình điểm mặt thật tối thiểu
PAD_WINDOW = 6
PAD_MIN_FRAMES = 4
# Kết luận chỉ khi có BẰNG CHỨNG. Mọi ngưỡng cần hiệu chỉnh bằng dữ liệu thật (tools/eval_far_frr.py), đây là giá trị khởi điểm.
MISMATCH_CONFIRM_SIM = float(_env("AI_MISMATCH_CONFIRM_SIM", "0.35"))      # độ khớp DƯỚI mức này mới là "sai chủ thẻ" đã xác nhận;
#                                                                            giữa MISMATCH_CONFIRM_SIM và SIMILARITY_THRESHOLD = chưa kết luận
SPOOF_CONF_THRESHOLD = float(_env("AI_SPOOF_CONF_THRESHOLD", "0.40"))      # điểm PAD TB dưới mức này mới là giả mạo chắc chắn (spoof_pad);
#                                                                            giữa mức này và PAD_THRESHOLD = chưa đủ tin cậy (không buộc tội)
# Cổng chất lượng khi quét cửa NHẸ hơn lúc đăng ký: số đo thật trên camera ESP32 qua WiFi là độ nét 9-39, dò mặt 0.62-0.76
ATTEMPT_MIN_INTERVAL_S = float(_env("AI_ATTEMPT_MIN_INTERVAL_S", "5"))     # giãn cách giữa hai lần ghi "sai mặt" của cùng một phiên
VERIFY_MIN_DET_SCORE = float(_env("AI_VERIFY_MIN_DET_SCORE", "0.50"))
VERIFY_MIN_SHARPNESS = float(_env("AI_VERIFY_MIN_SHARPNESS", "8"))
SNAPSHOT_WIDTH = 480
# ========================================================================================

state_lock = threading.Lock()
infer_lock = threading.Lock()   # tuần tự hoá face_app.get giữa vòng lặp camera và API /embed (tránh đỉnh VRAM)
active_providers = []

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
    "ended_at": 0,           # thời điểm phiên gần nhất kết thúc (để cửa sổ camera hiện kết quả vài giây)
}
known_embeddings = {}
face_app = None
pad = None
camera_ok = False
enroll_sessions = enroll_guide.SessionStore()   # phiên quét có hướng dẫn của trang đăng ký web (xem /api/ai/enroll/*)
enroll_lock = threading.Lock()                  # tuần tự hoá thao tác lên một phiên quét (khung hình và lệnh có thể đến song song)


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

ALERT_LEVEL = {"spoof_pad": "high", "challenge_failed": "medium", "face_mismatch": "medium"}   # khớp mức của backend: chỉ PAD chắc chắn mới là HIGH
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
                r = requests.post(BACKEND_EVENT_URL, json=ev, headers=headers, timeout=3)
                if r.status_code >= 400:
                    print(f"[EVENT] Backend từ chối phiên {ev['session_id']}: HTTP {r.status_code} {r.text[:120]} "
                          f"(kiểm tra AI_SERVICE_TOKEN khớp LAB_SERVICE_TOKEN)")
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
        ai_state.update(is_active=False, result=result, reason=reason, phase="done", hint="", ended_at=time.time())
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
        "gpu": "CUDAExecutionProvider" in active_providers,
        "providers": active_providers,
        "gpu_mem_limit_mb": GPU_MEM_MB if "CUDAExecutionProvider" in active_providers else None,
        "local_enrolled_count": len(known_embeddings),
        "session_active": ai_state["is_active"],
        "thresholds": {"similarity": SIMILARITY_THRESHOLD, "pad": PAD_THRESHOLD, "min_face_width": MIN_FACE_WIDTH},
    }

@api_app.post("/api/ai/embed")
def embed_image(request: EmbedRequest):
    """Web đăng ký người dùng (qua Backend): ảnh -> kiểm tra (1 mặt, đủ lớn, đủ nét, không giả) -> vector 512 chiều.

    Luôn trả HTTP 200 với {"ok": bool, "reason": ..., "embedding": [512 float] khi ok}; ảnh không giải mã được -> 400."""
    if face_app is None:
        raise HTTPException(status_code=503, detail="Model not ready")
    try:
        img = enrollment.decode_image(request.image_base64)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image")
    with infer_lock:
        return enrollment.analyze_image(face_app, pad, img, MIN_FACE_WIDTH, PAD_THRESHOLD, MIN_SHARPNESS)

class EnrollFrameRequest(BaseModel):
    session_id: str
    image_base64: Optional[str] = None        # khung hình JPEG/PNG; bỏ trống khi chỉ gửi lệnh
    command: Optional[str] = None             # "retry" (làm lại bước hiện tại) | "skip" (bỏ qua bước chớp mắt)

@api_app.post("/api/ai/enroll/start")
def enroll_start():
    """Web đăng ký (qua Backend): mở phiên quét có hướng dẫn nhìn thẳng -> quay trái -> quay phải -> chớp mắt."""
    if face_app is None:
        raise HTTPException(status_code=503, detail="Model not ready")
    sid, guide = enroll_sessions.start()
    return {"session_id": sid, **guide.snapshot()}

@api_app.post("/api/ai/enroll/frame")
def enroll_frame(request: EnrollFrameRequest):
    """Một khung hình của phiên quét -> trạng thái bước hiện tại (enroll_guide.GuidedEnroll.snapshot) kèm "hint" (mã gợi ý),
    "quality" (số đo của khung) và "passed_step": khác null nghĩa là KHUNG NÀY vừa hoàn thành bước đó, bên gọi giữ lại ảnh
    làm ảnh đăng ký. 404 = phiên không có hoặc đã hết hạn; 400 = lệnh/ảnh không hợp lệ."""
    if face_app is None:
        raise HTTPException(status_code=503, detail="Model not ready")
    guide = enroll_sessions.get(request.session_id)
    if guide is None:
        raise HTTPException(status_code=404, detail="Unknown or expired session")
    with enroll_lock:
        if request.command == "retry":
            guide.retry()
        elif request.command == "skip":
            if not guide.skip():
                raise HTTPException(status_code=400, detail="Only the blink step can be skipped")
        elif request.command:
            raise HTTPException(status_code=400, detail="Unknown command")
    if request.image_base64 is None:
        return guide.snapshot()
    try:
        img = enrollment.decode_image(request.image_base64)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image")
    with infer_lock:
        result, face = enrollment.inspect_image(face_app, pad, img, MIN_FACE_WIDTH, PAD_THRESHOLD, MIN_SHARPNESS)
    with enroll_lock:
        out = guide.update(result["reason"], face, (img.shape[1], img.shape[0]))
    out["quality"] = {k: result[k] for k in ("face_width", "det_score", "sharpness", "pad_score") if k in result}
    pose = {}
    if face is not None:
        offset, ear = face_turn_offset(face), face_ear(face)
        if offset is not None:
            pose["offset"] = round(offset, 3)      # độ lệch mũi (dương = sang trái của người dùng): thước đo quay đầu
        if ear is not None:
            pose["ear"] = round(ear, 3)            # độ mở mắt (EAR): thước đo chớp mắt
    out["pose"] = pose
    if ENROLL_DEBUG:
        print(f"[ENROLL] bước={out['step']} khung={result['reason']} gợi ý={out['hint'] or '-'} đạt={out['passed_step'] or '-'} "
              f"offset={pose.get('offset')} ear={pose.get('ear')} {out['quality']}")
    return out

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
        self.last_match = 0.0     # lần cuối thấy mặt khớp chủ thẻ (khi nhìn thẳng)
        self.faces_seen = 0       # số khung có đúng một mặt đủ lớn (để phân loại khi hết giờ)
        self.quality_low = 0      # trong đó số khung quá mờ / dò mặt kém tin cậy
        self.inconclusive = 0     # số khung có độ khớp ở vùng "chưa kết luận"
        self.pad_uncertain = False
        self.last_attempt_at = 0.0   # lần gần nhất ghi "sai mặt" (giãn cách để không làm đầy nhật ký)
        self.frame_times = deque(maxlen=20)   # thời điểm các khung gần nhất, để ước lượng fps thật (chọn thử thách)
        self.front_sims = deque(maxlen=6)   # độ khớp các khung nhìn thẳng gần nhất, để phát hiện diện mạo đổi đột ngột

    def fps(self):
        if len(self.frame_times) < 5:
            return None
        span = self.frame_times[-1] - self.frame_times[0]
        return (len(self.frame_times) - 1) / span if span > 0 else None

    def reset_liveness(self):
        self.pad.clear()
        self.challenge = None
        self.match = 0
        with state_lock:
            ai_state.update(challenge=None, phase="verify", pad_score=0.0)

def process_frame(frame, ctx):
    """Một bước xử lý cho phiên đang active. Trả về (faces_vẽ, chú thích debug)."""
    ctx.frame_times.append(time.time())
    with infer_lock:
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
    ctx.faces_seen += 1
    if not face_verifiable(float(face.det_score), enrollment.sharpness(frame, face.bbox)):
        ctx.quality_low += 1              # không dùng khung kém để buộc tội hay xác nhận ai
        ctx.match = 0
        with state_lock:
            ai_state["hint"] = "quality_low"
        drawn.append((face, False, "Anh kem"))
        return drawn, ""
    emb = ai_state["embedding"]
    sim = compute_similarity(face.normed_embedding, emb)
    with state_lock:
        ai_state["similarity"] = max(ai_state["similarity"], sim)

    # Đang quay đầu theo thử thách: mặt nghiêng làm độ khớp tụt, không được tính là "sai chủ thể".
    # Danh tính đã được xác nhận lúc nhìn thẳng (ctx.last_match) và được kiểm tra lại khi thử thách xong.
    turned = ctx.challenge is not None and ctx.challenge.head_turned
    verdict = classify_similarity(sim)
    if verdict == "inconclusive" and not turned:
        ctx.inconclusive += 1             # chưa kết luận: không đếm sai chủ thẻ, không làm hỏng thử thách đang chạy
        ctx.match = 0
        drawn.append((face, True, f"Chua ro ({sim:.2f})"))
        return drawn, ""
    if verdict == "mismatch" and not turned:
        drawn.append((face, False, f"Sai chu the ({sim:.2f})"))
        ctx.mismatch += 1
        with state_lock:
            ai_state["hint"] = "mismatch"                # OLED báo "SAI MAT", người dùng thử lại ngay
        if ctx.challenge is not None or len(ctx.pad.scores):
            ctx.reset_liveness()      # đổi người giữa chừng -> làm lại từ đầu
        ctx.match = 0
        if ctx.mismatch >= MISMATCH_FRAMES:
            report_attempt(ctx, "face_mismatch", frame)  # chỉ ghi log, KHÔNG đóng phiên
            ctx.mismatch = 0                             # mỗi lần thử cần thêm MISMATCH_FRAMES khung xác nhận
        return drawn, ""

    # Diện mạo đổi đột ngột lúc nhìn thẳng (bỏ / đeo khẩu trang, bỏ kính, tay che rồi mở...): mốc tư thế đầu của thử thách
    # đã lấy trong điều kiện khác nên không còn đáng tin -> làm lại thử thách với mốc mới.
    if not turned:
        progress = abs(ctx.challenge.debug) if ctx.challenge is not None and ctx.challenge.type != "blink" else 0.0
        if ctx.challenge is not None and appearance_changed(sim, ctx.front_sims, progress):
            print(f"[LIVENESS] Diện mạo đổi đột ngột (độ khớp {np.median(ctx.front_sims):.2f} -> {sim:.2f}), làm lại thử thách")
            ctx.reset_liveness()
            ctx.front_sims.clear()
        ctx.front_sims.append(sim)
    if sim >= SIMILARITY_THRESHOLD:
        ctx.mismatch = 0
        ctx.match += 1
        ctx.last_match = time.time()
        drawn.append((face, True, f"Trung khop ({sim:.2f})"))
    else:
        drawn.append((face, True, f"Dang quay dau ({sim:.2f})"))

    # ----- Mức 2: PAD thụ động trên cùng khuôn mặt đã khớp -----
    if pad.enabled:
        if pad_applicable(ctx.challenge):
            ctx.pad.add(pad.score(frame, face.bbox))
            with state_lock:
                ai_state["pad_score"] = ctx.pad.mean()
            verdict = pad_verdict(ctx.pad.full(), ctx.pad.mean())
            if verdict == "spoof":
                finish("denied", "spoof_pad", frame)
                return drawn, ""
            ctx.pad_uncertain = verdict == "uncertain"      # chưa đủ tin cậy: không buộc tội, nhưng cũng không cho đi tiếp
        pad_ok = ctx.pad.ready() and ctx.pad.mean() >= PAD_THRESHOLD
    else:
        pad_ok = not REQUIRE_PAD   # chế độ dev: AI_REQUIRE_PAD=0 và không có model

    # ----- Mức 1: thách thức ngẫu nhiên -----
    debug = ""
    if ctx.challenge is None:
        if ctx.match >= REQUIRED_FRAMES and pad_ok:
            ctx.challenge = LivenessChallenge(timeout_s=min(CHALLENGE_TIMEOUT_S, max(1.0, ai_state["timeout_at"] - time.time())),
                                              fps=ctx.fps())
            with state_lock:
                ai_state.update(phase="challenge", challenge={
                    "type": ctx.challenge.type, "text": ctx.challenge.text,
                    "text_vi": ctx.challenge.text_vi, "deadline": ctx.challenge.deadline})
            print(f"[LIVENESS] Thách thức: {ctx.challenge.type}")
    else:
        status = ctx.challenge.update(face)
        debug = f"{ctx.challenge.type}: {ctx.challenge.debug:+.2f}"
        if status == "passed":
            if time.time() - ctx.last_match <= IDENTITY_FRESH_S:
                finish("granted", "ok", frame)
            else:                 # quay xong nhưng đã lâu không thấy mặt khớp chủ thẻ: làm lại thử thách
                print("[LIVENESS] Đạt thử thách nhưng chưa xác nhận lại danh tính, làm lại")
                ctx.reset_liveness()
        elif status == "failed":
            finish("denied", "challenge_failed", frame)
    return drawn, debug

RESULT_BANNER_S = 4.0
BANNER_REASON = {
    "face_mismatch": "SAI KHUON MAT", "spoof_pad": "PHAT HIEN GIA MAO", "challenge_failed": "KHONG QUA THU THACH",
    "not_enrolled": "CHUA DANG KY MAT", "pad_unavailable": "THIEU MODEL CHONG GIA MAO", "timeout": "HET GIO",
}

def result_banner(img):
    """Vẽ kết quả của phiên vừa xong lên cửa sổ chờ, để người dùng biết hệ thống đã xử lý xong
    (sau phiên AI nhả camera nên nếu không có dòng này thì cửa sổ chỉ đen và trông như bị lỗi)."""
    if time.time() - ai_state["ended_at"] > RESULT_BANNER_S:
        return
    result, reason = ai_state["result"], ai_state["reason"]
    if result == "granted":
        lines, color = ["HOP LE", "Dang gui ket qua de mo cua..."], (0, 255, 0)
    elif result == "denied":
        lines, color = ["TU CHOI", BANNER_REASON.get(reason, reason.upper())], (0, 0, 255)
    elif result == "timeout":
        lines, color = ["HET GIO", "Khong nhan dien duoc khuon mat"], (0, 165, 255)
    else:
        return
    img[:] = 0
    cv2.putText(img, lines[0], (20, 55), cv2.FONT_HERSHEY_SIMPLEX, 1.6, color, 3)
    cv2.putText(img, lines[1], (20, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

# Chữ hiển thị trên cửa sổ camera cho mỗi gợi ý (hint) mà process_frame có thể đặt. Thêm gợi ý mới thì thêm ở đây.
HINT_TEXT = {"no_face": "KHONG THAY MAT", "move_closer": "LAI GAN HON", "multiple_faces": "CHI 1 NGUOI TRONG KHUNG",
             "quality_low": "ANH KEM: THEM SANG", "mismatch": "SAI KHUON MAT: THU LAI"}


def draw_overlay(frame, drawn, debug, fps=0.0):
    # Hiển thị kiểu soi gương: "phải" của người dùng nằm bên phải màn hình, đúng với chữ "QUAY DAU SANG PHAI".
    # Chỉ lật hình để hiển thị; việc phân tích luôn dùng khung hình gốc.
    w = frame.shape[1]
    frame[:] = cv2.flip(frame, 1)
    for face, ok, label in drawn:
        x1, y1, x2, y2 = map(int, face.bbox)
        x1, x2 = w - x2, w - x1
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
        cv2.putText(frame, HINT_TEXT.get(s["hint"], str(s["hint"]).upper()), (20, 110),    # gợi ý lạ vẫn vẽ được, không bao giờ sập
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 200, 255), 2)
    if debug:
        cv2.putText(frame, debug, (20, frame.shape[0] - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)
    cv2.putText(frame, f"{fps:.1f} fps", (frame.shape[1] - 110, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                (0, 255, 0) if fps >= 3 else (0, 0, 255), 2)

def init_models():
    global face_app, pad
    import onnxruntime as ort
    providers = ["CPUExecutionProvider"]
    ctx_id = -1
    if GPU_MODE != "0":
        # onnxruntime-gpu <= 1.26 build cho CUDA 12 + cuDNN 9. Nếu có torch+cu12x thì import trước để các DLL
        # CUDA/cuDNN trong torch/lib được nạp sẵn vào tiến trình (Windows dùng lại module cùng tên), khỏi cài riêng CUDA.
        try:
            import torch  # noqa: F401
        except Exception:
            pass
        if hasattr(ort, "preload_dlls"):      # ORT >= 1.21: nạp thêm CUDA/cuDNN từ gói nvidia-* pip nếu có
            try:
                ort.preload_dlls()
            except Exception:
                pass
        if "CUDAExecutionProvider" in ort.get_available_providers():
            # kSameAsRequested: không nhân đôi vùng đệm; HEURISTIC: không thử nhiều thuật toán conv (đỡ đỉnh VRAM)
            cuda = ("CUDAExecutionProvider", {
                "device_id": 0,
                "gpu_mem_limit": GPU_MEM_MB * 1024 * 1024,
                "arena_extend_strategy": "kSameAsRequested",
                "cudnn_conv_algo_search": "HEURISTIC",
            })
            providers, ctx_id = [cuda, "CPUExecutionProvider"], 0
        else:
            print("[GPU] Không có CUDAExecutionProvider (`pip uninstall -y onnxruntime && pip install onnxruntime-gpu==1.26.0`) -> chạy CPU")
    so = ort.SessionOptions()
    if ORT_THREADS > 0:                      # xem chú thích ORT_THREADS ở phần cấu hình
        so.intra_op_num_threads, so.inter_op_num_threads = ORT_THREADS, 1
        print(f"[ORT] Giới hạn {ORT_THREADS} luồng cho mỗi model")
    # Chỉ nạp module cần dùng: phát hiện, nhúng khuôn mặt, landmark 3D (thách thức). Bỏ landmark 2D 106 điểm
    # và đoán tuổi/giới tính, giảm ~35% thời gian mỗi khung hình. Batch luôn = 1 (mỗi lần một khung/ảnh).
    # Không dùng FP16: model là FP32 ~vài trăm MB, batch 1 nên không cần; đổi sang FP16 còn làm lệch vector
    # so với các vector đã lưu trong DB.
    face_app = FaceAnalysis(name='buffalo_l', providers=providers, sess_options=so,
                            allowed_modules=['detection', 'recognition', 'landmark_3d_68'])
    face_app.prepare(ctx_id=ctx_id, det_size=(DET_SIZE, DET_SIZE))
    pad = PassivePAD(PAD_MODEL_DIR, providers, sess_options=so)
    used = {p for m in face_app.models.values() for p in m.session.get_providers()}
    active_providers[:] = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider") if p in used]
    print(f"[ORT] Provider đang chạy: {active_providers}")
    if GPU_MODE == "1" and "CUDAExecutionProvider" not in active_providers:
        print("[GPU] CẢNH BÁO: AI_USE_GPU=1 nhưng CUDA không khởi tạo được (thiếu cuDNN/CUDA DLL?) -> đang chạy CPU")
    if not pad.enabled:
        msg = "KHÔNG có model PAD trong " + PAD_MODEL_DIR + " (xem tools/export_pad_onnx.py)"
        print(("[PAD] " + msg + " -> mọi phiên sẽ bị từ chối") if REQUIRE_PAD else ("[PAD] CẢNH BÁO: " + msg + " -> CHẠY KHÔNG CHỐNG GIẢ MẠO ẢNH IN/MÀN HÌNH"))
    warmup_models()


def warmup_models():
    """Chạy thử mỗi model một lần ngay lúc khởi động. Trên GPU, lần gọi đầu của mỗi model mất vài giây (cuDNN chọn
    thuật toán, cấp phát vùng đệm); nếu để phiên quét thẻ đầu tiên gánh thì cửa sổ chớp mắt/hết giờ bị tiêu hao."""
    t0 = time.time()
    blank = np.zeros((DET_SIZE, DET_SIZE, 3), np.uint8)
    face_app.get(blank)                                           # bộ phát hiện (đầu vào kích thước động)
    for m in face_app.models.values():                            # nhúng + landmark có đầu vào tĩnh
        inp = m.session.get_inputs()[0]
        if all(isinstance(d, int) for d in inp.shape):
            m.session.run(None, {inp.name: np.zeros(inp.shape, np.float32)})
    if pad.enabled:
        quarter = DET_SIZE // 4
        pad.score(blank, (quarter, quarter, DET_SIZE - quarter, DET_SIZE - quarter))
    print(f"[WARMUP] Làm nóng model xong sau {time.time() - t0:.1f}s")

class CameraStream:
    """Đọc camera ở luồng riêng, luôn giữ khung hình MỚI NHẤT.

    Nếu đọc ngay trong vòng lặp xử lý thì khi AI chậm, OpenCV/driver xếp hàng khung hình cũ:
    hình trễ vài giây và cú chớp mắt bị rơi mất."""

    def __init__(self, source, skip=None, reopen_after_s=None):
        self._source = source
        self._skip = default_skip_frames(source) if skip is None else skip     # số khung đầu còn phải bỏ
        # Nguồn URL (ESP32) im lặng quá lâu (board khởi động lại, WiFi đứt) thì mở lại luồng thay vì chờ ffmpeg 30 giây
        self._reopen_after = float(_env("AI_CAMERA_REOPEN_S", "5")) if reopen_after_s is None else reopen_after_s
        # Backend MSMF mặc định của OpenCV trên Windows mất ~6 s để ra khung hình đầu tiên (đo trên máy này),
        # DirectShow ~1 s. Camera được mở lại ở MỖI phiên quét nên 6 s đó trừ thẳng vào thời gian chờ.
        # Đặt AI_CAMERA_BACKEND=default để dùng lại backend mặc định. Nguồn là URL (ESP32-CAM) thì không đổi.
        self.cap = self._open()
        self.ok = False
        self.frame, self.seq = None, 0
        self._lock = threading.Lock()
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _open(self):
        source = self._source
        if os.name == "nt" and isinstance(source, int) and _env("AI_CAMERA_BACKEND", "dshow").lower() == "dshow":
            return cv2.VideoCapture(source, cv2.CAP_DSHOW)
        if isinstance(source, str):       # URL: không treo 30 giây khi board không trả lời, tối đa 5 giây mỗi lần đọc
            return cv2.VideoCapture(source, cv2.CAP_FFMPEG, [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000])
        return cv2.VideoCapture(source)

    def _run(self):
        try:
            self._read_loop()
        finally:
            self.cap.release()      # CHỈ luồng này được đóng camera: đóng từ luồng khác lúc read() chưa trả về làm ffmpeg assert và sập cả AI

    def _read_loop(self):
        silent_since = None
        while not self._stop:
            ret, frame = self.cap.read()
            self.ok = bool(ret)
            if ret:
                silent_since = None
                if self._skip > 0:
                    self._skip -= 1                    # khung cũ / chưa lấy sáng: bỏ
                else:
                    with self._lock:
                        self.frame, self.seq = frame, self.seq + 1
                continue
            now = time.time()
            silent_since = silent_since or now
            if isinstance(self._source, str) and now - silent_since >= self._reopen_after and not self._stop:
                print(f"[CAMERA] Luồng im lặng {now - silent_since:.1f}s, mở lại kết nối tới camera")
                self.cap.release()
                self.cap = self._open()
                self._skip = default_skip_frames(self._source)
                silent_since = None
            else:
                time.sleep(0.1)

    def latest(self):
        with self._lock:
            return self.frame, self.seq

    def wait_first_frame(self, timeout=3.0):
        t0 = time.time()
        while self.frame is None and time.time() - t0 < timeout:
            time.sleep(0.05)
        return self.frame is not None

    def release(self):
        """Báo luồng đọc dừng rồi chờ tối đa 2 s. KHÔNG tự đóng camera ở đây: nếu read() còn kẹt (WiFi chậm) thì luồng đọc
        sẽ tự đóng ngay khi read() trả về."""
        self._stop = True
        self._thread.join(timeout=2)


def main():
    global camera_ok
    known_embeddings.update(load_known_embeddings())
    init_models()
    if not BACKEND_EVENT_URL:
        print("[CẢNH BÁO] AI_BACKEND_EVENT_URL đang rỗng: kết quả quét mặt KHÔNG được gửi về backend, "
              "ESP32 sẽ không bao giờ nhận được kết quả.")
    else:
        print(f"[BACKEND] Gửi kết quả về {BACKEND_EVENT_URL}"
              + ("  (đang dùng token dev mặc định)" if SERVICE_TOKEN == "dev-service-token" else ""))
    threading.Thread(target=_event_worker, daemon=True).start()
    threading.Thread(target=run_server, daemon=True).start()

    # Webcam Windows chỉ cho một chương trình dùng tại một thời điểm. Mặc định chỉ mở camera khi
    # có phiên quét mặt, nhờ vậy trình duyệt (trang đăng ký khuôn mặt) dùng được camera lúc rảnh.
    stream = CameraStream(CAMERA_SOURCE)
    stream.wait_first_frame()
    camera_ok = stream.ok                                  # thăm dò một lần để /health báo đúng
    if not KEEP_CAMERA_OPEN:
        stream.release()
        stream = None
    ctx = None
    last_seq = -1
    n_frames, t_first = 0, 0.0                             # đếm khung hình đã xử lý trong phiên để báo fps
    last_frame = None                                      # khung hình cuối của phiên, đính kèm khi hết giờ
    idle_frame = np.zeros((120, 640, 3), np.uint8)
    cv2.putText(idle_frame, "VUI LONG QUET THE...", (50, 70), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)
    print("HỆ THỐNG AI SẴN SÀNG (FastAPI Port 5050)... (Chờ quét thẻ)"
          + ("" if KEEP_CAMERA_OPEN else " Camera chỉ mở khi có phiên quét mặt."))

    try:
        while True:
            if not ai_state["is_active"]:
                if n_frames:
                    dur = max(time.time() - t_first, 1e-6)
                    print(f"[PERF] Phiên vừa xong: xử lý {n_frames} khung hình trong {dur:.1f}s = {n_frames / dur:.1f} fps"
                          + ("  <-- QUÁ CHẬM, chớp mắt dễ bị bỏ lỡ" if n_frames / dur < 2 else ""))
                    n_frames = 0
                ctx, last_frame = None, None   # "chặn cửa": không chạy AI khi chưa có phiên
                if stream is not None and not KEEP_CAMERA_OPEN:
                    stream.release()                   # nhả camera cho chương trình khác
                    stream = None
                if SHOW_WINDOW:
                    idle = idle_frame.copy()
                    if stream is not None:             # chỉ xảy ra khi AI_KEEP_CAMERA_OPEN=1
                        frame, _ = stream.latest()
                        camera_ok = stream.ok
                        if frame is not None:
                            idle = frame.copy()
                            cv2.putText(idle, "VUI LONG QUET THE...", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)
                    result_banner(idle[:120] if stream is None else idle)
                    cv2.imshow('Lab 2FA System', idle)
                    if cv2.waitKey(30) & 0xFF == ord('q'):
                        break
                else:
                    time.sleep(0.05)
                continue

            if ctx is None or ctx.sid != ai_state["session_id"]:
                ctx = SessionCtx(ai_state["session_id"])

            # Kiểm tra hết giờ TRƯỚC khi đọc camera: camera lỗi vẫn phải kết thúc phiên để báo về backend
            if time.time() > ai_state["timeout_at"]:
                print("!!! [BÁO ĐỘNG] Quá thời gian quy định không nhận diện được mặt hợp lệ.")
                finish("timeout", timeout_reason(ctx), last_frame)
                continue

            if stream is None:
                stream, last_seq = CameraStream(CAMERA_SOURCE), -1   # mở lại khi có phiên mới
            frame, seq = stream.latest()
            camera_ok = stream.ok
            if frame is None or seq == last_seq:       # chưa có khung hình mới: không xử lý lại khung cũ
                if SHOW_WINDOW:
                    cv2.waitKey(1)
                time.sleep(0.005)
                continue
            last_seq = seq

            last_frame = frame
            if n_frames == 0:
                t_first = time.time()
            drawn, debug = process_frame(frame, ctx)
            n_frames += 1
            if SHOW_WINDOW:
                fps = n_frames / max(time.time() - t_first, 1e-6) if n_frames > 1 else 0.0
                try:
                    draw_overlay(frame, drawn, debug, fps)
                except Exception as e:      # chỉ là phần HIỂN THỊ: lỗi vẽ không được làm chết dịch vụ xác thực
                    if not getattr(main, "_overlay_error_shown", False):
                        print(f"[CẢNH BÁO] Lỗi vẽ cửa sổ camera (bỏ qua, nhận diện vẫn chạy): {type(e).__name__}: {e}")
                        main._overlay_error_shown = True
                cv2.imshow('Lab 2FA System', frame)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
    except KeyboardInterrupt:
        pass
    finally:
        if stream is not None:
            stream.release()
        cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
