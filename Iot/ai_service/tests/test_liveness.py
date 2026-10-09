"""Kiểm thử thách thức người thật bằng chuỗi số đo THẬT từ webcam (tools/calibrate_liveness.py, ~5 fps).

Chạy:  python -m pytest tests -q      (từ thư mục ai_service)
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import liveness
from liveness import LivenessChallenge

OPEN_EAR = 0.327

# Tỉ lệ EAR / mức mở của 48 khung hình khi chớp mắt tự nhiên 6 lần (đo thật)
REAL_BLINK = [0.97, 0.97, 0.98, 0.98, 0.98, 0.99, 0.99, 0.99, 0.97, 0.98, 0.96, 0.91, 0.97, 0.97, 0.86, 0.90,
              0.95, 0.95, 0.95, 0.79, 0.91, 0.95, 0.94, 0.95, 0.95, 0.76, 0.93, 0.96, 0.95, 0.95, 0.97, 0.79,
              0.96, 0.95, 0.95, 0.96, 0.73, 0.94, 0.99, 0.98, 0.98, 0.98, 0.80, 0.93, 0.97, 0.97, 0.97, 0.98]
# Độ lệch mũi khi quay phải / trái 30-40 độ (đo thật; hàng đầu là tư thế thẳng lúc bắt đầu)
REAL_RIGHT = [0.0731, 0.0591, 0.0318, -0.0152, -0.1203, -0.1721, -0.1977, -0.2094, -0.2176, -0.2200, -0.2301,
              -0.2227, -0.2196, -0.2187, -0.2230, -0.2343, -0.2299, -0.2237, -0.2343, -0.2371]
REAL_LEFT = [0.0644, 0.1353, 0.2384, 0.3144, 0.3422, 0.3574, 0.3585, 0.3634, 0.3644, 0.3630, 0.3675, 0.3645]
STRAIGHT = [0.0838, 0.0822, 0.0779, 0.0587, 0.0597, 0.0555, 0.0463, 0.0464, 0.0517, 0.0562] * 3


class Clock:
    """Đồng hồ giả để thử thời gian nhắm mắt mà không phải chờ thật."""
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture()
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(liveness.time, "time", c)
    return c


def run(ctype, series, clock, kind, dt=0.2):
    """Phát lại chuỗi giá trị; trả về chỉ số khung hình mà thách thức 'passed' (hoặc None)."""
    ch = LivenessChallenge(timeout_s=60, ctype=ctype)
    for i, v in enumerate(series):
        clock.t += dt
        if kind == "ear":
            liveness.face_ear = lambda f, v=v: v * OPEN_EAR
        else:
            liveness.face_turn_offset = lambda f, v=v: v
        status = ch.update(object())
        if status == "passed":
            return i
        assert status != "failed", f"thất bại ở khung {i}"
    return None


@pytest.fixture(autouse=True)
def restore_funcs():
    ear, off = liveness.face_ear, liveness.face_turn_offset
    yield
    liveness.face_ear, liveness.face_turn_offset = ear, off


# ----------------------------- Chớp mắt -----------------------------
def test_real_blinks_pass_on_first_blink(clock):
    idx = run("blink", REAL_BLINK, clock, "ear")
    assert idx is not None and idx <= 21          # cú chớp đầu tiên đủ sâu là ở khung 19-20


def test_no_blink_never_passes(clock):
    noise = [1.0, 1.005, 0.995, 1.003, 0.997] * 12
    assert run("blink", noise, clock, "ear") is None


def test_single_garbage_high_frame_does_not_break_detection(clock):
    series = list(REAL_BLINK)
    series[6] = 2.7        # khung rác: EAR ~0.9 (gấp ~3 lần bình thường), cách thuật toán cũ bị phá
    assert run("blink", series, clock, "ear") is not None


def test_garbage_frame_alone_is_not_a_blink(clock):
    series = [1.0] * 30
    series[10] = 2.7
    assert run("blink", series, clock, "ear") is None


def test_eyes_closed_for_long_is_not_a_blink(clock):
    series = [1.0] * 10 + [0.6] * 20             # nhắm hẳn 4 giây, không mở lại -> không phải chớp
    assert run("blink", series, clock, "ear") is None


def test_looking_down_then_recovering_slowly_is_not_a_blink(clock):
    series = [1.0] * 10 + [0.8] * 10 + [1.0] * 10   # EAR thấp ~2s rồi mới lên lại: quá lâu
    assert run("blink", series, clock, "ear") is None


# ----------------------------- Quay đầu -----------------------------
def test_real_turn_right_passes_when_asked_right(clock):
    assert run("turn_right", REAL_RIGHT, clock, "turn") is not None


def test_real_turn_left_passes_when_asked_left(clock):
    assert run("turn_left", REAL_LEFT, clock, "turn") is not None


def test_wrong_direction_does_not_pass(clock):
    assert run("turn_left", REAL_RIGHT, clock, "turn") is None
    assert run("turn_right", REAL_LEFT, clock, "turn") is None


def test_staying_straight_does_not_pass_turn(clock):
    assert run("turn_right", STRAIGHT, clock, "turn") is None
    assert run("turn_left", STRAIGHT, clock, "turn") is None


def test_challenge_times_out(clock):
    ch = LivenessChallenge(timeout_s=3, ctype="blink")
    liveness.face_ear = lambda f: OPEN_EAR
    clock.t += 5
    assert ch.update(object()) == "failed"
