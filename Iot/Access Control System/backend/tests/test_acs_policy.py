"""Kiểm thử nghiệp vụ xác thực RFID + khuôn mặt: vòng đời phiên, trạng thái thẻ, khóa tạm, chống đua, audit.

Nhóm A thẻ | C phiên | D khóa tạm | E quyền truy cập | F audit / cảnh báo | cửa. (Nhóm B nhận diện chạy ở ai_service/tests.)
"""
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from app.core.clock import utcnow
from app.core.config import settings
from app.core.database import SessionLocal
from app.models import AccessLog, Alert, AuthSession, Card, LogEvent
from app.services.auth_policy import limiter

from .conftest import DEVICE, EMB, SERVICE
from .test_flow import heartbeat, scan, verify

DAY = 86400


@pytest.fixture(autouse=True)
def _policy_defaults(monkeypatch):
    limiter.clear()
    monkeypatch.setattr(settings, "scan_rate_per_uid_per_min", 1000)      # các test khóa tạm quẹt nhiều lần liên tiếp
    monkeypatch.setattr(settings, "scan_rate_per_device_per_min", 1000)
    yield
    limiter.clear()


@pytest.fixture()
def second(admin):
    r = admin.post("/api/v1/users", json={"student_id": "B23DCAT296", "full_name": "SV B", "password": "123456",
                                          "card_uid": "b456"})
    assert r.status_code == 201, r.text
    admin.put(f"/api/v1/users/{r.json()['id']}/face", files=[("images", (f"{i}.jpg", b"ok")) for i in range(3)])
    return r.json()["id"]


def db_card(uid="A123"):
    with SessionLocal() as db:
        return db.query(Card).filter(Card.uid == uid).first()


def set_card(uid="A123", **fields):
    with SessionLocal() as db:
        card = db.query(Card).filter(Card.uid == uid).first()
        for k, v in fields.items():
            setattr(card, k, v)
        db.commit()


def card_id(uid="A123"):
    return db_card(uid).id


def sess(session_id):
    with SessionLocal() as db:
        return db.query(AuthSession).filter(AuthSession.session_id == session_id).first()


def start(client, uid="A123"):
    r = scan(client, uid)
    assert r.status_code == 202, r.text
    return r.json()["data"]["session_id"]


def end(client, reason, result="denied", uid="A123", student="B23DCAT286"):
    sid = start(client, uid)
    r = verify(client, sid, result=result, reason=reason, student_id=student)
    assert r.status_code in (200, 403), r.text
    return sid


def logs(**flt):
    with SessionLocal() as db:
        q = db.query(AccessLog)
        for k, v in flt.items():
            q = q.filter(getattr(AccessLog, k) == v)
        return q.all()


def alerts(**flt):
    with SessionLocal() as db:
        q = db.query(Alert)
        for k, v in flt.items():
            q = q.filter(getattr(Alert, k) == v)
        return q.all()


# =============================== Nhóm A: RFID / trạng thái thẻ ===============================
def test_valid_card_opens_a_pending_session(student, client):
    r = scan(client)
    assert r.status_code == 202
    s = sess(r.json()["data"]["session_id"])
    assert s.state == "pending" and s.student_id == "B23DCAT286" and s.card_id == card_id()
    assert s.expires_at > utcnow() + timedelta(seconds=settings.face_timeout_s - 1)


def test_unknown_card_is_denied_without_session(student, client):
    r = scan(client, "FFFF")
    assert r.status_code == 403 and r.json()["data"]["reason"] == "unknown_card"
    with SessionLocal() as db:
        assert db.query(AuthSession).count() == 0


def test_expired_card_is_denied(student, client):
    set_card(expires_at=utcnow() - timedelta(seconds=1))
    r = scan(client)
    assert r.status_code == 403 and r.json()["data"]["reason"] == "card_expired"


def test_card_that_expires_later_still_works(student, client):
    set_card(expires_at=utcnow() + timedelta(days=30))
    assert scan(client).status_code == 202


def test_revoked_card_is_denied_and_alert_is_not_duplicated(student, client):
    set_card(status="revoked")
    for _ in range(5):
        assert scan(client).json()["data"]["reason"] == "card_revoked"
    assert len(alerts(reason="revoked_card_used")) == 1, "mỗi lần quẹt thẻ thu hồi không được tạo thêm cảnh báo"
    assert len(logs(event=LogEvent.CARD_SCAN, reason="card_revoked")) == 5, "nhưng mọi lần thử đều phải nằm trong nhật ký"


def test_disabled_card_is_denied(student, client):
    set_card(status="disabled")
    r = scan(client)
    assert r.status_code == 403 and r.json()["data"]["reason"] == "card_disabled"


def test_locked_card_is_denied_for_the_whole_cooldown_and_tells_remaining_time(student, client):
    set_card(locked_until=utcnow() + timedelta(minutes=4), lock_reason="face_mismatch")
    r = scan(client)
    assert r.status_code == 403 and r.json()["data"]["reason"] == "card_locked"
    assert "phút" in r.json()["data"]["message"]
    with SessionLocal() as db:
        assert db.query(AuthSession).count() == 0


def test_cooldown_expires_exactly(student, client):
    set_card(locked_until=utcnow() - timedelta(seconds=1), lock_reason="face_mismatch")
    assert scan(client).status_code == 202


def test_suspended_user_card_is_denied(student, client, admin):
    admin.post(f"/api/v1/users/{student}/suspend", json={"reason": "vi phạm nội quy"})
    r = scan(client)
    assert r.status_code == 403 and r.json()["data"]["reason"] == "account_suspended"


def test_rapid_scans_of_one_card_are_rate_limited_without_touching_counters(student, client, monkeypatch):
    monkeypatch.setattr(settings, "scan_rate_per_uid_per_min", 3)
    codes = [scan(client).status_code for _ in range(6)]
    assert codes.count(202) == 3 and codes.count(429) == 3
    assert db_card().locked_until is None, "giới hạn tần suất không được khóa thẻ"


def test_device_rate_limit_is_independent_of_the_card(student, client, monkeypatch):
    monkeypatch.setattr(settings, "scan_rate_per_device_per_min", 4)
    codes = [scan(client, f"E{i:03d}").status_code for i in range(8)]
    assert 429 in codes


def test_unknown_card_spam_never_locks_a_real_card(student, client):
    for i in range(20):
        assert scan(client, f"DEAD{i:04d}").status_code == 403
    assert db_card().locked_until is None and scan(client).status_code == 202


# =============================== Nhóm C: vòng đời phiên ===============================
def test_second_scan_supersedes_the_open_session_and_late_result_cannot_grant(student, client):
    old = start(client)
    new = start(client)
    assert sess(old).state == "cancelled" and sess(old).reason == "superseded"
    r = verify(client, old, result="granted", reason="ok")
    assert r.status_code == 200 and r.json()["action"] == "none"
    assert sess(old).state == "cancelled"
    assert sess(new).state == "pending"


def test_new_session_never_reuses_a_previous_result(student, client):
    first = start(client)
    verify(client, first, result="granted", reason="ok")
    second_id = start(client)
    st = client.get(f"/api/v1/access/session/{second_id}", headers=DEVICE).json()
    assert st["status"] == "pending"
    assert client.get(f"/api/v1/access/session/{first}", headers=DEVICE).json()["status"] == "granted"


def test_verify_for_unknown_session_is_rejected(student, client):
    r = verify(client, "doesnotexist1", result="granted", reason="ok")
    assert r.status_code == 404
    assert logs(event=LogEvent.FACE_VERIFY) == []


def test_verify_for_a_different_student_than_the_card_owner_is_rejected(student, second, client):
    sid = start(client)                                         # thẻ của B23DCAT286
    r = verify(client, sid, result="granted", reason="ok", student_id="B23DCAT296")
    assert r.status_code == 409
    assert sess(sid).state == "pending", "kết quả sai chủ phiên không được đóng phiên"
    assert alerts(reason="session_mismatch"), "phải có cảnh báo vì có bên đang gửi kết quả giả"


def test_granted_result_closes_the_session_once(student, client):
    sid = start(client)
    first = verify(client, sid, result="granted", reason="ok")
    assert first.json()["action"] == "open_door"
    again = verify(client, sid, result="granted", reason="ok")
    assert again.json()["action"] == "none"
    flipped = verify(client, sid, result="denied", reason="face_mismatch")
    assert flipped.json()["action"] == "none"
    assert sess(sid).state == "granted"
    assert len(logs(event=LogEvent.FACE_VERIFY, session_id=sid)) == 1


def test_concurrent_results_for_one_session_grant_exactly_once(student, client):
    sid = start(client)
    barrier = threading.Barrier(8)

    def go(_):
        barrier.wait()
        return verify(client, sid, result="granted", reason="ok").json()["action"]

    with ThreadPoolExecutor(8) as pool:
        actions = list(pool.map(go, range(8)))
    assert actions.count("open_door") == 1, actions
    assert len(logs(event=LogEvent.FACE_VERIFY, session_id=sid)) == 1


def test_result_after_expiry_cannot_grant(student, client):
    sid = start(client)
    with SessionLocal() as db:
        s = db.query(AuthSession).filter(AuthSession.session_id == sid).first()
        s.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    r = verify(client, sid, result="granted", reason="ok")
    assert r.json()["action"] == "none"
    assert sess(sid).state == "timeout"


def test_polling_an_expired_pending_session_reports_timeout(student, client):
    sid = start(client)
    with SessionLocal() as db:
        s = db.query(AuthSession).filter(AuthSession.session_id == sid).first()
        s.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert client.get(f"/api/v1/access/session/{sid}", headers=DEVICE).json()["status"] == "timeout"
    assert sess(sid).state == "timeout"


def test_cancel_is_idempotent_and_cannot_undo_a_decision(student, client, fake_ai):
    sid = start(client)
    for _ in range(3):
        assert client.post(f"/api/v1/access/session/{sid}/cancel", headers=DEVICE).status_code == 200
    assert sess(sid).state == "cancelled"
    done = start(client)
    verify(client, done, result="granted", reason="ok")
    client.post(f"/api/v1/access/session/{done}/cancel", headers=DEVICE)
    assert sess(done).state == "granted"


def test_ai_outage_denies_and_marks_session_error_without_counting(student, client, monkeypatch):
    from app.services import ai_client

    def down(*a, **k):
        raise ai_client.AIUnavailable("x")

    monkeypatch.setattr(ai_client, "trigger", down)
    for _ in range(5):
        r = scan(client)
        assert r.status_code == 403 and r.json()["data"]["reason"] == "ai_unavailable"
    assert db_card().locked_until is None
    with SessionLocal() as db:
        assert {s.state for s in db.query(AuthSession).all()} == {"error"}


# =============================== Nhóm E: quyền truy cập ===============================
def test_granted_is_refused_if_card_was_revoked_during_the_scan(student, client):
    sid = start(client)
    set_card(status="revoked")
    r = verify(client, sid, result="granted", reason="ok")
    assert r.json()["action"] == "none"
    assert sess(sid).state == "denied" and sess(sid).reason == "card_revoked"


def test_granted_is_refused_if_card_got_locked_during_the_scan(student, client):
    sid = start(client)
    set_card(locked_until=utcnow() + timedelta(minutes=5))
    assert verify(client, sid, result="granted", reason="ok").json()["action"] == "none"
    assert sess(sid).state == "denied" and sess(sid).reason == "card_locked"


def test_granted_is_refused_if_account_was_suspended_during_the_scan(student, client, admin):
    sid = start(client)
    admin.post(f"/api/v1/users/{student}/suspend", json={"reason": "vi phạm nội quy"})
    assert verify(client, sid, result="granted", reason="ok").json()["action"] == "none"
    assert sess(sid).state == "denied"


def test_frontend_cannot_grant_by_itself(student, client):
    """Người dùng đã đăng nhập (cookie) không có service token thì không gửi được kết quả xác thực."""
    sid = start(client)
    r = client.post("/api/v1/access/verify", json={"student_id": "B23DCAT286", "session_id": sid, "result": "granted"})
    assert r.status_code == 401
    assert sess(sid).state == "pending"


def test_face_mismatch_is_a_denial_not_a_grant(student, client):
    sid = end(client, "face_mismatch")
    assert sess(sid).state == "denied"
    assert client.get(f"/api/v1/access/session/{sid}", headers=DEVICE).json()["status"] == "denied"


def test_backend_error_never_produces_an_open_door_action(student, client):
    sid = start(client)
    r = verify(client, sid, result="error", reason="boom")
    assert r.json()["action"] == "none"
    assert sess(sid).state in ("error", "denied")


# =============================== Nhóm D: khóa tạm ===============================
def test_three_confirmed_mismatches_lock_the_card_for_five_minutes(student, client):
    for _ in range(2):
        end(client, "face_mismatch")
    assert db_card().locked_until is None
    end(client, "face_mismatch")
    c = db_card()
    assert c.locked_until and 290 <= (c.locked_until - utcnow()).total_seconds() <= 305
    assert c.lock_level == 1 and c.lock_reason == "face_mismatch"
    assert scan(client).json()["data"]["reason"] == "card_locked"


def test_mismatches_older_than_the_window_do_not_count(student, client):
    sids = [end(client, "face_mismatch") for _ in range(2)]
    with SessionLocal() as db:
        for sid in sids:
            for row in db.query(AccessLog).filter(AccessLog.session_id == sid).all():
                row.ts = utcnow() - timedelta(seconds=settings.mismatch_window_s + 60)
        db.commit()
    end(client, "face_mismatch")
    assert db_card().locked_until is None


@pytest.mark.parametrize("reason", ["timeout", "face_not_detected", "face_quality_low", "face_inconclusive", "pad_uncertain",
                                    "ai_unavailable", "cancelled"])
def test_technical_outcomes_never_count_toward_lockout(student, client, reason):
    result = "timeout" if reason in ("timeout", "face_not_detected", "face_quality_low", "face_inconclusive") else "denied"
    for _ in range(10):
        end(client, reason, result=result)
    assert db_card().locked_until is None
    assert alerts() == [] or all(a.reason != "card_locked" for a in alerts())


def test_spoof_and_mismatch_counters_are_separate(student, client):
    for _ in range(2):
        end(client, "face_mismatch")
    for _ in range(2):
        end(client, "spoof_pad")
    assert db_card().locked_until is None, "2 + 2 không được cộng chung thành khóa"
    end(client, "spoof_pad")
    c = db_card()
    assert c.locked_until and c.lock_reason == "spoof_pad"


def test_cooldown_escalates_then_is_capped(student, client):
    seen = []
    for _ in range(4):
        for _ in range(3):
            end(client, "face_mismatch")
        c = db_card()
        seen.append(round((c.locked_until - utcnow()).total_seconds() / 60))
        set_card(locked_until=utcnow() - timedelta(seconds=1))        # hết cooldown
    assert seen == [5, 15, 30, 30]
    assert db_card().lock_level == 4


def test_cooldown_level_decays_after_a_quiet_day(student, client):
    for _ in range(3):
        end(client, "face_mismatch")
    set_card(locked_until=utcnow() - timedelta(seconds=1), last_locked_at=utcnow() - timedelta(seconds=DAY + 60))
    for _ in range(3):
        end(client, "face_mismatch")
    c = db_card()
    assert c.lock_level == 1 and 290 <= (c.locked_until - utcnow()).total_seconds() <= 305


def test_a_lock_starts_a_fresh_count_instead_of_relocking_on_one_more_failure(student, client):
    for _ in range(3):
        end(client, "face_mismatch")
    set_card(locked_until=utcnow() - timedelta(seconds=1))
    end(client, "face_mismatch")
    assert db_card().locked_until is None or db_card().locked_until < utcnow()


def test_expired_cooldown_never_revives_revoked_or_disabled_cards(student, client):
    set_card(locked_until=utcnow() - timedelta(seconds=1), status="revoked")
    assert scan(client).json()["data"]["reason"] == "card_revoked"
    set_card(status="disabled")
    assert scan(client).json()["data"]["reason"] == "card_disabled"


def test_lock_state_lives_in_the_database_not_in_memory(student, client):
    for _ in range(3):
        end(client, "face_mismatch")
    limiter.clear()                                                    # mô phỏng khởi động lại ứng dụng
    assert scan(client).json()["data"]["reason"] == "card_locked"


def test_lock_is_per_card_not_per_person_or_device(student, second, client):
    for _ in range(3):
        end(client, "face_mismatch")
    assert scan(client, "B456").status_code == 202, "thẻ của người khác vẫn dùng được"
    assert scan(client, "A123").status_code == 403


def test_locking_creates_alert_and_audit_entry(student, client):
    for _ in range(3):
        end(client, "face_mismatch")
    a = alerts(reason="card_locked")
    assert len(a) == 1 and a[0].level == "medium" and a[0].student_id == "B23DCAT286"
    assert logs(event=LogEvent.ADMIN_ACTION, reason="card_locked")


# =============================== Quản trị mở khóa / trạng thái thẻ ===============================
def test_admin_unlock_clears_cooldown_keeps_history_and_restarts_the_count(admin, student, client):
    for _ in range(3):
        end(admin, "face_mismatch")
    cid = card_id()
    r = admin.post(f"/api/v1/cards/{cid}/unlock", json={"reason": "người dùng xuất trình giấy tờ"})
    assert r.status_code == 200
    assert db_card().locked_until is None
    with SessionLocal() as db:
        assert db.query(AuthSession).filter(AuthSession.state == "denied").count() == 3, "lịch sử không bị xóa"
    end(admin, "face_mismatch")
    assert db_card().locked_until is None, "một lần sai sau mở khóa không được khóa lại ngay"
    audit = [l for l in logs(event=LogEvent.ADMIN_ACTION) if "Mở khóa thẻ" in l.note]
    assert audit and "giấy tờ" in audit[0].note


def test_unlock_requires_admin_and_a_reason(admin, student, client):
    cid = card_id()
    assert admin.post(f"/api/v1/cards/{cid}/unlock", json={"reason": "x"}).status_code == 400
    admin.post("/api/v1/auth/logout")
    admin.post("/api/v1/auth/login", json={"student_id": "B23DCAT286", "password": "123456"})
    assert admin.post(f"/api/v1/cards/{cid}/unlock", json={"reason": "tự mở khóa cho mình"}).status_code == 403


def test_unlock_cannot_revive_a_revoked_card(admin, student):
    cid = card_id()
    admin.post(f"/api/v1/cards/{cid}/revoke")
    r = admin.post(f"/api/v1/cards/{cid}/unlock", json={"reason": "thử mở khóa thẻ đã thu hồi"})
    assert r.status_code == 409 and db_card().status == "revoked"


def test_disable_enable_and_expiry_are_audited(admin, student):
    cid = card_id()
    assert admin.post(f"/api/v1/cards/{cid}/disable", json={"reason": "tạm dừng trong kỳ nghỉ"}).status_code == 200
    assert db_card().status == "disabled"
    assert admin.post(f"/api/v1/cards/{cid}/enable", json={"reason": "đi làm lại sau kỳ nghỉ"}).status_code == 200
    assert db_card().status == "active"
    exp = (utcnow() + timedelta(days=10)).isoformat()
    assert admin.post(f"/api/v1/cards/{cid}/expiry", json={"expires_at": exp, "reason": "thẻ tạm 10 ngày"}).status_code == 200
    assert db_card().expires_at is not None
    notes = " | ".join(l.note for l in logs(event=LogEvent.ADMIN_ACTION))
    assert "Vô hiệu hóa thẻ" in notes and "Bật lại thẻ" in notes and "hạn thẻ" in notes


def test_enable_cannot_revive_a_revoked_card(admin, student):
    cid = card_id()
    admin.post(f"/api/v1/cards/{cid}/revoke")
    assert admin.post(f"/api/v1/cards/{cid}/enable", json={"reason": "thử bật lại thẻ đã thu hồi"}).status_code == 409


def test_users_api_exposes_effective_card_status_for_the_admin_page(admin, student):
    set_card(locked_until=utcnow() + timedelta(minutes=3), lock_reason="face_mismatch")
    card = admin.get("/api/v1/users").json()
    card = [c for u in card for c in u["cards"] if c["uid"] == "A123"][0]
    assert card["effective_status"] == "temporarily_locked" and card["locked_until"] and card["lock_reason"] == "face_mismatch"


# =============================== Nhóm F: audit / cảnh báo ===============================
def test_every_verify_event_is_tied_to_its_session_and_person(student, client):
    sid = end(client, "face_mismatch")
    (row,) = logs(event=LogEvent.FACE_VERIFY, session_id=sid)
    assert row.student_id == "B23DCAT286" and row.reason == "face_mismatch" and row.result == "denied"
    assert [r.session_id for r in logs(event=LogEvent.CARD_SCAN, session_id=sid)] == [sid]


def test_alert_is_created_once_per_session_even_if_the_result_is_resent(student, client):
    sid = start(client)
    for _ in range(4):
        verify(client, sid, result="denied", reason="spoof_pad")
    assert len(alerts(reason="spoof_pad")) == 1


def test_alert_levels_follow_confidence(student, client):
    end(client, "spoof_pad")
    end(client, "face_mismatch")
    end(client, "pad_uncertain")
    end(client, "timeout", result="timeout")
    assert [a.level for a in alerts(reason="spoof_pad")] == ["high"]
    assert [a.level for a in alerts(reason="face_mismatch")] == ["medium"]
    assert alerts(reason="pad_uncertain") == [] and alerts(reason="timeout") == []


def test_logs_do_not_contain_biometric_vectors(student, client):
    sid = start(client)
    verify(client, sid, result="denied", reason="face_mismatch")
    for row in logs():
        text = " ".join(str(v) for v in (row.note, row.reason, row.device))
        assert len(text) < 300 and "[" not in text, "không ghi vector / dữ liệu sinh trắc vào nhật ký"


# =============================== Cửa: lệnh khác với thực thi ===============================
def test_door_events_are_logged_from_relay_transitions_and_not_duplicated(student, client):
    sid = start(client)
    verify(client, sid, result="granted", reason="ok")
    heartbeat(client, relay=0)
    heartbeat(client, relay=1, state="UNLOCKED")
    heartbeat(client, relay=1, state="UNLOCKED")
    heartbeat(client, relay=0)
    heartbeat(client, relay=0)
    rows = logs(event=LogEvent.DOOR_EVENT)
    assert [r.reason for r in rows] == ["door_unlocked", "door_locked"]
    assert rows[0].session_id == sid, "mở cửa vừa sau một phiên granted thì gắn với phiên đó"


def test_granted_alone_is_not_logged_as_door_opened(student, client):
    sid = start(client)
    verify(client, sid, result="granted", reason="ok")
    assert logs(event=LogEvent.DOOR_EVENT) == [], "xác thực thành công chưa chứng minh relay đã đóng"


# =============================== Sai mặt chỉ ghi log, KHÔNG đóng phiên ===============================
def attempt(client, sid, student="B23DCAT286", **kw):
    return verify(client, sid, result="attempt", reason="face_mismatch", student_id=student, **kw)


def test_wrong_face_attempt_is_logged_but_the_session_stays_open(student, client):
    sid = start(client)
    r = attempt(client, sid, confidence_score=0.12)
    assert r.status_code == 200 and r.json()["action"] == "none"
    s = sess(sid)
    assert s.state == "pending" and s.attempts == 1 and s.ended_at is None
    (row,) = logs(event=LogEvent.FACE_VERIFY, session_id=sid)
    assert row.result == "denied" and row.reason == "face_mismatch" and row.confidence == 0.12
    assert client.get(f"/api/v1/access/session/{sid}", headers=DEVICE).json()["status"] == "pending"


def test_each_attempt_is_logged_but_only_one_alert_per_session(student, client):
    sid = start(client)
    for _ in range(4):
        attempt(client, sid)
    assert len(logs(event=LogEvent.FACE_VERIFY, session_id=sid)) == 4
    assert len(alerts(reason="face_mismatch")) == 1 and alerts(reason="face_mismatch")[0].level == "medium"
    assert sess(sid).state == "pending"


def test_the_right_person_can_still_be_granted_after_wrong_attempts(student, client):
    sid = start(client)
    attempt(client, sid)
    attempt(client, sid)
    r = verify(client, sid, result="granted", reason="ok")
    assert r.json()["action"] == "open_door" and sess(sid).state == "granted"
    assert db_card().locked_until is None, "đã xác thực thành công trong phiên thì không khóa thẻ"


def test_attempts_never_lock_the_card_while_the_session_is_still_running(student, client):
    sid = start(client)
    for _ in range(6):
        attempt(client, sid)
    assert db_card().locked_until is None and sess(sid).state == "pending"


def test_attempts_count_toward_the_lock_when_the_session_ends_without_success(student, client):
    sid = start(client)
    for _ in range(3):
        attempt(client, sid)
    with SessionLocal() as db:
        s = db.query(AuthSession).filter(AuthSession.session_id == sid).first()
        s.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert client.get(f"/api/v1/access/session/{sid}", headers=DEVICE).json()["status"] == "timeout"
    c = db_card()
    assert c.locked_until and c.lock_reason == "face_mismatch"
    assert scan(client).json()["data"]["reason"] == "card_locked"


def test_attempts_across_sessions_add_up_but_old_ones_do_not(student, client):
    s1 = start(client)
    attempt(client, s1)
    attempt(client, s1)
    client.post(f"/api/v1/access/session/{s1}/cancel", headers=DEVICE)
    assert db_card().locked_until is None, "mới 2 lần"
    s2 = start(client)
    attempt(client, s2)
    client.post(f"/api/v1/access/session/{s2}/cancel", headers=DEVICE)
    assert db_card().locked_until is not None, "đủ 3 lần trong cửa sổ thì khóa khi phiên kết thúc"
    # lần thử cũ quá cửa sổ không được cộng
    set_card(locked_until=None, failures_reset_at=None, last_locked_at=None, lock_level=0)
    with SessionLocal() as db:
        for row in db.query(AccessLog).filter(AccessLog.event == LogEvent.FACE_VERIFY).all():
            row.ts = utcnow() - timedelta(seconds=settings.mismatch_window_s + 120)
        db.commit()
    s3 = start(client)
    attempt(client, s3)
    client.post(f"/api/v1/access/session/{s3}/cancel", headers=DEVICE)
    assert db_card().locked_until is None


def test_attempt_for_unknown_or_foreign_or_closed_session_is_not_logged(student, second, client):
    assert attempt(client, "nosuchsession1").status_code == 404
    sid = start(client)
    assert attempt(client, sid, student="B23DCAT296").status_code == 409
    done = start(client)
    verify(client, done, result="granted", reason="ok")
    before = len(logs(event=LogEvent.FACE_VERIFY))
    r = attempt(client, done)
    assert r.json()["action"] == "none" and len(logs(event=LogEvent.FACE_VERIFY)) == before


def test_attempt_needs_the_service_token(student, client):
    sid = start(client)
    r = client.post("/api/v1/access/verify", json={"student_id": "B23DCAT286", "session_id": sid, "result": "attempt",
                                                   "reason": "face_mismatch"})
    assert r.status_code == 401
