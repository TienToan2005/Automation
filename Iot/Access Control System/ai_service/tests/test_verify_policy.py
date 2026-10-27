"""Chính sách kết luận trong phiên quét cửa (main.process_frame): chỉ buộc tội khi có BẰNG CHỨNG, còn lại thì phân loại
đúng kiểu lỗi thu nhận để backend không tính nhầm là tấn công.

  * độ khớp: match >= SIMILARITY_THRESHOLD | chưa kết luận (giữa hai ngưỡng) | sai chủ thẻ chỉ khi thấp RÕ RỆT
  * khung kém chất lượng (rất mờ / dò mặt kém tin cậy) không được dùng để kết luận khớp hay không khớp
  * PAD: chắc chắn giả (điểm rất thấp) -> spoof_pad; lưng chừng -> không buộc tội, hết giờ báo pad_uncertain
  * hết giờ: face_not_detected | face_quality_low | face_inconclusive | pad_uncertain | timeout
"""
import os
import sys
import time

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main

NOISE = np.random.RandomState(1).randint(0, 255, (480, 640, 3)).astype(np.uint8)    # rất nét
FLAT = np.full((480, 640, 3), 128, np.uint8)                                          # độ nét = 0
EMB = np.zeros(512, np.float32)
EMB[0] = 1.0


def emb_with_similarity(sim):
    v = np.zeros(512, np.float32)
    v[0], v[1] = sim, float(np.sqrt(max(0.0, 1 - sim * sim)))
    return v


class Face:
    def __init__(self, sim=0.9, width=200, det=0.8):
        self.bbox = np.array([100.0, 60.0, 100.0 + width, 60.0 + width * 1.25])
        self.det_score = det
        self.normed_embedding = emb_with_similarity(sim)


class App:
    def __init__(self):
        self.faces = []

    def get(self, img):
        return list(self.faces)


class FakePad:
    enabled = True

    def __init__(self):
        self.value = 0.95

    def score(self, img, bbox):
        return self.value


@pytest.fixture()
def env(monkeypatch):
    app, pad, events = App(), FakePad(), []
    monkeypatch.setattr(main, "face_app", app)
    monkeypatch.setattr(main, "pad", pad)
    monkeypatch.setattr(main, "publish_event", events.append)
    monkeypatch.setattr(main, "encode_snapshot", lambda *a, **k: None)
    now = time.time()
    with main.state_lock:
        main.ai_state.update(is_active=True, session_id="S1", expected_id="B23DCAT286", embedding=EMB, started_at=now,
                             timeout_at=now + 60, result="scanning", reason="", phase="verify", hint="", similarity=0.0,
                             pad_score=0.0, challenge=None)
    ctx = main.SessionCtx("S1")
    yield app, pad, events, ctx
    with main.state_lock:
        main.ai_state.update(is_active=False, result="idle", reason="", challenge=None)


def run(env, face, frames=1, img=NOISE):
    app, _pad, _events, ctx = env
    app.faces = [face] if face is not None else []
    for _ in range(frames):
        main.process_frame(img, ctx)


def finished(env):
    return [(e["result"], e["reason"]) for e in env[2]]


# ----------------------------------- độ khớp -----------------------------------
def test_clear_match_is_never_an_accusation(env):
    run(env, Face(sim=0.85), frames=30)
    assert finished(env) == [] or all(r != "face_mismatch" for _, r in finished(env))


def test_confirmed_mismatch_is_logged_as_an_attempt_and_the_session_stays_open(env):
    """Sai mặt KHÔNG đóng phiên: chỉ báo một lần thử (ghi log), người dùng vẫn quét tiếp được trong thời gian của phiên."""
    run(env, Face(sim=0.10), frames=main.MISMATCH_FRAMES)
    assert finished(env) == [("attempt", "face_mismatch")]
    assert main.ai_state["is_active"] is True and main.ai_state["result"] == "scanning"


def test_attempts_are_throttled_so_a_fast_camera_does_not_flood_the_log(env, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(main.time, "time", lambda: clock[0])
    run(env, Face(sim=0.10), frames=main.MISMATCH_FRAMES * 4)
    assert len(env[2]) == 1, "cùng một khoảng thời gian chỉ ghi một lần thử"
    clock[0] += main.ATTEMPT_MIN_INTERVAL_S + 0.1
    run(env, Face(sim=0.10), frames=main.MISMATCH_FRAMES)
    assert len(env[2]) == 2


def test_after_a_mismatch_attempt_the_right_person_can_still_pass(env):
    run(env, Face(sim=0.10), frames=main.MISMATCH_FRAMES)
    run(env, Face(sim=0.9), frames=main.REQUIRED_FRAMES + main.PAD_MIN_FRAMES + 2)
    assert env[3].challenge is not None, "đúng người xuất hiện sau đó vẫn đi tiếp tới thử thách"
    assert main.ai_state["is_active"] is True


def test_inconclusive_band_never_accuses_even_after_many_frames(env):
    run(env, Face(sim=(main.MISMATCH_CONFIRM_SIM + main.SIMILARITY_THRESHOLD) / 2), frames=main.MISMATCH_FRAMES * 3)
    assert finished(env) == []
    assert env[3].inconclusive >= main.MISMATCH_FRAMES * 3


def test_classify_similarity_bands():
    assert main.classify_similarity(0.9) == "match"
    assert main.classify_similarity(main.SIMILARITY_THRESHOLD) == "match"
    assert main.classify_similarity(main.SIMILARITY_THRESHOLD - 0.01) == "inconclusive"
    assert main.classify_similarity(main.MISMATCH_CONFIRM_SIM) == "inconclusive"
    assert main.classify_similarity(main.MISMATCH_CONFIRM_SIM - 0.01) == "mismatch"


# ----------------------------------- chất lượng khung -----------------------------------
def test_blurry_frames_are_not_used_to_decide_anything(env):
    run(env, Face(sim=0.05), frames=main.MISMATCH_FRAMES * 2, img=FLAT)      # rất mờ + sai chủ thẻ: vẫn không kết luận
    assert finished(env) == []
    assert env[3].quality_low == main.MISMATCH_FRAMES * 2 and env[3].mismatch == 0
    assert main.ai_state["hint"] == "quality_low"


def test_unreliable_detection_is_low_quality(env):
    run(env, Face(sim=0.05, det=0.2), frames=main.MISMATCH_FRAMES * 2)
    assert finished(env) == [] and env[3].quality_low == main.MISMATCH_FRAMES * 2


def test_face_verifiable_thresholds():
    assert main.face_verifiable(0.8, 50.0) is True
    assert main.face_verifiable(0.8, main.VERIFY_MIN_SHARPNESS - 0.1) is False
    assert main.face_verifiable(main.VERIFY_MIN_DET_SCORE - 0.01, 50.0) is False


def test_real_esp32_like_frames_pass_the_quality_gate():
    """Số đo THẬT trên camera ESP32 qua WiFi: độ nét 9-39, dò mặt 0.62-0.76. Cổng chất lượng không được từ chối chúng."""
    for det, sharp in ((0.62, 9.1), (0.66, 39.2), (0.72, 20.7), (0.76, 20.3), (0.73, 128.0)):
        assert main.face_verifiable(det, sharp) is True, (det, sharp)


# ----------------------------------- PAD -----------------------------------
def test_pad_verdict_tiers():
    assert main.pad_verdict(False, 0.1) == "pending"
    assert main.pad_verdict(True, main.SPOOF_CONF_THRESHOLD - 0.01) == "spoof"
    assert main.pad_verdict(True, (main.SPOOF_CONF_THRESHOLD + main.PAD_THRESHOLD) / 2) == "uncertain"
    assert main.pad_verdict(True, main.PAD_THRESHOLD) == "ok"


def test_confident_spoof_is_denied_immediately(env):
    env[1].value = 0.10
    run(env, Face(sim=0.9), frames=main.PAD_WINDOW + 2)
    assert finished(env) == [("denied", "spoof_pad")]


def test_borderline_pad_does_not_accuse_and_never_starts_the_challenge(env):
    env[1].value = (main.SPOOF_CONF_THRESHOLD + main.PAD_THRESHOLD) / 2       # vd 0.64 như người thật đã bị chấm
    run(env, Face(sim=0.9), frames=40)
    assert finished(env) == []
    assert env[3].challenge is None, "PAD chưa đạt thì không được tiến tới cấp quyền"
    assert env[3].pad_uncertain is True


def test_healthy_pad_reaches_the_challenge(env):
    run(env, Face(sim=0.9), frames=main.REQUIRED_FRAMES + main.PAD_MIN_FRAMES + 2)
    assert env[3].challenge is not None


# ----------------------------------- phân loại khi hết giờ -----------------------------------
@pytest.mark.parametrize("kwargs, expected", [
    (dict(faces_seen=0, quality_low=0, inconclusive=0, pad_uncertain=False, matched=False), "face_not_detected"),
    (dict(faces_seen=10, quality_low=10, inconclusive=0, pad_uncertain=False, matched=False), "face_quality_low"),
    (dict(faces_seen=10, quality_low=7, inconclusive=0, pad_uncertain=False, matched=False), "face_quality_low"),
    (dict(faces_seen=10, quality_low=0, inconclusive=6, pad_uncertain=False, matched=False), "face_inconclusive"),
    (dict(faces_seen=10, quality_low=0, inconclusive=0, pad_uncertain=True, matched=True), "pad_uncertain"),
    (dict(faces_seen=10, quality_low=1, inconclusive=1, pad_uncertain=False, matched=True), "timeout"),
])
def test_timeout_is_classified_by_what_actually_happened(kwargs, expected):
    assert main.classify_timeout(**kwargs) == expected


def test_timeout_helper_reads_the_session_context(env):
    ctx = env[3]
    ctx.faces_seen, ctx.quality_low = 8, 8
    assert main.timeout_reason(ctx) == "face_quality_low"


# ----------------------------------- cửa sổ camera không được sập vì một gợi ý lạ -----------------------------------
HINTS_PRODUCED = ("no_face", "move_closer", "multiple_faces", "quality_low", "mismatch")


@pytest.mark.parametrize("hint", HINTS_PRODUCED + ("gợi ý chưa từng có",))
def test_overlay_renders_every_hint_without_crashing(env, hint):
    with main.state_lock:
        main.ai_state.update(hint=hint, challenge=None)
    frame = np.zeros((480, 640, 3), np.uint8)
    main.draw_overlay(frame, [], "", 5.0)            # trước đây: KeyError: 'mismatch' làm chết cả AI Service
    assert frame.any(), "phải vẽ được chữ lên khung hình"


def test_every_hint_the_pipeline_can_set_has_a_display_text():
    for hint in HINTS_PRODUCED:
        assert hint in main.HINT_TEXT
