import os
import sys
import tempfile
from pathlib import Path

# Phải đặt trước khi import app: dùng DB tạm, không đụng data/lab.db thật
os.environ["LAB_DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/test.db"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

from app.core.database import Base, engine
from app.main import app
from app.services import ai_client
from app.services.device_registry import registry

EMB = [0.0] * 512
EMB[0] = 1.0

DEVICE = {"X-Device-Token": "dev-device-token"}
SERVICE = {"X-Service-Token": "dev-service-token"}


@pytest.fixture()
def fake_ai(monkeypatch):
    """AI Service giả: ghi lại các lần gọi, trả vector cố định."""
    calls = {"trigger": [], "cancel": 0, "status": None}
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
