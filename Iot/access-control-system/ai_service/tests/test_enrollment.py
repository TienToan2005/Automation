"""Kiểm thử bước kiểm tra chất lượng ảnh đăng ký (enrollment.inspect_image / analyze_image) với khuôn mặt giả lập.

Chạy:  python -m pytest tests -q      (từ thư mục ai_service)
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import enrollment

MIN_FACE_WIDTH, PAD_THRESHOLD, MIN_SHARPNESS = 120, 0.70, 60


class FakeFace:
    """Đủ các trường insightface mà enrollment đọc: bbox, det_score, normed_embedding."""

    def __init__(self, width=160, det_score=0.9):
        self.bbox = np.array([100.0, 60.0, 100.0 + width, 60.0 + width * 1.25])
        self.det_score = det_score
        emb = np.zeros(512, np.float32)
        emb[3] = 1.0
        self.normed_embedding = emb


class FakeApp:
    def __init__(self, *faces):
        self.faces = list(faces)

    def get(self, img):
        return self.faces


class FakePad:
    enabled = True

    def __init__(self, score):
        self._score = score

    def score(self, img, bbox):
        return self._score


NOISE = np.random.RandomState(0).randint(0, 255, (480, 640, 3)).astype(np.uint8)   # rất nét
FLAT = np.full((480, 640, 3), 128, np.uint8)                                        # phương sai Laplacian = 0


def inspect(app, img=NOISE, pad=None):
    return enrollment.inspect_image(app, pad, img, MIN_FACE_WIDTH, PAD_THRESHOLD, MIN_SHARPNESS)


def analyze(app, img=NOISE, pad=None):
    return enrollment.analyze_image(app, pad, img, MIN_FACE_WIDTH, PAD_THRESHOLD, MIN_SHARPNESS)


# ----------------------- inspect_image: kết quả + đối tượng khuôn mặt, không có vector -----------------------
def test_inspect_no_face():
    res, face = inspect(FakeApp())
    assert res["ok"] is False and res["reason"] == "no_face" and face is None


def test_inspect_two_faces_is_rejected():
    res, face = inspect(FakeApp(FakeFace(), FakeFace()))
    assert res["reason"] == "multiple_faces" and face is None


def test_inspect_tiny_background_face_is_ignored():
    main = FakeFace(width=200)
    res, face = inspect(FakeApp(main, FakeFace(width=40)))
    assert res["ok"] is True and face is main


def test_inspect_low_confidence_background_detection_is_ignored():
    main = FakeFace(width=200)
    res, face = inspect(FakeApp(main, FakeFace(width=100, det_score=0.3)))
    assert res["ok"] is True and face is main


def test_inspect_second_person_of_real_size_is_still_rejected():
    res, face = inspect(FakeApp(FakeFace(width=200), FakeFace(width=90)))
    assert res["reason"] == "multiple_faces" and face is None


def test_inspect_small_face_is_low_quality_but_still_returns_the_face():
    small = FakeFace(width=60)            # < 120 * 0.75
    res, face = inspect(FakeApp(small))
    assert res["reason"] == "low_quality" and face is small


def test_inspect_unsure_detector_is_low_quality():
    res, _ = inspect(FakeApp(FakeFace(det_score=0.4)))
    assert res["reason"] == "low_quality"


def test_inspect_flat_image_is_blurry():
    res, _ = inspect(FakeApp(FakeFace()), img=FLAT)
    assert res["reason"] == "blurry" and res["sharpness"] == 0.0


def test_inspect_low_pad_score_is_spoof_suspected():
    res, _ = inspect(FakeApp(FakeFace()), pad=FakePad(0.30))
    assert res["reason"] == "spoof_suspected" and res["pad_score"] == 0.3


def test_inspect_good_face_returns_the_same_face_and_no_embedding():
    good = FakeFace()
    res, face = inspect(FakeApp(good), pad=FakePad(0.95))
    assert res["ok"] is True and res["reason"] == "ok" and res["pad_score"] == 0.95
    assert face is good
    assert "embedding" not in res


# ----------------------- analyze_image: hành vi cũ giữ nguyên (đặc tả hóa trước khi tách hàm) -----------------------
@pytest.mark.parametrize("app, img, pad, reason", [
    (FakeApp(), NOISE, None, "no_face"),
    (FakeApp(FakeFace(), FakeFace()), NOISE, None, "multiple_faces"),
    (FakeApp(FakeFace(width=60)), NOISE, None, "low_quality"),
    (FakeApp(FakeFace()), FLAT, None, "blurry"),
    (FakeApp(FakeFace()), NOISE, FakePad(0.30), "spoof_suspected"),
])
def test_analyze_rejections_carry_no_embedding(app, img, pad, reason):
    res = analyze(app, img, pad)
    assert res["ok"] is False and res["reason"] == reason
    assert "embedding" not in res


def test_analyze_good_face_returns_unit_512_embedding():
    res = analyze(FakeApp(FakeFace()), pad=FakePad(0.95))
    assert res["ok"] is True
    assert len(res["embedding"]) == 512 and res["embedding"][3] == 1.0
    assert res["face_width"] == 160.0 and res["pad_score"] == 0.95
