import base64
import uuid

from .conftest import DEVICE, SERVICE


def scan(client, uid="A123"):
    return client.post("/api/v1/access/card-scan", json={"device_id": "door-esp32-01", "uid": uid}, headers=DEVICE)


def verify(client, session_id, **kw):
    ev = {"camera_id": "cam1", "student_id": "B23DCAT286", "confidence_score": 0.89, "session_id": session_id,
          "result": "granted", "reason": "ok",
          "liveness": {"passed": True, "pad_score": 0.95, "challenge_type": "blink"}, "duration_ms": 4000, **kw}
    return client.post("/api/v1/access/verify", json=ev, headers=SERVICE)


def heartbeat(client, **kw):
    body = {"device_id": "door-esp32-01", "state": "WAIT_CARD", "relay": 0, "rssi": -60, "uptime_s": 5, "ip": "10.0.0.2", **kw}
    return client.post("/api/v1/device/heartbeat", json=body, headers=DEVICE)


# ------------------------------ Đăng nhập / phân quyền ------------------------------
def test_pages_redirect_to_login_when_anonymous(client):
    assert client.get("/", follow_redirects=False).status_code == 303
    assert client.get("/login").status_code == 200


def test_login_wrong_password(client):
    r = client.post("/api/v1/auth/login", json={"student_id": "admin", "password": "sai"})
    assert r.status_code == 401


def test_admin_pages_render(admin, student):
    for path in ("/dashboard", "/users", f"/users/{student}", "/logs", "/alerts", "/openapi.json"):
        assert admin.get(path).status_code == 200, path


def test_student_has_no_admin_rights(client, student):
    client.post("/api/v1/auth/logout")
    assert client.post("/api/v1/auth/login", json={"student_id": "B23DCAT286", "password": "123456"}).status_code == 200
    assert client.get("/api/v1/users").status_code == 403
    assert client.get("/api/v1/alerts").status_code == 403
    assert client.get("/api/v1/devices").status_code == 403
    assert client.get("/api/v1/system/status").status_code == 403
    assert client.post("/api/v1/door/emergency-open", json={"reason": "muốn vào phòng"}).status_code == 403
    assert client.get("/dashboard").status_code == 403
    assert client.get("/me").status_code == 200
    assert client.get("/api/v1/users/1").status_code == 403           # hồ sơ admin


def test_student_sees_only_own_logs_and_enrolls_own_face(client, student):
    scan(client)    # tạo log cho SV
    client.post("/api/v1/auth/logout")
    client.post("/api/v1/auth/login", json={"student_id": "B23DCAT286", "password": "123456"})
    logs = client.get("/api/v1/access-logs").json()
    assert logs["total"] > 0 and all(l["student_id"] == "B23DCAT286" for l in logs["items"])
    imgs = [("images", (f"{i}.jpg", b"ok")) for i in range(3)]
    assert client.put(f"/api/v1/users/{student}/face", files=imgs).status_code == 200
    assert client.put("/api/v1/users/1/face", files=imgs).status_code == 403


def test_suspended_account_cannot_login_or_use_token(admin, student):
    admin.post(f"/api/v1/users/{student}/suspend", json={"reason": "test"})
    admin.post("/api/v1/auth/logout")
    r = admin.post("/api/v1/auth/login", json={"student_id": "B23DCAT286", "password": "123456"})
    assert r.status_code == 403


# ------------------------------ Đăng ký mặt ------------------------------
def test_face_enroll_needs_enough_valid_images(admin):
    uid = admin.post("/api/v1/users", json={"student_id": "S2", "full_name": "B", "password": "123456"}).json()["id"]
    r = admin.put(f"/api/v1/users/{uid}/face", files=[("images", ("a.jpg", b"bad")), ("images", ("b.jpg", b"ok"))])
    assert r.status_code == 422
    assert r.json()["detail"]["errors"][0]["reason"] == "no_face"


# ------------------------------ Luồng quẹt thẻ -> AI -> mở cửa ------------------------------
def test_full_access_flow(admin, student, fake_ai):
    assert scan(admin, "A123").status_code == 202          # (không cần đăng nhập, dùng device token)
    r = scan(admin)
    body = r.json()
    assert r.status_code == 202 and body["action"] == "scan_face"
    sid = body["data"]["session_id"]
    assert fake_ai["trigger"][-1] == ("B23DCAT286", sid, 512)

    # Đang chờ: lấy gợi ý OLED từ AI
    fake_ai["status"] = {"session_id": sid, "hint": "move_closer", "challenge": {"type": "blink", "text": "HAY CHOP MAT"}, "time_left": 9}
    st = admin.get(f"/api/v1/access/session/{sid}", headers=DEVICE).json()
    assert st == {"status": "pending", "reason": "", "hint": "move_closer", "challenge_type": "blink",
                  "challenge_text": "HAY CHOP MAT", "time_left": 9}

    # AI báo granted
    r = verify(admin, sid)
    assert r.status_code == 200 and r.json()["action"] == "open_door"
    assert admin.get(f"/api/v1/access/session/{sid}", headers=DEVICE).json()["status"] == "granted"

    # Gửi lại cùng phiên không ghi trùng
    verify(admin, sid)
    assert admin.get("/api/v1/access-logs?event=face_verify").json()["total"] == 1


def test_card_scan_requires_device_token(client, student):
    assert client.post("/api/v1/access/card-scan", json={"device_id": "d", "uid": "A123"}).status_code == 401


def test_card_scan_denials(admin, student):
    assert scan(admin, "ZZZZ").json()["data"]["reason"] == "unknown_card"

    admin.post(f"/api/v1/users/{student}/suspend", json={"reason": "x"})
    assert scan(admin).json()["data"]["reason"] == "account_suspended"
    admin.post(f"/api/v1/users/{student}/activate")

    admin.delete(f"/api/v1/users/{student}/face")
    assert scan(admin).json()["data"]["reason"] == "not_enrolled"


def test_ai_unavailable_denies(admin, student, monkeypatch):
    def boom(*a):
        raise ai_client.AIUnavailable("down")
    from app.services import ai_client
    monkeypatch.setattr(ai_client, "trigger", boom)
    r = scan(admin)
    assert r.status_code == 403 and r.json()["data"]["reason"] == "ai_unavailable"


def test_revoked_card_raises_alert(admin, student):
    card_id = admin.get(f"/api/v1/users/{student}").json()["cards"][0]["id"]
    admin.post(f"/api/v1/cards/{card_id}/revoke")
    assert scan(admin).json()["data"]["reason"] == "card_revoked"
    assert any(a["reason"] == "revoked_card_used" for a in admin.get("/api/v1/alerts").json())


def test_spoof_creates_alert_with_snapshot(admin, student):
    sid = uuid.uuid4().hex[:12]
    r = verify(admin, sid, result="denied", reason="spoof_pad",
               snapshot_jpeg_base64=base64.b64encode(b"jpegdata").decode())
    assert r.json()["action"] == "none"
    alerts = admin.get("/api/v1/alerts").json()
    assert alerts[0]["level"] == "high" and alerts[0]["snapshot_url"]
    assert admin.get(alerts[0]["snapshot_url"]).content == b"jpegdata"
    assert admin.get("/api/v1/snapshots/..%2F..%2Fapp%2Fmain.py").status_code == 404


def test_granted_is_denied_if_account_suspended_mid_scan(admin, student):
    admin.post(f"/api/v1/users/{student}/suspend", json={"reason": "x"})
    r = verify(admin, "late00000001")
    assert r.status_code == 403 and r.json()["action"] == "none"


def test_cancel_session_calls_ai(admin, student, fake_ai):
    sid = scan(admin).json()["data"]["session_id"]
    assert admin.post(f"/api/v1/access/session/{sid}/cancel", headers=DEVICE).status_code == 200
    assert fake_ai["cancel"] == 1


def test_unknown_session_404(admin):
    assert admin.get("/api/v1/access/session/nope", headers=DEVICE).status_code == 404


# ------------------------------ Điều khiển thiết bị từ web ------------------------------
def test_device_heartbeat_and_status(admin):
    assert admin.post("/api/v1/device/heartbeat", json={"device_id": "x"}).status_code == 401
    assert heartbeat(admin, state="WAIT_FACE", relay=1).json() == {"commands": []}
    dev = admin.get("/api/v1/devices").json()[0]
    assert dev["online"] and dev["state"] == "WAIT_FACE" and dev["relay"] == 1 and dev["rssi"] == -60


def test_remote_open_reaches_device_once(admin):
    heartbeat(admin)
    assert admin.post("/api/v1/door/emergency-open", json={"reason": "ab"}).status_code == 400   # lý do quá ngắn
    r = admin.post("/api/v1/door/emergency-open", json={"reason": "Quên thẻ, cần lấy thiết bị"})
    assert r.status_code == 200
    cmds = heartbeat(admin).json()["commands"]
    assert cmds == [{"command": "open_lock", "duration_ms": 5000, "reason": "Quên thẻ, cần lấy thiết bị"}]
    assert heartbeat(admin).json()["commands"] == []                       # lệnh chỉ giao một lần
    logs = admin.get("/api/v1/access-logs?event=remote_open").json()
    assert logs["total"] == 1 and logs["items"][0]["note"].startswith("Quên thẻ")


def test_remote_command_fails_when_device_offline(admin):
    r = admin.post("/api/v1/door/emergency-open", json={"reason": "Cần mở cửa gấp"})
    assert r.status_code == 409
    assert admin.get("/api/v1/access-logs?event=remote_open").json()["total"] == 0


def test_reset_lockout_command(admin):
    heartbeat(admin, state="LOCKOUT")
    admin.post("/api/v1/devices/door-esp32-01/commands", json={"command": "reset_lockout"})
    assert heartbeat(admin).json()["commands"][0]["command"] == "reset_lockout"


def test_exit_button_logged(admin):
    admin.post("/api/v1/access/exit", json={"device_id": "door-esp32-01"}, headers=DEVICE)
    assert admin.get("/api/v1/access-logs?event=exit_button").json()["total"] == 1
