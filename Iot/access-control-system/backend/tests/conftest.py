import os
import sys
import tempfile
from pathlib import Path

# Phải đặt trước khi import app: dùng DB tạm, không đụng data/lab.db thật
os.environ["LAB_DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/test.db"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import Base, engine
from app.main import app
from app.services import ai_client
from app.services.device_registry import registry
from app.services.notifier import notifier
from app.services.auth_policy import limiter

EMB = [0.0] * 512
EMB[0] = 1.0

DEVICE = {"X-Device-Token": "dev-device-token"}
SERVICE = {"X-Service-Token": "dev-service-token"}

GUIDE_STEP_IDS = ("front", "left", "right", "blink")


def guide_payload(step="front", index=0, **over):
    """Trả lời của AI /api/ai/enroll/* (enroll_guide.GuidedEnroll.snapshot + quality): đủ các trường như thật."""
    steps = [{"id": sid, "state": "done" if i < index else "active" if i == index else "pending"}
             for i, sid in enumerate(GUIDE_STEP_IDS)]
    return {"step": step, "step_index": index, "total": 4, "steps": steps, "hint": "", "passed_step": None,
            "step_timeout": False, "time_left": 15, "done": False, "quality": {}, **over}


@pytest.fixture(autouse=True)
def _fresh_rate_limiter():
    """Bộ giới hạn tần suất nằm trong bộ nhớ: mỗi test bắt đầu sạch, không bị test trước làm lệch."""
    limiter.clear()
    yield
    limiter.clear()


@pytest.fixture(autouse=True)
def _isolated_notifier(monkeypatch):
    """Test không bao giờ được gửi tin thật dù .env của máy có token Telegram / SMTP; lịch sử thông báo sạch mỗi test."""
    for name in ("telegram_bot_token", "telegram_chat_id", "smtp_host", "smtp_to", "smtp_user", "smtp_from", "public_base_url"):
        monkeypatch.setattr(settings, name, "")
    notifier.reset()
    yield
    notifier.reset()


@pytest.fixture()
def fake_ai(monkeypatch):
    """AI Service giả: ghi lại các lần gọi, trả vector cố định."""
    calls = {"trigger": [], "cancel": 0, "status": None, "guide": [], "guide_reply": None}

    def enroll_frame(session_id, image, command=None):
        calls["guide"].append((session_id, image, command))
        return calls["guide_reply"] or guide_payload()

    monkeypatch.setattr(ai_client, "enroll_start", lambda: {"session_id": "S1", **guide_payload()})
    monkeypatch.setattr(ai_client, "enroll_frame", enroll_frame)
    monkeypatch.setattr(ai_client, "trigger", lambda sid, sess, emb: calls["trigger"].append((sid, sess, len(emb))) or {})
    monkeypatch.setattr(ai_client, "cancel", lambda: calls.__setitem__("cancel", calls["cancel"] + 1))
    monkeypatch.setattr(ai_client, "status", lambda: calls["status"])
    monkeypatch.setattr(ai_client, "health", lambda: None)
    monkeypatch.setattr(ai_client, "embed",
                        lambda data: {"ok": True, "embedding": EMB} if data != b"bad" else {"ok": False, "reason": "no_face"})
    return calls


@pytest.fixture()
def client(fake_ai):
    registry.clear()
    Base.metadata.drop_all(engine)      # mỗi test một DB sạch
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def admin(client):
    assert client.post("/api/v1/auth/login", json={"student_id": "admin", "password": "admin123"}).status_code == 200
    return client


@pytest.fixture()
def student(admin):
    """Tạo sinh viên có thẻ A123 và đã đăng ký mặt; trả về id."""
    r = admin.post("/api/v1/users", json={"student_id": "B23DCAT286", "full_name": "SV A",
                                          "password": "123456", "card_uid": "a123"})
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    r = admin.put(f"/api/v1/users/{uid}/face", files=[("images", (f"{i}.jpg", b"ok")) for i in range(3)])
    assert r.status_code == 200, r.text
    return uid
