"""Kiểm thử quét khuôn mặt có hướng dẫn khi đăng ký (enroll_guide.GuidedEnroll).

Khuôn mặt giả lập có landmark_3d_68 thật hình học (hai khóe mắt ngoài, chóp mũi, 6 điểm mỗi mắt) nên toàn bộ
liveness.face_turn_offset / face_ear / LivenessChallenge chạy thật, chỉ có camera và mô hình là giả.
Các chuỗi số đo lấy từ webcam thật (xem test_liveness.py).

Chạy:  python -m pytest tests -q      (từ thư mục ai_service)
"""
import os
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import enroll_guide
from enroll_guide import GuidedEnroll
from test_liveness import REAL_BLINK, REAL_LEFT, REAL_RIGHT      # số đo thật: quay trái/phải 30-40 độ, chớp mắt tự nhiên

SIZE = (640, 480)
IOD, EYE_W, OPEN_EAR = 100.0, 30.0, 0.327


def _set_eye(lm, base, x0, h):
    """6 điểm iBUG của một mắt: khóe ngoài/trong ở hai đầu, hai điểm trên và hai điểm dưới cách nhau h => EAR = h / EYE_W."""
    pts = [(0, 0), (EYE_W / 3, -h / 2), (2 * EYE_W / 3, -h / 2), (EYE_W, 0), (2 * EYE_W / 3, h / 2), (EYE_W / 3, h / 2)]
    for i, (dx, dy) in enumerate(pts):
        lm[base + i, 0], lm[base + i, 1] = x0 + dx, dy


def make_face(offset=0.06, ear=1.0, cx=0.5, cy=0.5, width=0.30):
    """offset: độ lệch mũi (dương = quay sang TRÁI của người dùng); ear: tỉ lệ EAR so với mắt mở; cx, cy: tâm mặt trong khung (0..1)."""
    lm = np.zeros((68, 3), np.float32)
    _set_eye(lm, 36, -IOD / 2, ear * OPEN_EAR * EYE_W)
    _set_eye(lm, 42, IOD / 2 - EYE_W, ear * OPEN_EAR * EYE_W)
    lm[30, 0] = offset * IOD
    w_img, h_img = SIZE
    fw = width * w_img
    bbox = np.array([cx * w_img - fw / 2, cy * h_img - fw * 0.6, cx * w_img + fw / 2, cy * h_img + fw * 0.6])
    return SimpleNamespace(landmark_3d_68=lm, bbox=bbox, det_score=0.9)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture()
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(enroll_guide.time, "time", c)     # cùng đối tượng module time với liveness
    return c


class Driver:
    """Đưa từng khung hình (cách nhau dt giây ≈ 5 fps như số đo thật) vào một phiên quét."""

    def __init__(self, clock, dt=0.2, **guide_kwargs):
        self.clock, self.dt = clock, dt
        self.guide = GuidedEnroll(**guide_kwargs)

    def feed(self, reason="ok", **face):
        self.clock.t += self.dt
        return self.guide.update(reason, None if reason == "no_face" else make_face(**face), SIZE)

    def feed_until_passed(self, kwargs_list, limit=None):
        """Phát lại danh sách tham số khuôn mặt; trả về kết quả của khung làm bước đạt (hoặc None)."""
        for kw in kwargs_list[:limit]:
            res = self.feed(**kw)
            if res["passed_step"]:
                return res
        return None


@pytest.fixture()
def drv(clock):
    return Driver(clock)


def straight(n, **kw):
    return [dict(offset=0.06, **kw) for _ in range(n)]


def turns(series, **kw):
    return [dict(offset=v, **kw) for v in series]


def to_left_step(drv):
    res = drv.feed_until_passed(straight(10))
    assert res and res["passed_step"] == "front"


def to_right_step(drv):
    to_left_step(drv)
    res = drv.feed_until_passed(turns(REAL_LEFT))
    assert res and res["passed_step"] == "left"


# =============================== Bước 1: nhìn thẳng ===============================
def test_front_step_needs_a_steady_hold_not_a_single_frame(drv):
    results = [drv.feed(offset=0.06) for _ in range(3)]            # 0,6 giây tính từ khung đầu: chưa đủ giữ 0,6 giây
    assert [r["passed_step"] for r in results] == [None, None, None]
    assert results[-1]["step"] == "front"
    final = drv.feed(offset=0.06)
    assert final["passed_step"] == "front" and final["step"] == "left"


def test_front_hold_restarts_when_the_head_moves_away(drv):
    for _ in range(3):
        drv.feed(offset=0.06)
    drv.feed(offset=0.30)                                           # quay đi một khung
    again = [drv.feed(offset=0.06) for _ in range(3)]               # phải giữ lại từ đầu
    assert all(r["passed_step"] is None for r in again)
    assert drv.feed(offset=0.06)["passed_step"] == "front"


@pytest.mark.parametrize("reason", ["no_face", "multiple_faces", "low_quality", "blurry", "spoof_suspected"])
def test_bad_frames_never_pass_report_the_reason_and_restart_the_hold(drv, reason):
    for _ in range(3):
        drv.feed(offset=0.06)                                       # đã giữ gần đủ
    bad = drv.feed(reason, offset=0.06)
    assert bad["hint"] == reason and bad["passed_step"] is None
    again = [drv.feed(offset=0.06) for _ in range(3)]               # khung xấu làm giữ lại từ đầu
    assert all(r["passed_step"] is None for r in again)


def test_off_center_face_is_not_accepted_as_front(drv):
    results = [drv.feed(offset=0.06, cx=0.92) for _ in range(10)]
    assert all(r["passed_step"] is None and r["hint"] == "off_center" for r in results)


def test_turned_face_is_not_front(drv):
    results = [drv.feed(offset=0.30) for _ in range(10)]
    assert all(r["passed_step"] is None and r["hint"] == "not_front" for r in results)


def test_small_face_far_from_camera_is_not_accepted_as_front(drv):
    results = [drv.feed(offset=0.06, width=0.10) for _ in range(10)]          # mặt rộng 10% khung hình
    assert all(r["passed_step"] is None and r["hint"] == "too_far" for r in results)


# =============================== Bước 2-3: quay trái, quay phải ===============================
def test_left_step_passes_on_a_real_left_turn_and_moves_on_to_right(drv):
    to_left_step(drv)
    res = drv.feed_until_passed(turns(REAL_LEFT))
    assert res["passed_step"] == "left" and res["step"] == "right"
    assert [s["state"] for s in res["steps"]] == ["done", "done", "active", "pending"]


def test_right_step_passes_on_a_real_right_turn(drv):
    to_right_step(drv)
    res = drv.feed_until_passed(turns(REAL_RIGHT))
    assert res["passed_step"] == "right" and res["step"] == "blink"


def test_turn_that_is_already_underway_on_the_first_frame_of_the_step_still_counts(drv):
    # Mốc "thẳng" lấy từ bước 1. Nếu lấy từ vài khung đầu của chính bước quay thì người quay ngay sẽ không bao giờ đạt.
    to_right_step(drv)
    res = drv.feed_until_passed(turns([-0.22] * 8))
    assert res["passed_step"] == "right"


def test_staying_straight_never_passes_a_turn_step(drv):
    to_left_step(drv)
    assert drv.feed_until_passed(straight(40)) is None


def test_turning_the_wrong_way_is_called_out_after_a_moment_and_never_passes(drv):
    to_left_step(drv)
    results = [drv.feed(**kw) for kw in turns(REAL_RIGHT)]        # được yêu cầu quay TRÁI nhưng quay PHẢI
    assert all(r["passed_step"] is None for r in results)
    assert "wrong_way" in [r["hint"] for r in results]


def test_swinging_back_through_the_middle_from_the_previous_turn_is_not_called_wrong_way(drv):
    to_right_step(drv)                                            # vừa quay trái xong, đang lệch +0,36
    swing = [0.30, 0.16, 0.04, -0.10, -0.22, -0.23, -0.23, -0.23]
    results = [drv.feed(**kw) for kw in turns(swing)]
    assert all(r["hint"] != "wrong_way" for r in results)
    assert "right" in [r["passed_step"] for r in results]


def test_losing_the_face_during_a_turn_restarts_the_hold(drv):
    to_left_step(drv)
    for _ in range(6):                                            # mỗi lần chỉ giữ 0,2 giây rồi mất mặt: không bao giờ đủ 0,35 giây
        assert drv.feed(offset=0.30)["passed_step"] is None
        drv.feed("no_face")


# =============================== Bước 4: chớp mắt ===============================
def to_blink_step(drv):
    to_right_step(drv)
    res = drv.feed_until_passed(turns(REAL_RIGHT))
    assert res and res["passed_step"] == "right"


def blinks(series, **kw):
    return [dict(offset=0.06, ear=v, **kw) for v in series]


def test_real_blinks_pass_the_last_step_and_finish_the_session(drv):
    to_blink_step(drv)
    res = drv.feed_until_passed(blinks(REAL_BLINK))
    assert res["passed_step"] == "blink" and res["done"] is True and res["step"] is None
    assert [s["state"] for s in res["steps"]] == ["done"] * 4
    later = drv.feed(offset=0.06)                                  # phiên đã xong: khung sau đó không đổi gì
    assert later["done"] is True and later["passed_step"] is None


def test_no_blink_never_passes(drv):
    to_blink_step(drv)
    assert drv.feed_until_passed(blinks([1.0, 1.005, 0.995, 1.003, 0.997] * 12)) is None


def test_blinks_made_while_the_head_is_still_turned_do_not_count(drv):
    to_blink_step(drv)
    results = [drv.feed(offset=-0.23, ear=v) for v in REAL_BLINK]  # vẫn đang quay phải
    assert all(r["passed_step"] is None and r["hint"] == "not_front" for r in results)


def test_blink_is_detected_once_the_user_has_turned_back_to_front(drv):
    to_blink_step(drv)
    for _ in range(4):
        drv.feed(offset=-0.23)                                     # còn đang quay
    assert drv.feed_until_passed(blinks(REAL_BLINK))["passed_step"] == "blink"


# =============================== Hết giờ, thử lại, bỏ qua ===============================
def test_step_times_out_when_nothing_happens_and_then_ignores_frames_until_retry(clock):
    drv = Driver(clock, step_timeout_s=3)
    flags = [drv.feed("no_face")["step_timeout"] for _ in range(20)]       # 4 giây không thấy mặt
    assert flags[0] is False and flags[-1] is True
    late = [drv.feed(offset=0.06) for _ in range(10)]                       # khung hoàn hảo đến muộn không còn được tính
    assert all(r["step_timeout"] and r["passed_step"] is None for r in late)


def test_retry_gives_a_fresh_time_budget_and_the_step_can_then_pass(clock):
    drv = Driver(clock, step_timeout_s=3)
    for _ in range(20):
        drv.feed("no_face")
    drv.guide.retry()
    assert drv.feed_until_passed(straight(10))["passed_step"] == "front"


def test_each_step_gets_its_own_time_budget(clock):
    drv = Driver(clock, step_timeout_s=3)
    to_left_step(drv)                                                       # bước 1 mất ~0,8 giây
    results = [drv.feed(offset=0.06) for _ in range(12)]                    # 2,4 giây ở bước 2: tổng > 3 giây nhưng chưa hết giờ bước 2
    assert not any(r["step_timeout"] for r in results)


def test_time_left_counts_down_from_the_first_frame_of_the_step(clock):
    drv = Driver(clock, dt=1.0, step_timeout_s=10)
    assert drv.guide.snapshot()["time_left"] == 10                          # chưa có khung nào: còn đủ ngân sách
    assert [drv.feed("no_face")["time_left"] for _ in range(4)] == [10, 9, 8, 7]


def test_only_the_blink_step_can_be_skipped(drv):
    assert drv.guide.skip() is False and drv.guide.snapshot()["step"] == "front"
    to_blink_step(drv)
    assert drv.guide.skip() is True
    snap = drv.guide.snapshot()
    assert snap["done"] is True and [s["state"] for s in snap["steps"]] == ["done", "done", "done", "skipped"]


# =============================== Kho phiên quét (nhiều trình duyệt, bỏ dở giữa chừng) ===============================
def test_sessions_are_independent_and_found_by_id(clock):
    store = enroll_guide.SessionStore(ttl_s=60, max_sessions=4)
    a, guide_a = store.start()
    b, guide_b = store.start()
    assert a != b and guide_a is not guide_b
    assert store.get(a) is guide_a and store.get(b) is guide_b
    assert store.get("khong-co") is None


def test_idle_session_expires_but_activity_keeps_it_alive(clock):
    store = enroll_guide.SessionStore(ttl_s=60, max_sessions=4)
    idle, _ = store.start()
    busy, guide_busy = store.start()
    for _ in range(3):
        clock.t += 30
        assert store.get(busy) is guide_busy                      # dùng liên tục: sống
    assert store.get(idle) is None                                # 90 giây không động tới: đã bị xóa


def test_oldest_session_is_dropped_when_too_many_are_open(clock):
    store = enroll_guide.SessionStore(ttl_s=600, max_sessions=2)
    first, _ = store.start()
    clock.t += 1
    second, _ = store.start()
    clock.t += 1
    third, _ = store.start()
    assert store.get(first) is None and store.get(second) is not None and store.get(third) is not None


def test_payload_lists_the_four_steps_and_their_progress(drv):
    first = drv.feed(offset=0.06)
    assert first["total"] == 4 and first["step_index"] == 0
    assert [(s["id"], s["state"]) for s in first["steps"]] == [
        ("front", "active"), ("left", "pending"), ("right", "pending"), ("blink", "pending")]
    for _ in range(3):
        last = drv.feed(offset=0.06)
    assert last["step_index"] == 1
    assert [s["state"] for s in last["steps"]] == ["done", "active", "pending", "pending"]
