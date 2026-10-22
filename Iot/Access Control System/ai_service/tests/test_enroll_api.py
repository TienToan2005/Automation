"""Kiểm thử các đường /api/ai/enroll/* của AI Service. Mô hình (face_app) và đồng hồ là giả; HTTP, giải mã ảnh,
kiểm tra chất lượng (enrollment.inspect_image) và máy trạng thái (enroll_guide) chạy thật.

Chạy:  python -m pytest tests -q      (từ thư mục ai_service)
"""
import base64
import os
import sys
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import enroll_guide
import main
from test_enroll_guide import SIZE, Clock, Driver, make_face, to_blink_step

_NOISE = np.random.RandomState(0).randint(0, 255, (SIZE[1], SIZE[0], 3)).astype(np.uint8)    # đủ nét để qua kiểm tra độ nét
IMAGE_B64 = base64.b64encode(cv2.imencode(".jpg", _NOISE, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tobytes()).decode()


class ScriptedApp:
    """face_app giả: mỗi lần get() trả về danh sách khuôn mặt kế tiếp trong kịch bản (hết kịch bản = không thấy mặt)."""

    def __init__(self):
        self.script = []

    def get(self, img):
        return self.script.pop(0) if self.script else []


@pytest.fixture()
def api(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(enroll_guide.time, "time", clock)
    app = ScriptedApp()
    monkeypatch.setattr(main, "face_app", app)
    monkeypatch.setattr(main, "pad", None)
    monkeypatch.setattr(main, "enroll_sessions", enroll_guide.SessionStore())
    return SimpleNamespace(client=TestClient(main.api_app), app=app, clock=clock)


def start(api):
    r = api.client.post("/api/ai/enroll/start")
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def send(api, sid, **body):
    return api.client.post("/api/ai/enroll/frame", json={"session_id": sid, **body})


def test_start_opens_a_session_at_the_first_step(api):
    body = api.client.post("/api/ai/enroll/start").json()
    assert body["session_id"] and body["step"] == "front" and body["total"] == 4 and body["done"] is False


def test_start_is_503_while_the_models_are_still_loading(api, monkeypatch):
    monkeypatch.setattr(main, "face_app", None)
    assert api.client.post("/api/ai/enroll/start").status_code == 503


def test_frame_for_an_unknown_session_is_404(api):
    assert send(api, "khong-co", image_base64=IMAGE_B64).status_code == 404


def test_front_step_passes_after_a_steady_hold_over_http(api):
    sid = start(api)
    results = []
    for _ in range(4):
        api.clock.t += 0.2
        api.app.script.append([make_face(offset=0.06)])
        results.append(send(api, sid, image_base64=IMAGE_B64).json())
    assert [r["passed_step"] for r in results] == [None, None, None, "front"]
    assert results[-1]["step"] == "left"


def test_quality_problem_is_reported_as_a_hint_and_nothing_passes(api):
    sid = start(api)
    api.app.script.append([make_face(), make_face()])                 # hai người trong khung
    body = send(api, sid, image_base64=IMAGE_B64).json()
    assert body["hint"] == "multiple_faces" and body["passed_step"] is None


def test_response_carries_the_quality_numbers_for_calibration(api):
    sid = start(api)
    api.app.script.append([make_face()])
    quality = send(api, sid, image_base64=IMAGE_B64).json()["quality"]
    assert quality["face_width"] == pytest.approx(192, abs=1) and quality["sharpness"] > 60


def test_response_carries_the_head_pose_numbers_the_steps_are_decided_on(api):
    sid = start(api)
    api.app.script.append([make_face(offset=0.12, ear=0.8)])
    pose = send(api, sid, image_base64=IMAGE_B64).json()["pose"]
    assert pose["offset"] == pytest.approx(0.12, abs=1e-3)               # độ lệch mũi: thước đo quay đầu
    assert pose["ear"] == pytest.approx(0.8 * 0.327, abs=1e-3)           # EAR: thước đo chớp mắt


def test_frame_without_a_face_has_no_pose_numbers(api):
    assert send(api, start(api), image_base64=IMAGE_B64).json()["pose"] == {}


def test_debug_flag_prints_one_line_per_frame_for_calibration(api, monkeypatch, capsys):
    monkeypatch.setattr(main, "ENROLL_DEBUG", True)
    sid = start(api)
    api.app.script.append([make_face(offset=0.12)])
    send(api, sid, image_base64=IMAGE_B64)
    line = capsys.readouterr().out
    assert "[ENROLL]" in line and "front" in line and "0.12" in line


def test_undecodable_image_is_400(api):
    assert send(api, start(api), image_base64="khong-phai-anh").status_code == 400


def test_command_without_an_image_returns_the_current_state(api):
    sid = start(api)
    r = send(api, sid, command="retry")
    assert r.status_code == 200 and r.json()["step"] == "front"


def test_unknown_command_is_400(api):
    assert send(api, start(api), command="nuke").status_code == 400


def test_skip_is_refused_before_the_blink_step(api):
    assert send(api, start(api), command="skip").status_code == 400


def test_blink_step_can_be_skipped_over_http(api):
    sid, guide = main.enroll_sessions.start()
    drv = Driver(api.clock)
    drv.guide = guide                                                  # đưa phiên tới bước chớp mắt bằng máy trạng thái thật
    to_blink_step(drv)
    body = send(api, sid, command="skip").json()
    assert body["done"] is True and [s["state"] for s in body["steps"]][-1] == "skipped"
