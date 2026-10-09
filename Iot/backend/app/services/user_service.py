from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.security import hash_password
from ..models import Card, CardStatus, Role, User, UserStatus
from ..schemas.user import UserCreate, UserUpdate
from . import ai_client
from .audit import audit

FACE_ERRORS = {
    "no_face": "Không thấy khuôn mặt",
    "multiple_faces": "Có nhiều hơn một khuôn mặt",
    "low_quality": "Mặt quá nhỏ hoặc mờ, hãy lại gần hơn",
    "spoof_suspected": "Nghi ảnh chụp từ màn hình hoặc ảnh in",
    "invalid_image": "Ảnh không hợp lệ",
}
MIN_PASSWORD = 6


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


def create_user(db: Session, admin: User, data: UserCreate) -> User:
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

    user = User(student_id=student_id, full_name=data.full_name.strip(), role=data.role,
                password_hash=hash_password(data.password))
    if uid:
        user.cards.append(Card(uid=uid))
    db.add(user)
    audit(db, admin, f"Tạo người dùng ({data.role})", student_id)
    db.commit()
    return user


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


def enroll_face(db: Session, actor: User, user: User, images: list[bytes]) -> tuple[int, list[dict]]:
    """Mỗi ảnh qua AI /embed (kiểm tra chất lượng + chống ảnh giả); vector hợp lệ được lấy trung bình."""
    good, errors = [], []
    for i, data in enumerate(images, 1):
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
    user.set_embedding(ai_client.average_embeddings(good))
    audit(db, actor, f"Đăng ký khuôn mặt ({len(good)} ảnh hợp lệ)", user.student_id)
    db.commit()
    return len(good), errors


def delete_face(db: Session, actor: User, user: User) -> User:
    user.set_embedding(None)
    audit(db, actor, "Xóa dữ liệu khuôn mặt", user.student_id)
    db.commit()
    return user
