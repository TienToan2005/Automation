"""Hợp đồng giữa backend và AI Service cho quét mặt có hướng dẫn: ai_client.enroll_* đổi mã HTTP của AI thành ngoại lệ
mà tầng dịch vụ hiểu. `requests.post` là thứ duy nhất bị thay thế (mạng), còn lại chạy thật."""
import base64

import pytest
import requests

from app.services import ai_client


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body if body is not None else {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def fake_post(monkeypatch, response=None, error=None):
    sent = {}

    def post(url, json=None, timeout=None, **kwargs):
        sent.update(url=url, json=json, timeout=timeout)
        if error:
            raise error
        return response
    monkeypatch.setattr(ai_client.requests, "post", post)
    return sent


def test_enroll_start_returns_the_ai_payload(monkeypatch):
    sent = fake_post(monkeypatch, Resp(200, {"session_id": "S1", "step": "front"}))
    assert ai_client.enroll_start() == {"session_id": "S1", "step": "front"}
    assert sent["url"].endswith("/api/ai/enroll/start")


def test_enroll_frame_sends_the_image_as_base64_and_the_command_only_when_given(monkeypatch):
    sent = fake_post(monkeypatch, Resp(200, {"step": "left"}))
    ai_client.enroll_frame("S1", b"\xff\xd8jpeg", "retry")
    assert sent["url"].endswith("/api/ai/enroll/frame")
    assert sent["json"] == {"session_id": "S1", "image_base64": base64.b64encode(b"\xff\xd8jpeg").decode(), "command": "retry"}
    ai_client.enroll_frame("S1", None, None)
    assert sent["json"] == {"session_id": "S1"}


def test_enroll_frame_unknown_session_is_a_distinct_error(monkeypatch):
    fake_post(monkeypatch, Resp(404, {"detail": "Unknown or expired session"}))
    with pytest.raises(ai_client.AISessionExpired):
        ai_client.enroll_frame("S1", b"x")


def test_enroll_frame_refusal_carries_the_ais_reason(monkeypatch):
    fake_post(monkeypatch, Resp(400, {"detail": "Only the blink step can be skipped"}))
    with pytest.raises(ai_client.AIRejected, match="blink step"):
        ai_client.enroll_frame("S1", None, "skip")


@pytest.mark.parametrize("response, error", [
    (Resp(503, {"detail": "Model not ready"}), None),                  # AI đang nạp mô hình
    (Resp(500, {}), None),
    (None, requests.ConnectionError("refused")),                       # AI chưa chạy
    (None, requests.Timeout("slow")),
])
def test_enroll_frame_ai_outage_is_unavailable(monkeypatch, response, error):
    fake_post(monkeypatch, response, error)
    with pytest.raises(ai_client.AIUnavailable):
        ai_client.enroll_frame("S1", b"x")


def test_enroll_start_ai_outage_is_unavailable(monkeypatch):
    fake_post(monkeypatch, error=requests.ConnectionError("refused"))
    with pytest.raises(ai_client.AIUnavailable):
        ai_client.enroll_start()
