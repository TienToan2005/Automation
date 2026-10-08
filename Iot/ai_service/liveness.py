"""Kiểm tra người thật (Presentation Attack Detection) cho hệ thống kiểm soát ra vào.

Mức 1 - LivenessChallenge: thách thức ngẫu nhiên (quay đầu trái/phải hoặc chớp mắt),
         dựa trên 68 landmark 3D của InsightFace -> chặn ảnh tĩnh.
Mức 2 - PassivePAD: mô hình MiniFASNet (Silent-Face-Anti-Spoofing, định dạng ONNX),
         chấm điểm "mặt thật" trên từng frame -> chặn ảnh in / ảnh-video trên màn hình.
"""
import glob
import os
import secrets
import time
from collections import deque

import cv2
import numpy as np


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


# ---------- Tham số (có thể chỉnh bằng biến môi trường khi hiệu chỉnh với camera thật) ----------
TURN_DELTA = _env_float("AI_TURN_DELTA", 0.12)           # độ lệch mũi so với tâm hai mắt (đơn vị: khoảng cách hai mắt)
TURN_HOLD_FRAMES = 2                                     # số frame liên tiếp phải giữ tư thế quay đầu
YAW_LEFT_SIGN = 1 if _env_float("AI_YAW_LEFT_SIGN", 1) >= 0 else -1  # đặt -1 nếu camera bị lật gương
BLINK_CLOSE_RATIO = _env_float("AI_BLINK_CLOSE_RATIO", 0.70)  # EAR < 70% mức mắt mở -> mắt nhắm
BLINK_OPEN_RATIO = _env_float("AI_BLINK_OPEN_RATIO", 0.85)    # EAR > 85% mức mắt mở -> mắt mở lại
BASELINE_FRAMES = 3                                      # số frame đầu để lấy tư thế trung tính

CHALLENGES = {
    "turn_left": ("QUAY DAU SANG TRAI", "Quay đầu sang trái"),
    "turn_right": ("QUAY DAU SANG PHAI", "Quay đầu sang phải"),
    "blink": ("HAY CHOP MAT", "Hãy chớp mắt"),
}


# ---------- Đặc trưng hình học từ landmark 68 điểm (iBUG) ----------
def eye_aspect_ratio(pts):
    """pts: 6 điểm của một mắt theo thứ tự iBUG."""
    a = np.linalg.norm(pts[1] - pts[5])
    b = np.linalg.norm(pts[2] - pts[4])
    c = np.linalg.norm(pts[0] - pts[3])
    return float((a + b) / (2.0 * c + 1e-6))


def face_ear(face):
    lm = getattr(face, "landmark_3d_68", None)
    if lm is None:
        return None
    lm = lm[:, :2]
    return (eye_aspect_ratio(lm[36:42]) + eye_aspect_ratio(lm[42:48])) / 2.0


def face_turn_offset(face):
    """Độ lệch ngang của chóp mũi so với trung điểm hai khóe mắt ngoài, chuẩn hóa theo khoảng cách hai mắt.

    Camera không lật gương: dương = người dùng quay sang TRÁI của họ (mũi dịch về phía bên phải ảnh).
    """
    lm = getattr(face, "landmark_3d_68", None)
    if lm is None:
        return None
    nose_x = lm[30, 0]
    eye_mid = (lm[36, 0] + lm[45, 0]) / 2.0
    iod = abs(lm[45, 0] - lm[36, 0]) + 1e-6
    return float(YAW_LEFT_SIGN * (nose_x - eye_mid) / iod)


# ---------- Mức 1: thách thức ngẫu nhiên ----------
class LivenessChallenge:
    """Một thách thức chọn ngẫu nhiên (secrets) cho mỗi phiên. update() trả về pending|passed|failed."""

    def __init__(self, timeout_s=8.0, ctype=None):
        self.type = ctype or secrets.choice(list(CHALLENGES))
        self.text, self.text_vi = CHALLENGES[self.type]
        self.deadline = time.time() + timeout_s
        self._baseline_samples = []
        self._baseline = None
        self._hold = 0
        # chớp mắt
        self._ear_ref = 0.0
        self._ear_n = 0
        self._closed = False
        self.debug = 0.0   # giá trị đang đo, hiển thị lên khung hình để hiệu chỉnh

    def time_left(self):
        return max(0, int(self.deadline - time.time()))

    def update(self, face):
        if time.time() > self.deadline:
            return "failed"
        if self.type == "blink":
            return self._update_blink(face)
        return self._update_turn(face)

    def _update_turn(self, face):
        off = face_turn_offset(face)
        if off is None:
            return "pending"
        if self._baseline is None:
            self._baseline_samples.append(off)
            if len(self._baseline_samples) >= BASELINE_FRAMES:
                self._baseline = float(np.median(self._baseline_samples))
            return "pending"
        delta = off - self._baseline
        self.debug = delta
        want_left = self.type == "turn_left"
        ok = delta >= TURN_DELTA if want_left else delta <= -TURN_DELTA
        self._hold = self._hold + 1 if ok else 0
        return "passed" if self._hold >= TURN_HOLD_FRAMES else "pending"

    def _update_blink(self, face):
        ear = face_ear(face)
        if ear is None:
            return "pending"
        self.debug = ear
        self._ear_n += 1
        # mức "mắt mở" = đỉnh gần nhất, giảm chậm để thích nghi
        self._ear_ref = max(ear, self._ear_ref * 0.98)
        if self._ear_n <= BASELINE_FRAMES + 2:
            return "pending"
        if not self._closed and ear < BLINK_CLOSE_RATIO * self._ear_ref:
            self._closed = True
        elif self._closed and ear > BLINK_OPEN_RATIO * self._ear_ref:
            return "passed"          # nhắm rồi mở lại = một lần chớp
        return "pending"


# ---------- Mức 2: mô hình PAD thụ động (MiniFASNet, ONNX) ----------
def _crop_scaled(img, bbox, scale, out_w, out_h):
    """Cắt vùng quanh mặt nới rộng theo `scale` (đúng cách tiền xử lý của Silent-Face-Anti-Spoofing)."""
    src_h, src_w = img.shape[:2]
    x1, y1, x2, y2 = [float(v) for v in bbox]
    box_w, box_h = max(x2 - x1, 1.0), max(y2 - y1, 1.0)
    scale = min((src_h - 1) / box_h, (src_w - 1) / box_w, scale)
    new_w, new_h = box_w * scale, box_h * scale
    cx, cy = x1 + box_w / 2, y1 + box_h / 2
    lx, ly = cx - new_w / 2, cy - new_h / 2
    rx, ry = cx + new_w / 2, cy + new_h / 2
    if lx < 0:
        rx -= lx
        lx = 0
    if ly < 0:
        ry -= ly
        ly = 0
    if rx > src_w - 1:
        lx -= rx - (src_w - 1)
        rx = src_w - 1
    if ry > src_h - 1:
        ly -= ry - (src_h - 1)
        ry = src_h - 1
    crop = img[int(max(ly, 0)):int(ry) + 1, int(max(lx, 0)):int(rx) + 1]
    return cv2.resize(crop, (out_w, out_h))


class PassivePAD:
    """Nạp mọi file `<scale>_<H>x<W>_<tên>.onnx` trong model_dir và lấy trung bình xác suất 'mặt thật'."""

    def __init__(self, model_dir, providers=None):
        import onnxruntime as ort
        self.models = []   # (session, input_name, scale, w, h)
        for path in sorted(glob.glob(os.path.join(model_dir, "*.onnx"))):
            parts = os.path.basename(path)[:-5].split("_")
            try:
                scale = float(parts[0])
                h, w = [int(v) for v in next(p for p in parts if "x" in p and p.replace("x", "").isdigit()).split("x")]
            except (ValueError, StopIteration):
                print(f"[PAD] Bỏ qua file không đúng định dạng tên: {path}")
                continue
            sess = ort.InferenceSession(path, providers=providers or ["CPUExecutionProvider"])
            self.models.append((sess, sess.get_inputs()[0].name, scale, w, h))
            print(f"[PAD] Đã nạp {os.path.basename(path)} (scale={scale}, {w}x{h})")

    @property
    def enabled(self):
        return bool(self.models)

    @property
    def names(self):
        return [f"{m[2]}_{m[4]}x{m[3]}" for m in self.models]

    def score(self, frame, bbox):
        """Xác suất mặt thật 0..1 (trung bình các model); None nếu chưa có model."""
        if not self.models:
            return None
        probs = []
        for sess, name, scale, w, h in self.models:
            crop = _crop_scaled(frame, bbox, scale, w, h)
            x = crop.transpose(2, 0, 1)[None].astype(np.float32)   # BGR, 0..255, không chia 255
            logits = sess.run(None, {name: x})[0][0]
            e = np.exp(logits - logits.max())
            probs.append(e / e.sum())
        return float(np.mean(probs, axis=0)[1])   # lớp 1 = mặt thật


class PadWindow:
    """Cửa sổ trượt các điểm PAD để giảm nhiễu từng frame."""

    def __init__(self, size=6, min_frames=4):
        self.scores = deque(maxlen=size)
        self.size, self.min_frames = size, min_frames

    def add(self, s):
        self.scores.append(s)

    def mean(self):
        return float(np.mean(self.scores)) if self.scores else 0.0

    def ready(self):
        return len(self.scores) >= self.min_frames

    def full(self):
        return len(self.scores) >= self.size

    def clear(self):
        self.scores.clear()
