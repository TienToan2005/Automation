"""Đẩy sự kiện cửa lên ThingsBoard: chỉ khi đã cấu hình, chỉ sau khi commit, lỗi mạng không làm hỏng request."""
import pytest

from app.core.config import settings
from app.services import thingsboard

URL = "https://tb.example/api/v1/TOKEN/telemetry"


@pytest.fixture()
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(thingsboard, "_dispatch", out.append)
    return out


def _scan(client):
    return client.post("/api/v1/access/card-scan", json={"device_id": "door-esp32-01", "uid": "zzzz"}, headers={"X-Device-Token": "dev-device-token"})


def test_disabled_by_default_sends_nothing(client, sent):
    _scan(client)
    assert sent == []


def test_card_scan_is_pushed_after_commit(client, sent, monkeypatch):
    monkeypatch.setattr(settings, "thingsboard_url", URL)
    _scan(client)
    assert len(sent) == 1
    p = sent[0]
    assert p["event"] == "card_scan" and p["result"] == "denied" and p["reason"]
    assert p["granted"] == 0 and "ts" not in p


def test_granted_flag(client, sent, monkeypatch, student):
    monkeypatch.setattr(settings, "thingsboard_url", URL)
    sent.clear()
    r = client.post("/api/v1/access/card-scan", json={"device_id": "door-esp32-01", "uid": "a123"}, headers={"X-Device-Token": "dev-device-token"})
    assert r.status_code == 202
    assert sent and sent[-1]["student_id"] == "B23DCAT286"


def test_rollback_sends_nothing(sent, monkeypatch):
    from app.core.database import SessionLocal
    from app.models import AccessLog
    monkeypatch.setattr(settings, "thingsboard_url", URL)
    with SessionLocal() as db:
        db.add(AccessLog(event="card_scan", result="denied", reason="x"))
        db.flush()
        db.rollback()
    assert sent == []


def test_network_error_is_swallowed(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("mat mang")
    monkeypatch.setattr(thingsboard.requests, "post", boom)
    monkeypatch.setattr(settings, "thingsboard_url", URL)
    thingsboard._post({"event": "x"})          # không được ném ra


def test_post_uses_configured_url(monkeypatch):
    seen = {}
    monkeypatch.setattr(thingsboard.requests, "post", lambda url, json=None, timeout=None: seen.update(url=url, json=json, t=timeout))
    monkeypatch.setattr(settings, "thingsboard_url", URL)
    thingsboard._post({"event": "x"})
    assert seen["url"] == URL and seen["json"] == {"event": "x"} and seen["t"]
