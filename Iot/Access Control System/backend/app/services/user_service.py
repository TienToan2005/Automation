from datetime import timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..core.clock import utcnow
from ..core.config import settings
from ..core.security import hash_password
from ..models import Card, CardStatus, Role, User, UserStatus
from ..schemas.user import UserCreate, UserUpdate
from . import ai_client
from .audit import audit

FACE_ERRORS = {
    "no_face": "Không thấy khuôn mặt",
    "multiple_faces": "Có nhiều hơn một khuôn mặt",
    "low_quality": "Mặt quá nhỏ hoặc bị che, hãy lại gần hơn và nhìn thẳng",
    "blurry": "Ảnh bị mờ, hãy giữ yên đầu và chụp lại",
    "spoof_suspected": "Nghi ảnh chụp từ màn hình hoặc ảnh in",
    "invalid_image": "Ảnh không hợp lệ",
}
MIN_PASSWORD = 6
MAX_IMAGE_BYTES = 8 * 1024 * 1024

# Quét khuôn mặt có hướng dẫn: AI trả mã bước / mã gợi ý, chữ tiếng Việt hiển thị trên web nằm ở đây (cạnh FACE_ERRORS).
GUIDE_STEPS = {                       # id bước -> (nhãn ngắn trên vòng tiến độ, câu hướng dẫn lớn)
    "front": ("Nhìn thẳng", "Nhìn thẳng vào camera"),
    "left": ("Quay trái", "Quay đầu sang trái"),
    "right": ("Quay phải", "Quay đầu sang phải"),
    "blink": ("Chớp mắt", "Hãy chớp mắt"),
}
GUIDE_DONE = "Hoàn tất quét khuôn mặt"
GUIDE_HINTS = {
    **FACE_ERRORS,
    "blurry": "Ảnh bị mờ, hãy giữ yên đầu",
    "too_far": "Hãy lại gần camera hơn",
    "off_center": "Đưa mặt vào giữa khung tròn",
    "not_front": "Nhìn thẳng vào camera",
    "wrong_way": "Quay sang bên kia",
}


def _check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise HTTPException(400, f"Mật khẩu tối thiểu {MIN_PASSWORD} ký tự")


def _check_role(role: str) -> None:
    if role not in (Role.ADMIN, Role.STUDENT):
        raise HTTPException(400, "Vai trò phải là admin hoặc student")


def get_or_404(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "Không tìm thấy người dùng")
    return user


def _validate_new_user(db: Session, data: UserCreate) -> tuple[str, str]:
    """Kiểm tra dữ liệu người dùng mới; trả về (mã sinh viên, UID thẻ chuẩn hóa)."""
    student_id = data.student_id.strip()
    if not student_id or not data.full_name.strip():
        raise HTTPException(400, "Thiếu mã sinh viên hoặc họ tên")
    _check_role(data.role)
    _check_password(data.password)
    if db.query(User).filter(User.student_id == student_id).first():
        raise HTTPException(409, "Mã sinh viên đã tồn tại")
    uid = (data.card_uid or "").strip().upper()
    if uid and db.query(Card).filter(Card.uid == uid).first():
        raise HTTPException(409, "UID thẻ đã được gán cho người khác")
    return student_id, uid


def create_user(db: Session, admin: User, data: UserCreate, embedding: list[float] | None = None,
                face_images: int = 0) -> User:
    student_id, uid = _validate_new_user(db, data)

    user = User(student_id=student_id, full_name=data.full_name.strip(), role=data.role,
                password_hash=hash_password(data.password))
    if uid:
        user.cards.append(Card(uid=uid))
    if embedding is not None:
        user.set_embedding(embedding)
    db.add(user)
    audit(db, admin, f"Tạo người dùng ({data.role})", student_id)
    if embedding is not None:
        audit(db, admin, f"Đăng ký khuôn mặt ({face_images} ảnh hợp lệ)", student_id)
    db.commit()
    return user


def scan_face(image: bytes) -> dict:
    """Kiểm tra một ảnh chụp từ webcam (chưa lưu gì). Trả về kết quả AI kèm thông báo tiếng Việt; không trả vector."""
    if not image or len(image) > MAX_IMAGE_BYTES:
        return {"ok": False, "reason": "invalid_image", "message": FACE_ERRORS["invalid_image"]}
    try:
        res = ai_client.embed(image)
    except ai_client.AIUnavailable:
        raise HTTPException(503, "AI Service không phản hồi, thử lại sau")
    res.pop("embedding", None)
    res["message"] = "Khuôn mặt hợp lệ" if res.get("ok") else FACE_ERRORS.get(res.get("reason", ""), "Ảnh không đạt")
    return res


def _guide_view(res: dict) -> dict:
    """Thêm chữ tiếng Việt vào trạng thái quét của AI: nhãn từng bước, câu hướng dẫn của bước hiện tại, lời gợi ý."""
    for s in res.get("steps", []):
        s["label"] = GUIDE_STEPS.get(s["id"], (s["id"], s["id"]))[0]
    step = res.get("step")
    res["instruction"] = GUIDE_STEPS.get(step, (step, step))[1] if step else GUIDE_DONE
    hint = res.get("hint") or ""
    res["message"] = GUIDE_HINTS.get(hint, hint)
    return res


def _guide_call(fn, *args) -> dict:
    """Gọi AI và đổi lỗi thành HTTPException có mã (code) để trang web phản ứng đúng."""
    try:
        return _guide_view(fn(*args))
    except ai_client.AIUnavailable:
        raise HTTPException(503, detail={"message": "AI Service không phản hồi, hãy bật AI Service rồi thử lại", "code": "ai_unavailable"})
    except ai_client.AISessionExpired:
        raise HTTPException(410, detail={"message": "Phiên quét đã hết hạn, hãy bắt đầu lại", "code": "session_expired"})
    except ai_client.AIRejected:
        raise HTTPException(400, "Không thực hiện được lệnh này ở bước hiện tại")


def guide_start() -> dict:
    return _guide_call(ai_client.enroll_start)


def guide_frame(session_id: str, image: bytes | None, command: str | None) -> dict:
    if image is not None and len(image) > MAX_IMAGE_BYTES:
        raise HTTPException(413, FACE_ERRORS["invalid_image"])
    return _guide_call(ai_client.enroll_frame, session_id, image, command)


def create_user_with_face(db: Session, admin: User, data: UserCreate, images: list[bytes]) -> tuple[User, int, list[dict]]:
    """Thêm người dùng + thẻ RFID + khuôn mặt trong MỘT bước. Ảnh không đạt -> không tạo người dùng nào."""
    _validate_new_user(db, data)                                  # báo lỗi trùng mã/thẻ trước khi tốn công chạy AI
    embedding, used, errors = _embed_all(images)
    user = create_user(db, admin, data, embedding, used)
    return user, used, errors


def update_user(db: Session, admin: User, user: User, data: UserUpdate) -> User:
    if data.full_name:
        user.full_name = data.full_name.strip()
    if data.role:
        _check_role(data.role)
        if user.id == admin.id and data.role != Role.ADMIN:
            raise HTTPException(400, "Không thể tự hạ quyền của chính mình")
        user.role = data.role
    if data.password:
        _check_password(data.password)
        user.password_hash = hash_password(data.password)
    audit(db, admin, "Sửa thông tin người dùng", user.student_id)
    db.commit()
    return user


def suspend_user(db: Session, admin: User, user: User, reason: str) -> User:
    if user.id == admin.id:
        raise HTTPException(400, "Không thể tự khóa tài khoản của chính mình")
    user.status, user.suspend_reason = UserStatus.SUSPENDED, reason.strip()
    audit(db, admin, f"Khóa tài khoản: {reason}", user.student_id)
    db.commit()
    return user


def activate_user(db: Session, admin: User, user: User) -> User:
    user.status, user.suspend_reason = UserStatus.ACTIVE, ""
    audit(db, admin, "Mở khóa tài khoản", user.student_id)
    db.commit()
    return user


def delete_user(db: Session, admin: User, user: User) -> None:
    """Offboarding: xóa tài khoản, thẻ và vector khuôn mặt. Nhật ký ra vào được giữ lại."""
    if user.id == admin.id:
        raise HTTPException(400, "Không thể tự xóa tài khoản của chính mình")
    audit(db, admin, "Xóa người dùng (offboarding)", user.student_id)
    db.delete(user)
    db.commit()


def add_card(db: Session, admin: User, user: User, uid: str) -> User:
    uid = uid.strip().upper()
    if not uid:
        raise HTTPException(400, "Thiếu UID")
    if db.query(Card).filter(Card.uid == uid).first():
        raise HTTPException(409, "UID thẻ đã tồn tại")
    user.cards.append(Card(uid=uid))
    audit(db, admin, f"Cấp thẻ {uid}", user.student_id)
    db.commit()
    return user


def revoke_card(db: Session, admin: User, card_id: int) -> User:
    card = db.get(Card, card_id)
    if not card:
        raise HTTPException(404, "Không tìm thấy thẻ")
    card.status = CardStatus.REVOKED
    audit(db, admin, f"Thu hồi thẻ {card.uid}", card.user.student_id)
    db.commit()
    return card.user


def _need_reason(reason: str) -> str:
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise HTTPException(400, "Cần nhập lý do (tối thiểu 5 ký tự)")
    return reason


def _get_card(db: Session, card_id: int) -> Card:
    card = db.get(Card, card_id)
    if not card:
        raise HTTPException(404, "Không tìm thấy thẻ")
    return card


def unlock_card(db: Session, admin: User, card_id: int, reason: str) -> User:
    """Mở khóa tạm. Không xóa lịch sử phiên / nhật ký: chỉ đặt mốc đếm lại. Không làm sống lại thẻ thu hồi / vô hiệu."""
    reason = _need_reason(reason)
    card = _get_card(db, card_id)
    if card.status in (CardStatus.REVOKED, CardStatus.DISABLED):
        raise HTTPException(409, "Thẻ đã bị thu hồi / vô hiệu hóa, mở khóa tạm không có tác dụng")
    card.locked_until, card.lock_reason = None, ""
    card.failures_reset_at = utcnow()
    audit(db, admin, f"Mở khóa thẻ {card.uid}: {reason}", card.user.student_id)
    db.commit()
    return card.user


def disable_card(db: Session, admin: User, card_id: int, reason: str) -> User:
    reason = _need_reason(reason)
    card = _get_card(db, card_id)
    if card.status == CardStatus.REVOKED:
        raise HTTPException(409, "Thẻ đã thu hồi vĩnh viễn")
    card.status = CardStatus.DISABLED
    audit(db, admin, f"Vô hiệu hóa thẻ {card.uid}: {reason}", card.user.student_id)
    db.commit()
    return card.user


def enable_card(db: Session, admin: User, card_id: int, reason: str) -> User:
    reason = _need_reason(reason)
    card = _get_card(db, card_id)
    if card.status == CardStatus.REVOKED:
        raise HTTPException(409, "Thẻ đã thu hồi vĩnh viễn, hãy cấp thẻ mới")
    card.status = CardStatus.ACTIVE
    audit(db, admin, f"Bật lại thẻ {card.uid}: {reason}", card.user.student_id)
    db.commit()
    return card.user


def set_card_expiry(db: Session, admin: User, card_id: int, expires_at, reason: str) -> User:
    reason = _need_reason(reason)
    card = _get_card(db, card_id)
    if expires_at is not None and expires_at.tzinfo is not None:
        expires_at = expires_at.astimezone(timezone.utc).replace(tzinfo=None)     # DB lưu UTC không kèm tzinfo
    card.expires_at = expires_at
    audit(db, admin, f"Đặt hạn thẻ {card.uid} đến {expires_at.isoformat() if expires_at else 'không giới hạn'}: {reason}",
          card.user.student_id)
    db.commit()
    return card.user


def _embed_all(images: list[bytes]) -> tuple[list[float], int, list[dict]]:
    """Mỗi ảnh qua AI /embed (kiểm tra chất lượng + chống ảnh giả); vector hợp lệ được lấy trung bình."""
    good, errors = [], []
    for i, data in enumerate(images, 1):
        if len(data) > MAX_IMAGE_BYTES:
            errors.append({"image": i, "reason": "invalid_image", "message": FACE_ERRORS["invalid_image"]})
            continue
        try:
            res = ai_client.embed(data)
        except ai_client.AIUnavailable:
            raise HTTPException(503, "AI Service không phản hồi, thử lại sau")
        if res.get("ok"):
            good.append(res["embedding"])
        else:
            code = res.get("reason", "unknown")
            errors.append({"image": i, "reason": code, "message": FACE_ERRORS.get(code, code)})

    if len(good) < settings.min_face_images:
        raise HTTPException(422, detail={
            "message": f"Cần tối thiểu {settings.min_face_images} ảnh hợp lệ, hiện có {len(good)}", "errors": errors})
    return ai_client.average_embeddings(good), len(good), errors


def enroll_face(db: Session, actor: User, user: User, images: list[bytes]) -> tuple[int, list[dict]]:
    embedding, used, errors = _embed_all(images)
    user.set_embedding(embedding)
    audit(db, actor, f"Đăng ký khuôn mặt ({used} ảnh hợp lệ)", user.student_id)
    db.commit()
    return used, errors


def delete_face(db: Session, actor: User, user: User) -> User:
    user.set_embedding(None)
    audit(db, actor, "Xóa dữ liệu khuôn mặt", user.student_id)
    db.commit()
    return user
