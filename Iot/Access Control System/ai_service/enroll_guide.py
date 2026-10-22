"""Quét khuôn mặt có hướng dẫn khi đăng ký (kiểu eKYC ngân hàng): nhìn thẳng -> quay trái -> quay phải -> chớp mắt.

GuidedEnroll là máy trạng thái thuần Python: nhận kết quả kiểm tra TỪNG khung hình (enrollment.inspect_image) cùng khuôn
mặt tìm được, và cho biết bước hiện tại đã xong chưa. Không đụng đến camera, mô hình hay HTTP nên thử được bằng số liệu giả lập.
Thước đo tư thế đầu và chớp mắt dùng lại của cửa ra vào (liveness.py) nên cùng ngưỡng đã hiệu chỉnh.
"""
import math
import os
import secrets
import threading
import time

import numpy as np

import liveness


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


STEP_IDS = ("front", "left", "right", "blink")
STEP_TIMEOUT_S = _env_float("AI_ENROLL_STEP_TIMEOUT_S", 15.0)   # mỗi bước có ngần này giây, tính từ khung hình đầu tiên của bước
FRONT_MAX_OFFSET = _env_float("AI_ENROLL_FRONT_OFFSET", 0.10)   # |độ lệch mũi| tối đa coi là nhìn thẳng (đơn vị: khoảng cách hai mắt)
FRONT_HOLD_S = _env_float("AI_ENROLL_FRONT_HOLD_S", 0.6)         # phải giữ nhìn thẳng liên tục ngần này giây
FRONT_MIN_WIDTH_RATIO = _env_float("AI_ENROLL_MIN_WIDTH_RATIO", 0.18)   # mặt rộng tối thiểu so với bề ngang khung hình
WRONG_WAY_S = _env_float("AI_ENROLL_WRONG_WAY_S", 0.6)          # quay sai hướng liên tục ngần này giây mới nhắc (lướt qua giữa thì không)
BLINK_FRONT_FRAMES = 3      # số khung nhìn thẳng liên tiếp trước khi bắt đầu đo chớp mắt
CENTER_X = (0.30, 0.70)     # tâm mặt phải nằm trong vùng này của khung hình (khung tròn hướng dẫn trên web)
CENTER_Y = (0.25, 0.75)
SESSION_TTL_S = 120.0       # phiên bỏ dở quá ngần này giây thì bị xóa
MAX_SESSIONS = 8            # số phiên mở cùng lúc tối đa (mỗi tab trình duyệt một phiên)


class GuidedEnroll:
    """Một phiên quét. update() gọi một lần cho mỗi khung hình và trả về trạng thái để hiển thị.

    Hết giờ một bước thì phiên dừng ở bước đó (bỏ qua mọi khung hình) cho tới khi retry(); riêng bước chớp mắt có thể skip()."""

    def __init__(self, step_timeout_s=STEP_TIMEOUT_S):
        self.step_timeout_s = step_timeout_s
        self.index = 0
        self.neutral = None          # độ lệch mũi lúc nhìn thẳng, đo ở bước 1, làm mốc cho các bước quay
        self._skipped = set()        # chỉ số các bước đã bỏ qua
        self._begin_step()

    @property
    def done(self):
        return self.index >= len(STEP_IDS)

    def _reset_progress(self):
        """Làm lại phần đang giữ dở của bước hiện tại (khung xấu, mất mặt...). Mốc tư thế thẳng `neutral` được giữ."""
        self._hold_since = None
        self._samples = []
        self._challenge = None       # liveness.LivenessChallenge của bước quay / chớp mắt, tạo khi cần
        self._wrong_since = None
        self._front_frames = 0       # số khung nhìn thẳng liên tiếp trước khi bắt đầu đo chớp mắt

    def _begin_step(self):
        self._reset_progress()
        self._deadline = None        # đặt ở khung hình đầu tiên của bước
        self.timed_out = False

    def retry(self):
        """Làm lại bước hiện tại với ngân sách thời gian mới (sau khi hết giờ)."""
        if not self.done:
            self._begin_step()

    def skip(self):
        """Bỏ qua bước chớp mắt (camera quá chậm để bắt được cú chớp). Các bước khác không bỏ qua được. Trả về True nếu đã bỏ qua."""
        if self.done or STEP_IDS[self.index] != "blink":
            return False
        self._skipped.add(self.index)
        self.index += 1
        self._begin_step()
        return True

    def _state(self, i):
        if i in self._skipped:
            return "skipped"
        return "done" if i < self.index else "active" if i == self.index else "pending"

    def snapshot(self):
        """Trạng thái hiện tại (không cần khung hình mới)."""
        if self.done or self.timed_out:
            time_left = 0
        else:
            left = self.step_timeout_s if self._deadline is None else self._deadline - time.time()
            time_left = max(0, math.ceil(left))
        return {
            "step": None if self.done else STEP_IDS[self.index],
            "step_index": self.index,
            "total": len(STEP_IDS),
            "steps": [{"id": sid, "state": self._state(i)} for i, sid in enumerate(STEP_IDS)],
            "hint": "",
            "passed_step": None,
            "step_timeout": self.timed_out,
            "time_left": time_left,
            "done": self.done,
        }

    def update(self, reason, face, frame_size):
        """reason: kết quả enrollment.inspect_image ("ok" hoặc mã lỗi); face: khuôn mặt insightface (hoặc None);
        frame_size: (rộng, cao) của khung hình. Trả về snapshot() kèm hint (mã gợi ý) và passed_step (bước vừa xong)."""
        now = time.time()
        if not self.done:
            if self._deadline is None:
                self._deadline = now + self.step_timeout_s
            elif now > self._deadline:
                self.timed_out = True
        out = self.snapshot()
        if self.done or self.timed_out:
            return out
        if reason != "ok":
            self._reset_progress()
            out["hint"] = reason
            return out
        step = STEP_IDS[self.index]
        if step == "front":
            hint, passed = self._front(face, frame_size, now)
        elif step == "blink":
            hint, passed = self._blink(face)
        else:
            hint, passed = self._turn(step, face, now)
        if passed:
            finished = STEP_IDS[self.index]
            self.index += 1
            self._begin_step()
            out = self.snapshot()
            out["passed_step"] = finished
        else:
            out["hint"] = hint
        return out

    def _front(self, face, frame_size, now):
        offset = liveness.face_turn_offset(face)
        width, height = frame_size
        x1, y1, x2, y2 = (float(v) for v in face.bbox)
        cx, cy = (x1 + x2) / 2 / width, (y1 + y2) / 2 / height
        if (x2 - x1) / width < FRONT_MIN_WIDTH_RATIO:
            hint = "too_far"
        elif not (CENTER_X[0] <= cx <= CENTER_X[1] and CENTER_Y[0] <= cy <= CENTER_Y[1]):
            hint = "off_center"
        elif offset is None or abs(offset) > FRONT_MAX_OFFSET:
            hint = "not_front"
        else:
            hint = ""
        if hint:
            self._reset_progress()
            return hint, False
        self._samples.append(offset)
        if self._hold_since is None:
            self._hold_since = now
        if now - self._hold_since >= FRONT_HOLD_S:
            self.neutral = float(np.median(self._samples))
            return "", True
        return "", False

    def _blink(self, face):
        """Chớp mắt chỉ được đo khi đang nhìn thẳng: mức "mắt mở" lấy lúc đang quay đầu sẽ sai (EAR đổi theo góc mặt)."""
        offset = liveness.face_turn_offset(face)
        if offset is None or abs(offset) > FRONT_MAX_OFFSET:
            self._reset_progress()
            return "not_front", False
        if self._challenge is None:
            self._front_frames += 1
            if self._front_frames < BLINK_FRONT_FRAMES:
                return "", False
            self._challenge = liveness.LivenessChallenge(timeout_s=float("inf"), ctype="blink")
        return "", self._challenge.update(face) == "passed"

    def _turn(self, step, face, now):
        """Quay trái / phải so với mốc thẳng của bước 1; giữ tư thế theo thời gian do LivenessChallenge lo."""
        if self._challenge is None:
            self._challenge = liveness.LivenessChallenge(timeout_s=float("inf"), ctype="turn_" + step, baseline=self.neutral)
        if self._challenge.update(face) == "passed":
            return "", True
        delta = self._challenge.debug       # độ lệch so với mốc thẳng; dương = sang trái của người dùng
        wrong = delta <= -liveness.TURN_DELTA / 2 if step == "left" else delta >= liveness.TURN_DELTA / 2
        if not wrong:
            self._wrong_since = None
            return "", False
        if self._wrong_since is None:
            self._wrong_since = now
        return ("wrong_way" if now - self._wrong_since >= WRONG_WAY_S else ""), False


class SessionStore:
    """Các phiên quét đang mở. Phiên bỏ dở quá ttl_s bị xóa; mở quá max_sessions thì phiên dùng lâu nhất bị xóa."""

    def __init__(self, ttl_s=SESSION_TTL_S, max_sessions=MAX_SESSIONS):
        self.ttl_s, self.max_sessions = ttl_s, max_sessions
        self._items = {}             # id -> [GuidedEnroll, lần dùng cuối]
        self._lock = threading.Lock()

    def _purge(self, now):
        for sid in [k for k, (_, used) in self._items.items() if now - used > self.ttl_s]:
            del self._items[sid]

    def start(self):
        """Mở phiên mới. Trả về (id, GuidedEnroll)."""
        now = time.time()
        with self._lock:
            self._purge(now)
            while len(self._items) >= self.max_sessions:
                del self._items[min(self._items, key=lambda k: self._items[k][1])]
            sid = secrets.token_urlsafe(9)
            guide = GuidedEnroll()
            self._items[sid] = [guide, now]
            return sid, guide

    def get(self, sid):
        """Tìm phiên và gia hạn; None nếu không có hoặc đã hết hạn."""
        now = time.time()
        with self._lock:
            self._purge(now)
            item = self._items.get(sid)
            if item is None:
                return None
            item[1] = now
            return item[0]
