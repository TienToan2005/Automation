"""Nghiệp vụ kiểm soát ra vào: quẹt thẻ, nhận kết quả từ AI, trạng thái phiên, điều khiển từ xa."""
import base64
import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..core.config import SNAPSHOT_DIR, settings
from ..models import AccessLog, AccessResult, Alert, AlertLevel, Card, CardStatus, LogEvent, User
from ..schemas.access import CardScan, SessionStatus, VerifyEvent
from ..schemas.device import DeviceCommand
from . import ai_client
from .audit import audit
from .device_registry import registry

ALERT_LEVEL = {"spoof_pad": AlertLevel.HIGH, "challenge_failed": AlertLevel.HIGH, "face_mismatch": AlertLevel.MEDIUM}
ALERT_MESSAGE = {
    "spoof_pad": "Phát hiện giả mạo khuôn mặt (ảnh in / ảnh hoặc video trên màn hình)",
    "challenge_failed": "Khuôn mặt đúng nhưng không thực hiện được thách thức người thật (nghi ảnh tĩnh)",
    "face_mismatch": "Khuôn mặt không khớp chủ thẻ (nghi dùng thẻ của người khác)",
}
DENY_MESSAGE = {
    "unknown_card": "Thẻ không tồn tại trong hệ thống.",
    "card_revoked": "Thẻ đã bị thu hồi.",
    "account_suspended": "Tài khoản đã bị khóa quyền truy cập.",
    "not_enrolled": "Chưa đăng ký khuôn mặt.",
    "ai_unavailable": "Hệ thống nhận diện khuôn mặt đang không khả dụng.",
}


def _deny_body(reason: str) -> dict:
    return {"status": "forbidden", "code": 403, "action": "none",
            "data": {"reason": reason, "message": DENY_MESSAGE.get(reason, reason)}}


# ------------------------------ Luồng 1: quẹt thẻ ------------------------------
def handle_card_scan(db: Session, scan: CardScan) -> tuple[int, dict]:
    uid = scan.uid.strip().upper()

    def deny(reason: str, user: User | None = None) -> tuple[int, dict]:
        db.add(AccessLog(event=LogEvent.CARD_SCAN, student_id=user.student_id if user else None,
                         result=AccessResult.DENIED, reason=reason, device=scan.device_id, note=f"uid={uid}"))
        db.commit()
        return 403, _deny_body(reason)

    card = db.query(Card).filter(Card.uid == uid).first()
    if not card:
        return deny("unknown_card")
    user = card.user
    if card.status != CardStatus.ACTIVE:
        # Thẻ đã báo mất mà bị quẹt lại -> cảnh báo
        db.add(Alert(level=AlertLevel.HIGH, reason="revoked_card_used", student_id=user.student_id,
                     message=f"Thẻ đã thu hồi ({uid}) vừa được quẹt tại {scan.device_id}"))
        return deny("card_revoked", user)
    if not user.is_active:
        return deny("account_suspended", user)
    embedding = user.get_embedding()
    if embedding is None:
        return deny("not_enrolled", user)

    session_id = uuid.uuid4().hex[:12]
    try:
        ai_client.trigger(user.student_id, session_id, embedding)
    except ai_client.AIUnavailable:
        return deny("ai_unavailable", user)

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


def handle_verify(db: Session, ev: VerifyEvent) -> tuple[int, dict]:
    already = db.query(AccessLog.id).filter(AccessLog.session_id == ev.session_id,
                                            AccessLog.event == LogEvent.FACE_VERIFY).first()
    if already:   # AI có thể gửi lại cùng một phiên
        return 200, {"status": "success", "code": 200, "action": "none", "data": {"message": "Phiên đã được xử lý."}}

    user = db.query(User).filter(User.student_id == ev.student_id).first()
    result, reason = ev.result, ev.reason
    action, code, status, message = "none", 200, "success", "Đã ghi nhận."

    if result == AccessResult.GRANTED:
        # Kiểm tra lại tại thời điểm mở cửa (tài khoản có thể bị khóa trong lúc quét mặt)
        if not user or not user.is_active:
            result, reason, code, status = AccessResult.DENIED, "account_suspended", 403, "forbidden"
            message = DENY_MESSAGE["account_suspended"]
        else:
            action, message = "open_door", "Xác thực thành công. Đang mở cửa..."

    snapshot = _save_snapshot(ev.session_id, ev.snapshot_jpeg_base64)
    note = f"{ev.duration_ms} ms" + (f", thử thách: {ev.liveness.challenge_type}" if ev.liveness.challenge_type else "")
    db.add(AccessLog(event=LogEvent.FACE_VERIFY, student_id=ev.student_id, result=result, reason=reason,
                     session_id=ev.session_id, confidence=ev.confidence_score, pad_score=ev.liveness.pad_score,
                     device=ev.camera_id, snapshot=snapshot, note=note))
    if reason in ALERT_LEVEL:
        db.add(Alert(level=ALERT_LEVEL[reason], reason=reason, student_id=ev.student_id,
                     message=ALERT_MESSAGE[reason], snapshot=snapshot))
    db.commit()
    return code, {"status": status, "code": code, "action": action,
                  "data": {"student_name": user.full_name if user else "Unknown",
                           "role": user.role if user else None, "message": message}}


# ------------------------------ ESP32 hỏi kết quả phiên ------------------------------
def get_session_status(db: Session, session_id: str) -> SessionStatus:
    if not db.query(AccessLog.id).filter(AccessLog.session_id == session_id).first():
        raise HTTPException(404, "Không có phiên này")
    verdict = db.query(AccessLog).filter(AccessLog.session_id == session_id,
                                         AccessLog.event == LogEvent.FACE_VERIFY).first()
    if verdict:
        return SessionStatus(status=verdict.result, reason=verdict.reason)

    ai = ai_client.status()
    if ai and ai.get("session_id") == session_id:
        challenge = ai.get("challenge") or {}
        return SessionStatus(status="pending", hint=ai.get("hint", ""), challenge_type=challenge.get("type", ""),
                             challenge_text=challenge.get("text", ""), time_left=ai.get("time_left", 0))
    return SessionStatus(status="pending")


def cancel_session(db: Session, session_id: str) -> None:
    """ESP32 rời bước quét mặt khi AI chưa kết luận (hết giờ, bấm nút trong...)."""
    if not db.query(AccessLog.id).filter(AccessLog.session_id == session_id,
                                         AccessLog.event == LogEvent.FACE_VERIFY).first():
        ai_client.cancel()


def log_exit_button(db: Session, device_id: str) -> None:
    db.add(AccessLog(event=LogEvent.EXIT_BUTTON, result=AccessResult.GRANTED, reason="exit_button", device=device_id))
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
