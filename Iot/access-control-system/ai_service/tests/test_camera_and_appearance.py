"""Hai chỗ liên quan độ ổn định của phiên quét mặt qua camera ESP32:
  * CameraStream bỏ vài khung đầu sau khi mở luồng (khung cũ nằm trong bộ đệm camera + khung chưa lấy sáng làm PAD chấm thấp oan);
  * main.appearance_changed: không coi là "đổi diện mạo" khi đầu vừa bắt đầu quay (độ khớp tụt là bình thường).
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import liveness
import main


class FakeCapture:
    """cv2.VideoCapture giả: đúng `total` khung (khung n có giá trị n), hết thì trả lỗi."""

    def __init__(self, total=5):
        self.n = 0
        self.total = total

    def read(self):
        if self.n >= self.total:
            time.sleep(0.005)
            return False, None
        self.n += 1
        return True, main.np.full((1, 1, 3), self.n % 256, main.np.uint8)

    def release(self):
        pass


def frames_kept(skip, total=5):
    """Mở CameraStream trên nguồn giả có `total` khung; trả (số khung được giữ, giá trị khung nhận được cuối)."""
    cam = main.CameraStream("http://esp32/stream", skip=skip)
    try:
        deadline = time.time() + 2.0
        while cam.cap.n < total and time.time() < deadline:
            time.sleep(0.005)
        time.sleep(0.05)
        frame, seq = cam.latest()
        return seq, (int(frame[0, 0, 0]) if frame is not None else None)
    finally:
        cam.release()


@pytest.fixture()
def fake_cap(monkeypatch):
    monkeypatch.setattr(main.cv2, "VideoCapture", lambda *a, **k: FakeCapture())


def test_stream_drops_exactly_the_first_frames(fake_cap):
    assert frames_kept(skip=3) == (2, 5)       # 5 khung đọc, bỏ 3 khung đầu, giữ 2 (khung 4 và 5)


def test_stream_without_skip_keeps_every_frame(fake_cap):
    assert frames_kept(skip=0) == (5, 5)


def test_stream_that_delivers_fewer_frames_than_skip_has_no_frame_yet(fake_cap):
    assert frames_kept(skip=10) == (0, None)


def test_default_skip_is_zero_for_a_local_webcam_and_positive_for_a_url():
    assert main.default_skip_frames(0) == 0
    assert main.default_skip_frames("http://192.168.1.195:81/stream") > 0


def test_appearance_jump_is_detected_when_looking_straight():
    assert main.appearance_changed(0.50, [0.85, 0.84, 0.86], turn_progress=0.0) is True


def test_head_just_starting_to_turn_is_not_an_appearance_change():
    started = liveness.TURN_DELTA * 0.3
    assert main.appearance_changed(0.61, [0.81, 0.82, 0.80], turn_progress=started) is False


def test_small_changes_and_too_few_samples_are_ignored():
    assert main.appearance_changed(0.69, [0.85, 0.85, 0.85], turn_progress=0.0) is False   # lệch 0.16 < ngưỡng
    assert main.appearance_changed(0.10, [0.85, 0.85], turn_progress=0.0) is False         # mới 2 mẫu


# ---------- Luồng im lặng (board khởi động lại, WiFi đứt): tự mở lại thay vì chờ ffmpeg 30 giây ----------
class DeadCapture:
    def read(self):
        time.sleep(0.005)
        return False, None

    def release(self):
        pass


def test_stalled_url_stream_is_reopened_by_itself(monkeypatch):
    opened = []

    def factory(*a, **k):
        opened.append(a)
        return DeadCapture() if len(opened) == 1 else FakeCapture(total=50)

    monkeypatch.setattr(main.cv2, "VideoCapture", factory)
    cam = main.CameraStream("http://esp32/stream", skip=0, reopen_after_s=0.05)
    try:
        assert cam.wait_first_frame(timeout=2.0), "luồng không được mở lại"
        assert len(opened) >= 2
    finally:
        cam.release()


def test_local_webcam_is_never_reopened(monkeypatch):
    opened = []

    def factory(*a, **k):
        opened.append(a)
        return DeadCapture()

    monkeypatch.setattr(main.cv2, "VideoCapture", factory)
    cam = main.CameraStream(0, skip=0, reopen_after_s=0.05)
    try:
        time.sleep(0.4)
        assert len(opened) == 1
    finally:
        cam.release()


# ---------- Đóng camera khi luồng đọc còn kẹt trong read(): không được gọi release() chồng lên read() ----------
# (OpenCV VideoCapture không an toàn đa luồng: đóng trong lúc đọc làm ffmpeg tự assert và giết cả tiến trình AI)
import threading


class BlockingCapture:
    def __init__(self):
        self.gate = threading.Event()
        self.in_read = False
        self.released_during_read = False
        self.release_calls = 0

    def read(self):
        self.in_read = True
        self.gate.wait(5)
        self.in_read = False
        return False, None

    def release(self):
        self.release_calls += 1
        if self.in_read:
            self.released_during_read = True


def test_release_never_overlaps_a_blocked_read(monkeypatch):
    cap = BlockingCapture()
    monkeypatch.setattr(main.cv2, "VideoCapture", lambda *a, **k: cap)
    cam = main.CameraStream("http://esp32/stream", skip=0)
    time.sleep(0.1)                                   # luồng đọc đã kẹt trong read()
    t0 = time.time()
    cam.release()
    assert time.time() - t0 < 3.5, "release() không được treo chương trình chính"
    assert cap.released_during_read is False
    cap.gate.set()                                    # read() trả về: luồng tự đóng camera
    deadline = time.time() + 2
    while cap.release_calls == 0 and time.time() < deadline:
        time.sleep(0.01)
    assert cap.release_calls == 1 and cap.released_during_read is False


def test_release_closes_the_capture_exactly_once_in_the_normal_case(monkeypatch):
    cap = FakeCapture(total=1000)
    closed = []
    cap.release = lambda: closed.append(1)
    monkeypatch.setattr(main.cv2, "VideoCapture", lambda *a, **k: cap)
    cam = main.CameraStream("http://esp32/stream", skip=0)
    time.sleep(0.05)
    cam.release()
    time.sleep(0.1)
    assert closed == [1]


# ---------- PAD chỉ chấm điểm khung nhìn thẳng: mặt nghiêng (đang quay đầu theo thử thách) cho điểm thấp, không phải giả mạo ----------
def test_pad_is_scored_before_any_challenge_exists():
    assert main.pad_applicable(None) is True


def test_pad_is_scored_while_looking_straight_during_a_challenge():
    ch = liveness.LivenessChallenge(ctype="turn_left")
    ch.debug = 0.02
    assert main.pad_applicable(ch) is True


def test_pad_is_not_scored_while_the_head_is_turned():
    ch = liveness.LivenessChallenge(ctype="turn_right")
    ch.debug = liveness.TURN_DELTA * 0.9
    assert main.pad_applicable(ch) is False


def test_pad_is_scored_during_a_blink_challenge():
    ch = liveness.LivenessChallenge(ctype="blink")
    ch.debug = 5.0                     # với chớp mắt, debug không phải độ lệch đầu
    assert main.pad_applicable(ch) is True
