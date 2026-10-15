import base64
import uuid

import pytest

from app.core.config import SNAPSHOT_DIR, settings
from app.services import ai_client, notifier as notifier_module
from app.services.device_registry import registry
from app.services.notifier import notifier

from .test_flow import heartbeat, scan, verify


def feed(client, after=None):
    q = "" if after is None else f"?after={after}"
    r = client.get("/api/v1/notifications" + q)
    assert r.status_code == 200, r.text
    return r.json()


def spoof(client, sid=None):
    return verify(client, sid or uuid.uuid4().hex[:12], result="denied", reason="spoof_pad",
                  snapshot_jpeg_base64=base64.b64encode(b"jpegdata").decode())


# ------------------------------ Chuông trên web ------------------------------
def test_notifications_are_admin_only(client, student):
    client.post("/api/v1/auth/logout")                                    # fixture `student` đã đăng nhập admin
    assert client.get("/api/v1/notifications").status_code == 401
    client.post("/api/v1/auth/login", json={"student_id": "B23DCAT286", "password": "123456"})
    assert client.get("/api/v1/notifications").status_code == 403
    assert client.post("/api/v1/notifications/test").status_code == 403


def test_spoof_attempt_notifies_admin_with_snapshot(admin, student):
    spoof(admin)
    page = feed(admin)
    [n] = page["items"]
    assert n["kind"] == "alert" and n["level"] == "high" and n["student_id"] == "B23DCAT286"
    assert "giả mạo" in n["title"] and "SV A" in n["message"] and n["link"] == "/alerts"
    assert admin.get(n["snapshot_url"]).content == b"jpegdata"
    assert page["last_id"] == n["id"] and page["boot"] == notifier.boot


def test_cursor_returns_only_newer_events(admin, student):
    spoof(admin)
    last = feed(admin)["last_id"]
    assert feed(admin, after=last)["items"] == []
    verify(admin, uuid.uuid4().hex[:12], result="denied", reason="face_mismatch")
    [n] = feed(admin, after=last)["items"]
    assert n["level"] == "medium" and n["id"] > last


def test_granted_entry_is_not_a_notification(admin, student):
    sid = scan(admin).json()["data"]["session_id"]
    verify(admin, sid)
    assert feed(admin)["items"] == []


def test_revoked_card_and_ai_outage_notify(admin, student, monkeypatch):
    card_id = admin.get(f"/api/v1/users/{student}").json()["cards"][0]["id"]
    admin.post(f"/api/v1/cards/{card_id}/revoke")
    scan(admin)
    [n] = feed(admin)["items"]
    assert n["kind"] == "alert" and n["level"] == "high" and "thu hồi" in n["title"]

    uid = admin.post("/api/v1/users", json={"student_id": "S9", "full_name": "SV Chín", "password": "123456",
                                            "card_uid": "9999"}).json()["id"]
    admin.put(f"/api/v1/users/{uid}/face", files=[("images", (f"{i}.jpg", b"ok")) for i in range(3)])

    def down(*a):
        raise ai_client.AIUnavailable("down")
    monkeypatch.setattr(ai_client, "trigger", down)
    scan(admin, "9999")
    kinds = [n["kind"] for n in feed(admin)["items"]]
    assert kinds == ["alert", "ai_unavailable"]


def test_remote_open_is_announced(admin):
    heartbeat(admin)
    admin.post("/api/v1/door/emergency-open", json={"reason": "Quên thẻ, cần lấy thiết bị"})
    remote = [n for n in feed(admin)["items"] if n["kind"] == "remote_open"]
    assert len(remote) == 1 and remote[0]["level"] == "info" and "Quên thẻ" in remote[0]["message"]


def test_device_online_lockout_offline_transitions(admin):
    kinds = lambda: [n["kind"] for n in feed(admin)["items"]]
    heartbeat(admin)
    heartbeat(admin)                                             # heartbeat đều đặn: không báo thêm
    assert kinds() == ["device_online"]
    heartbeat(admin, state="LOCKOUT")
    heartbeat(admin, state="LOCKOUT")                            # vẫn đang khóa: chỉ báo một lần
    assert kinds() == ["device_online", "device_lockout"]

    registry._devices["door-esp32-01"].last_seen -= settings.device_offline_after_s + 5      # thiết bị im lặng
    assert notifier.check_devices(registry) == ["door-esp32-01"]
    assert notifier.check_devices(registry) == []                # mỗi đợt offline chỉ báo một lần
    heartbeat(admin)                                             # nối lại
    assert kinds() == ["device_online", "device_lockout", "device_offline", "device_online"]


def test_server_restart_is_detectable_by_boot_id(admin):
    boot = feed(admin)["boot"]
    assert boot == notifier.boot and len(boot) == 8


# ------------------------------ Telegram / Email ------------------------------
class FakeResponse:
    def __init__(self, ok=True, status=200, body=None):
        self.ok, self.status_code, self._body = ok, status, body or {"ok": True}
        self.text = str(self._body)

    def json(self):
        return self._body


@pytest.fixture()
def telegram(monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", "123:SECRET-TOKEN")
    monkeypatch.setattr(settings, "telegram_chat_id", "-100777")
    monkeypatch.setattr(settings, "public_base_url", "http://lab.local:8080/")
    calls = []

    def fake_post(url, **kw):
        calls.append({"url": url, **kw})
        return FakeResponse()
    monkeypatch.setattr(notifier_module.requests, "post", fake_post)
    return calls


def test_high_alert_is_pushed_to_telegram_with_photo(admin, student, telegram):
    spoof(admin)
    notifier.flush()
    [call] = telegram
    assert call["url"] == "https://api.telegram.org/bot123:SECRET-TOKEN/sendPhoto"
    assert call["data"]["chat_id"] == "-100777"
    caption = call["data"]["caption"]
    assert "🚨" in caption and "giả mạo" in caption and "SV A" in caption and "http://lab.local:8080/alerts" in caption
    assert call["files"]["photo"][1] == b"jpegdata"


def test_telegram_text_message_without_snapshot_and_local_time(admin, telegram):
    heartbeat(admin)
    registry._devices["door-esp32-01"].last_seen -= 99
    notifier.check_devices(registry)                             # mất kết nối = mức medium: được đẩy
    notifier.flush()
    offline = [c for c in telegram if "mất kết nối" in c["data"]["text"]]
    assert len(offline) == 1 and offline[0]["url"].endswith("/sendMessage")
    assert "(UTC+7)" in offline[0]["data"]["text"]


def test_min_level_filter_and_cooldown(admin, telegram, monkeypatch):
    heartbeat(admin)                                             # "online" là mức info: chỉ lên chuông web
    notifier.flush()
    assert telegram == []

    for _ in range(3):                                           # cùng loại, cùng người: chỉ đẩy một lần
        verify(admin, uuid.uuid4().hex[:12], result="denied", reason="face_mismatch")
    notifier.flush()
    assert len(telegram) == 1
    assert len([n for n in feed(admin)["items"] if n["kind"] == "alert"]) == 3        # web vẫn thấy cả ba

    monkeypatch.setattr(settings, "notify_cooldown_s", 0)
    verify(admin, uuid.uuid4().hex[:12], result="denied", reason="face_mismatch")
    notifier.flush()
    assert len(telegram) == 2

    monkeypatch.setattr(settings, "notify_min_level", "high")
    verify(admin, uuid.uuid4().hex[:12], result="denied", reason="face_mismatch")      # medium < high
    notifier.flush()
    assert len(telegram) == 2


def test_telegram_failure_never_breaks_the_request_and_hides_token(admin, student, telegram, monkeypatch):
    def boom(url, **kw):
        raise ConnectionError(f"HTTPSConnectionPool: Max retries exceeded with url: {url}")
    monkeypatch.setattr(notifier_module.requests, "post", boom)
    assert spoof(admin).status_code == 200                       # nghiệp vụ chính không bị ảnh hưởng
    notifier.flush()
    result = admin.post("/api/v1/notifications/test").json()
    assert result["external"]["telegram"]["ok"] is False
    assert "SECRET-TOKEN" not in result["external"]["telegram"]["error"]


def test_telegram_http_error_reports_description(admin, monkeypatch, telegram):
    monkeypatch.setattr(notifier_module.requests, "post",
                        lambda url, **kw: FakeResponse(ok=False, status=401, body={"ok": False, "description": "Unauthorized"}))
    result = admin.post("/api/v1/notifications/test").json()["external"]["telegram"]
    assert result == {"ok": False, "error": "RuntimeError: Telegram trả HTTP 401: Unauthorized"}


def test_test_endpoint_reaches_every_configured_channel_regardless_of_level(admin, telegram):
    assert admin.post("/api/v1/notifications/test").json() == {"web": True, "external": {"telegram": {"ok": True, "error": None}}}
    assert [n["kind"] for n in feed(admin)["items"]] == ["test"]
    assert len(telegram) == 1


def test_test_endpoint_without_channels_only_hits_web(admin):
    assert admin.post("/api/v1/notifications/test").json() == {"web": True, "external": {}}
    assert admin.get("/api/v1/system/status").json()["notify"] == {
        "web": True, "telegram": False, "email": False, "min_level": "medium", "cooldown_s": 30}


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.log, self.sent = host, port, [], None
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        self.log.append("starttls")

    def login(self, user, password):
        self.log.append(("login", user, password))

    def send_message(self, msg):
        self.sent = msg


def test_email_channel(admin, student, monkeypatch):
    for k, v in dict(smtp_host="smtp.example.com", smtp_user="bot@example.com", smtp_password="app-pass",
                     smtp_to="a@example.com, b@example.com").items():
        monkeypatch.setattr(settings, k, v)
    FakeSMTP.instances.clear()
    monkeypatch.setattr(notifier_module.smtplib, "SMTP", FakeSMTP)
    spoof(admin)
    notifier.flush()
    [smtp] = FakeSMTP.instances
    assert (smtp.host, smtp.port) == ("smtp.example.com", 587)
    assert smtp.log == ["starttls", ("login", "bot@example.com", "app-pass")]
    msg = smtp.sent
    assert msg["To"] == "a@example.com, b@example.com" and msg["From"] == "bot@example.com"
    assert msg["Subject"].startswith("[Lab Access] Cao:") and "giả mạo" in msg["Subject"]
    assert [p.get_content_type() for p in msg.iter_attachments()] == ["image/jpeg"]


def test_email_ssl_port_and_incomplete_config(admin, monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")          # thiếu người nhận: kênh chưa bật
    assert notifier_module.channels()["email"] is False
    monkeypatch.setattr(settings, "smtp_to", "a@example.com")
    monkeypatch.setattr(settings, "smtp_from", "lab@example.com")
    monkeypatch.setattr(settings, "smtp_port", 465)
    seen = []
    monkeypatch.setattr(notifier_module.smtplib, "SMTP_SSL", lambda *a, **k: seen.append(a) or FakeSMTP(*a, **k))
    assert notifier_module.channels()["email"] is True
    assert admin.post("/api/v1/notifications/test").json()["external"]["email"]["ok"] is True
    assert seen and seen[0][1] == 465


def test_snapshot_name_cannot_escape_snapshot_dir(telegram):
    n = notifier.publish("alert", "high", "t", "m", snapshot="../../app/main.py")
    assert notifier_module._snapshot_path(n) is None or notifier_module._snapshot_path(n).parent == SNAPSHOT_DIR
