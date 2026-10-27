"""Nghiệp vụ kiểm soát ra vào: quẹt thẻ, nhận kết quả từ AI, trạng thái phiên, điều khiển từ xa.

Mỗi lần quẹt thẻ hợp lệ tạo một dòng AuthSession (PENDING). Phiên chỉ được đóng MỘT lần bằng UPDATE có điều kiện
(`_close_session`): ai thắng thì ghi nhật ký / cảnh báo / bộ đếm, ai thua (callback muộn, timeout đồng thời, kết thúc lặp
lại) thì không đổi gì. Mở cửa luôn đòi hỏi: phiên còn mở + còn hạn + đúng người + thẻ và tài khoản còn hiệu lực lúc cấp quyền.
"""
import base64
import uuid
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.orm import Session

from ..core.clock import utcnow
from ..core.config import SNAPSHOT_DIR, settings
from ..models import (AccessLog, AccessResult, Alert, AlertLevel, AuthSession, Card, LogEvent, Reason, SessionState, User)
from ..schemas.access import CardScan, SessionStatus, VerifyEvent
from ..schemas.device import DeviceCommand
from . import ai_client
from .audit import audit
from .auth_policy import card_denial_reason, limiter, register_security_failure, remaining_text
from .device_registry import registry
from .notifier import notifier

# Mức cảnh báo theo ĐỘ TIN CẬY: chỉ PAD chắc chắn mới là HIGH. Liveness thất bại / sai chủ thẻ là MEDIUM;
# lỗi thu nhận (không thấy mặt, ảnh kém, PAD chưa chắc, timeout) không phải sự cố an ninh nên không có cảnh báo.
ALERT_LEVEL = {Reason.SPOOF_PAD: AlertLevel.HIGH, Reason.CHALLENGE_FAILED: AlertLevel.MEDIUM,
               Reason.FACE_MISMATCH: AlertLevel.MEDIUM}
ALERT_MESSAGE = {
    Reason.SPOOF_PAD: "Phát hiện giả mạo khuôn mặt (ảnh in / ảnh hoặc video trên màn hình)",
    Reason.CHALLENGE_FAILED: "Khuôn mặt khớp nhưng không thực hiện được thử thách người thật (chưa đủ bằng chứng giả mạo)",
    Reason.FACE_MISMATCH: "Khuôn mặt không khớp chủ thẻ (nghi dùng thẻ của người khác)",
}
# Thông báo cho NGƯỜI DÙNG: ngắn, không lộ ngưỡng / điểm số / luật phát hiện
DENY_MESSAGE = {
    Reason.UNKNOWN_CARD: "Thẻ không hợp lệ.",
    Reason.CARD_REVOKED: "Thẻ đã bị thu hồi. Vui lòng liên hệ quản trị viên.",
    Reason.CARD_DISABLED: "Thẻ đang bị vô hiệu hóa. Vui lòng liên hệ quản trị viên.",
    Reason.CARD_EXPIRED: "Thẻ đã hết hạn. Vui lòng liên hệ quản trị viên.",
    Reason.CARD_LOCKED: "Thẻ đang tạm khóa. Vui lòng liên hệ quản trị viên.",
    Reason.ACCOUNT_SUSPENDED: "Tài khoản đang bị khóa quyền truy cập.",
    Reason.NOT_ENROLLED: "Chưa đăng ký khuôn mặt.",
    Reason.AI_UNAVAILABLE: "Hệ thống nhận diện đang không khả dụng. Vui lòng thử lại sau.",
    Reason.RATE_LIMITED: "Thao tác quá nhanh. Vui lòng đợi giây lát.",
}
FINAL_STATE = {"granted": SessionState.GRANTED, "denied": SessionState.DENIED, "timeout": SessionState.TIMEOUT,
               "cancelled": SessionState.CANCELLED}


def _deny_body(reason: str, message: str | None = None, code: int = 403) -> dict:
    return {"status": "forbidden" if code == 403 else "error", "code": code, "action": "none",
            "data": {"reason": reason, "message": message or DENY_MESSAGE.get(reason, reason)}}


# ------------------------------ Phiên: chuyển trạng thái nguyên tử ------------------------------
def _close_session(db: Session, session_id: str, state: str, reason: str, now: datetime) -> bool:
    """PENDING -> trạng thái cuối. Trả về True chỉ cho NGƯỜI GỌI ĐẦU TIÊN; mọi lần sau là no-op (idempotent)."""
    res = db.execute(update(AuthSession)
                     .where(AuthSession.session_id == session_id, AuthSession.state == SessionState.PENDING)
                     .values(state=state, reason=reason, ended_at=now))
    return res.rowcount == 1


def _settle_attempts(db: Session, s: AuthSession, now: datetime) -> bool:
    """Phiên kết thúc KHÔNG thành công mà đã có lần thử sai mặt: lúc này mới xét khóa tạm thẻ (không khóa giữa phiên)."""
    if s.attempts <= 0 or not s.card_id:
        return False
    card = db.get(Card, s.card_id)
    if card is None:
        return False
    db.flush()
    return register_security_failure(db, card, Reason.FACE_MISMATCH, now)


def _log_session_end(db: Session, s: AuthSession, result: str, reason: str, note: str = "") -> None:
    db.add(AccessLog(event=LogEvent.FACE_VERIFY, student_id=s.student_id, result=result, reason=reason,
                     session_id=s.session_id, device="system", note=note))


def _expire_if_due(db: Session, s: AuthSession, now: datetime) -> bool:
    """Phiên còn PENDING nhưng quá hạn thì đóng thành TIMEOUT. Trả về True nếu phiên không còn mở."""
    if s.state != SessionState.PENDING:
        return True
    if now <= s.expires_at:
        return False
    if _close_session(db, s.session_id, SessionState.TIMEOUT, Reason.TIMEOUT, now):
        _log_session_end(db, s, AccessResult.TIMEOUT, Reason.TIMEOUT, "hết hạn phiên (backend)")
        _settle_attempts(db, s, now)
    db.commit()
    db.refresh(s)
    return True


def _supersede_open_sessions(db: Session, device_id: str, now: datetime) -> None:
    for old in db.query(AuthSession).filter(AuthSession.device_id == device_id,
                                            AuthSession.state == SessionState.PENDING).all():
        if _close_session(db, old.session_id, SessionState.CANCELLED, Reason.SUPERSEDED, now):
            _log_session_end(db, old, AccessResult.CANCELLED, Reason.SUPERSEDED, "có phiên mới thay thế")
            _settle_attempts(db, old, now)


# ------------------------------ Luồng 1: quẹt thẻ ------------------------------
def handle_card_scan(db: Session, scan: CardScan) -> tuple[int, dict]:
    uid = scan.uid.strip().upper()
    now = utcnow()

    def deny(reason: str, user: User | None = None, message: str | None = None, code: int = 403) -> tuple[int, dict]:
        db.add(AccessLog(event=LogEvent.CARD_SCAN, student_id=user.student_id if user else None,
                         result=AccessResult.DENIED, reason=reason, device=scan.device_id, note=f"uid={uid}"))
        db.commit()
        return code, _deny_body(reason, message, code)

    # Giới hạn tần suất TRƯỚC mọi thứ khác và không đụng tới bộ đếm lỗi của thẻ (kẻ xấu không dùng nó để khóa thẻ người khác)
    ok_uid, first_uid = limiter.check(f"uid:{uid}", settings.scan_rate_per_uid_per_min)
    ok_dev, first_dev = limiter.check(f"dev:{scan.device_id}", settings.scan_rate_per_device_per_min)
    if not (ok_uid and ok_dev):
        if first_uid or first_dev:        # chỉ ghi một dòng cho mỗi đợt bị chặn, tránh làm đầy nhật ký
            deny(Reason.RATE_LIMITED, code=429)
        return 429, _deny_body(Reason.RATE_LIMITED, code=429)

    card = db.query(Card).filter(Card.uid == uid).first()
    if not card:
        return deny(Reason.UNKNOWN_CARD)
    user = card.user
    reason = card_denial_reason(card, now)
    if reason == Reason.CARD_REVOKED:
        message = f"Thẻ đã thu hồi ({uid}) vừa được quẹt tại {scan.device_id}"
        new_alert = not db.query(Alert.id).filter(Alert.reason == "revoked_card_used", Alert.student_id == user.student_id,
                                                  Alert.resolved.is_(False)).first()
        if new_alert:                      # một cảnh báo cho tới khi admin xử lý, không nhân bản theo từng lần quẹt
            db.add(Alert(level=AlertLevel.HIGH, reason="revoked_card_used", student_id=user.student_id, message=message))
        result = deny(Reason.CARD_REVOKED, user)
        if new_alert:
            notifier.alert(AlertLevel.HIGH, "revoked_card_used", message, student_id=user.student_id, student_name=user.full_name)
        return result
    if reason == Reason.CARD_LOCKED:
        return deny(reason, user, f"{DENY_MESSAGE[reason]} ({remaining_text(card, now)})")
    if reason:
        return deny(reason, user)
    if not user.is_active:
        return deny(Reason.ACCOUNT_SUSPENDED, user)
    embedding = user.get_embedding()
    if embedding is None:
        return deny(Reason.NOT_ENROLLED, user)

    _supersede_open_sessions(db, scan.device_id, now)
    session_id = uuid.uuid4().hex[:12]
    session = AuthSession(session_id=session_id, card_id=card.id, student_id=user.student_id, device_id=scan.device_id,
                          started_at=now, expires_at=now + timedelta(seconds=settings.face_timeout_s + settings.session_grace_s))
    db.add(session)
    db.commit()                            # phiên phải tồn tại trước khi AI có thể báo kết quả
    try:
        ai_client.trigger(user.student_id, session_id, embedding)
    except ai_client.AIUnavailable:
        _close_session(db, session_id, SessionState.ERROR, Reason.AI_UNAVAILABLE, utcnow())
        result = deny(Reason.AI_UNAVAILABLE, user)     # lỗi kỹ thuật: KHÔNG tính vào bộ đếm sai
        notifier.ai_unavailable(user.student_id, user.full_name)
        return result

    db.add(AccessLog(event=LogEvent.CARD_SCAN, student_id=user.student_id, result=AccessResult.PENDING,
                     reason="scan_face", session_id=session_id, device=scan.device_id, note=f"uid={uid}"))
    db.commit()
    return 202, {"status": "pending", "code": 202, "action": "scan_face",
                 "data": {"session_id": session_id, "student_name": user.full_name,
                          "face_timeout_s": settings.face_timeout_s, "message": "Thẻ hợp lệ. Vui lòng nhìn vào camera."}}


# ------------------------------ Luồng 3: AI báo kết quả ------------------------------
def _save_snapshot(session_id: str, b64: str | None) -> str | None:
    if not b64:
        return None
    name = f"{session_id}.jpg"
    try:
        (SNAPSHOT_DIR / name).write_bytes(base64.b64decode(b64))
    except Exception:
        return None
    return name


def _ok(message: str, action: str = "none", **data) -> tuple[int, dict]:
    return 200, {"status": "success", "code": 200, "action": action, "data": {"message": message, **data}}


def handle_verify(db: Session, ev: VerifyEvent) -> tuple[int, dict]:
    now = utcnow()
    session = db.query(AuthSession).filter(AuthSession.session_id == ev.session_id).first()
    if session is None:       # không có phiên tương ứng với một lần quẹt thẻ: không thể là kết quả hợp lệ
        return 404, _deny_body("unknown_session", "Không có phiên xác thực này.", 404)
    if session.student_id != ev.student_id:
        text = f"Kết quả xác thực cho phiên {ev.session_id} gửi kèm mã người dùng khác chủ thẻ (nghi giả mạo dịch vụ)"
        if not db.query(Alert.id).filter(Alert.reason == "session_mismatch", Alert.message == text).first():
            db.add(Alert(level=AlertLevel.MEDIUM, reason="session_mismatch", student_id=session.student_id, message=text))
            db.commit()
        return 409, _deny_body("session_mismatch", "Kết quả không khớp phiên.", 409)
    if _expire_if_due(db, session, now) or not session.is_open:
        return _ok("Phiên đã kết thúc, kết quả này bị bỏ qua.")

    if ev.result == "attempt":
        return _record_attempt(db, session, ev)

    result, reason = ev.result, ev.reason
    card = db.get(Card, session.card_id) if session.card_id else None
    user = db.query(User).filter(User.student_id == ev.student_id).first()
    action, message, code = "none", "Đã ghi nhận.", 200

    if result == AccessResult.GRANTED:
        # Kiểm tra lại TẠI THỜI ĐIỂM CẤP QUYỀN: thẻ / tài khoản có thể bị thu hồi, khóa, vô hiệu trong lúc quét mặt
        recheck = card_denial_reason(card, now) if card else Reason.UNKNOWN_CARD
        if recheck is None and (not user or not user.is_active):
            recheck = Reason.ACCOUNT_SUSPENDED
        if recheck:
            result, reason, code = AccessResult.DENIED, recheck, 403     # thẻ / tài khoản vừa bị khóa trong lúc quét mặt
            message = DENY_MESSAGE.get(recheck, recheck)

    state = FINAL_STATE.get(result, SessionState.ERROR)
    if state == SessionState.ERROR:
        result = AccessResult.DENIED
    if not _close_session(db, session.session_id, state, reason, now):
        db.rollback()
        return _ok("Phiên đã được xử lý.")                  # thua cuộc đua: người khác đã đóng phiên
    if state == SessionState.GRANTED:
        action, message = "open_door", "Xác thực thành công. Đang mở cửa..."

    snapshot = _save_snapshot(ev.session_id, ev.snapshot_jpeg_base64)
    settled = state != SessionState.GRANTED and state != SessionState.DENIED and _settle_attempts(db, session, now)
    note = f"{ev.duration_ms} ms" + (f", thử thách: {ev.liveness.challenge_type}" if ev.liveness.challenge_type else "")
    db.add(AccessLog(event=LogEvent.FACE_VERIFY, student_id=ev.student_id, result=result, reason=reason,
                     session_id=ev.session_id, confidence=ev.confidence_score, pad_score=ev.liveness.pad_score,
                     device=ev.camera_id, snapshot=snapshot, note=note))
    level = ALERT_LEVEL.get(reason) if state == SessionState.DENIED else None
    if level:
        db.add(Alert(level=level, reason=reason, student_id=ev.student_id, message=ALERT_MESSAGE[reason], snapshot=snapshot))
    locked = settled
    if state == SessionState.DENIED and card is not None:
        db.flush()
        locked = register_security_failure(db, card, reason, now) or _settle_attempts(db, session, now)
    db.commit()
    if level:
        notifier.alert(level, reason, ALERT_MESSAGE[reason], student_id=ev.student_id,
                       student_name=user.full_name if user else None, snapshot=snapshot)
    if locked:
        notifier.alert(AlertLevel.MEDIUM, "card_locked", f"Thẻ {card.uid} bị khóa tạm do nhiều lần xác thực thất bại",
                       student_id=ev.student_id, student_name=user.full_name if user else None)
    return code, {"status": "success" if code == 200 else "forbidden", "code": code, "action": action,
                  "data": {"student_name": user.full_name if user else "Unknown",
                           "role": user.role if user else None, "message": message}}


def _record_attempt(db: Session, session: AuthSession, ev: VerifyEvent) -> tuple[int, dict]:
    """Một lần thử SAI MẶT đã xác nhận: ghi nhật ký (kèm ảnh) + cảnh báo, nhưng KHÔNG đóng phiên: người dùng vẫn quét tiếp
    được tới hết giờ. Lần thử được cộng vào bộ đếm khóa tạm, và chỉ xét khóa khi phiên kết thúc không thành công."""
    reason = ev.reason or Reason.FACE_MISMATCH
    counted = db.execute(update(AuthSession)
                         .where(AuthSession.session_id == session.session_id, AuthSession.state == SessionState.PENDING)
                         .values(attempts=AuthSession.attempts + 1)).rowcount == 1
    if not counted:                           # phiên vừa đóng: kết quả này đến muộn
        db.rollback()
        return _ok("Phiên đã kết thúc, kết quả này bị bỏ qua.")
    db.refresh(session)
    snapshot = _save_snapshot(f"{ev.session_id}-{session.attempts}", ev.snapshot_jpeg_base64)
    db.add(AccessLog(event=LogEvent.FACE_VERIFY, student_id=ev.student_id, result=AccessResult.DENIED, reason=reason,
                     session_id=ev.session_id, confidence=ev.confidence_score, pad_score=ev.liveness.pad_score,
                     device=ev.camera_id, snapshot=snapshot, note=f"lần thử {session.attempts}, phiên vẫn mở"))
    level = ALERT_LEVEL.get(reason)
    text = f"{ALERT_MESSAGE.get(reason, reason)} (phiên {ev.session_id})"
    new_alert = bool(level) and not db.query(Alert.id).filter(Alert.reason == reason, Alert.message == text).first()
    if new_alert:                             # một cảnh báo cho mỗi phiên, mọi lần thử vẫn nằm trong nhật ký
        db.add(Alert(level=level, reason=reason, student_id=ev.student_id, message=text, snapshot=snapshot))
    db.commit()
    if new_alert:
        user = db.query(User).filter(User.student_id == ev.student_id).first()
        notifier.alert(level, reason, text, student_id=ev.student_id,
                       student_name=user.full_name if user else None, snapshot=snapshot)
    return _ok("Đã ghi nhận lần thử, phiên vẫn mở.")


# ------------------------------ ESP32 hỏi kết quả phiên ------------------------------
def get_session_status(db: Session, session_id: str) -> SessionStatus:
    session = db.query(AuthSession).filter(AuthSession.session_id == session_id).first()
    if session is None:
        raise HTTPException(404, "Không có phiên này")
    _expire_if_due(db, session, utcnow())
    if session.state != SessionState.PENDING:
        status = "denied" if session.state == SessionState.ERROR else session.state
        return SessionStatus(status=status, reason=session.reason)

    ai = ai_client.status()
    if ai and ai.get("session_id") == session_id:
        challenge = ai.get("challenge") or {}
        return SessionStatus(status="pending", hint=ai.get("hint", ""), challenge_type=challenge.get("type", ""),
                             challenge_text=challenge.get("text", ""), time_left=ai.get("time_left", 0))
    return SessionStatus(status="pending")


def cancel_session(db: Session, session_id: str) -> None:
    """ESP32 rời bước quét mặt khi AI chưa kết luận (hết giờ, bấm nút trong...). Gọi lặp lại vô hại."""
    session = db.query(AuthSession).filter(AuthSession.session_id == session_id).first()
    if session is None:
        return
    now = utcnow()
    if _close_session(db, session_id, SessionState.CANCELLED, Reason.CANCELLED, now):
        _log_session_end(db, session, AccessResult.CANCELLED, Reason.CANCELLED, "thiết bị hủy phiên")
        _settle_attempts(db, session, now)
        db.commit()
        ai_client.cancel()


def log_exit_button(db: Session, device_id: str) -> None:
    db.add(AccessLog(event=LogEvent.EXIT_BUTTON, result=AccessResult.GRANTED, reason="exit_button", device=device_id))
    db.commit()


# ------------------------------ Sự kiện cửa (khác với "xác thực thành công") ------------------------------
def log_door_event(db: Session, device_id: str, unlocked: bool) -> None:
    """Relay thật sự đóng / nhả theo nhịp tim của thiết bị. Mở cửa ngay sau một phiên granted thì gắn với phiên đó."""
    session_id = None
    if unlocked:
        recent = utcnow() - timedelta(seconds=settings.door_event_session_window_s)
        s = (db.query(AuthSession).filter(AuthSession.device_id == device_id, AuthSession.state == SessionState.GRANTED,
                                          AuthSession.ended_at >= recent).order_by(AuthSession.ended_at.desc()).first())
        session_id = s.session_id if s else None
    db.add(AccessLog(event=LogEvent.DOOR_EVENT, result=AccessResult.GRANTED if unlocked else AccessResult.PENDING,
                     reason=Reason.DOOR_UNLOCKED if unlocked else Reason.DOOR_LOCKED, session_id=session_id,
                     device=device_id, note="relay đóng" if unlocked else "relay nhả"))
    db.commit()


# ------------------------------ Điều khiển từ xa ------------------------------
def send_remote_command(db: Session, admin: User, device_id: str, command: str, reason: str) -> None:
    reason = reason.strip()
    if command == "open_lock" and len(reason) < 5:
        raise HTTPException(400, "Cần nhập lý do mở cửa (tối thiểu 5 ký tự)")
    cmd = DeviceCommand(command=command, reason=reason,
                        duration_ms=settings.unlock_ms if command == "open_lock" else 0)
    if not registry.enqueue(device_id, cmd):
        raise HTTPException(409, f"Thiết bị {device_id} đang offline, không gửi được lệnh")
    if command == "open_lock":
        db.add(AccessLog(event=LogEvent.REMOTE_OPEN, student_id=admin.student_id, result=AccessResult.GRANTED,
                         reason="remote_open", device=device_id, note=reason))
    else:
        audit(db, admin, f"Gửi lệnh {command} tới {device_id}")
    db.commit()
    if command == "open_lock":
        notifier.remote_open(admin.full_name, device_id, reason)
