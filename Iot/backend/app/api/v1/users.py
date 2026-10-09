from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ...core.database import get_db
from ...models import User
from ...schemas.user import CardCreate, FaceEnrollResult, ReasonBody, UserCreate, UserOut, UserUpdate
from ...services import user_service as svc
from ..deps import current_user, require_admin

router = APIRouter(tags=["users"])


def _self_or_admin(actor: User, user_id: int, message: str) -> None:
    if not actor.is_admin and actor.id != user_id:
        raise HTTPException(403, message)


# ------------------------------ Quản lý người dùng (admin) ------------------------------
@router.get("/users", response_model=list[UserOut])
def list_users(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return db.query(User).order_by(User.student_id).all()


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return svc.create_user(db, admin, body)


@router.get("/users/{user_id}", response_model=UserOut)
def get_user(user_id: int, actor: User = Depends(current_user), db: Session = Depends(get_db)):
    _self_or_admin(actor, user_id, "Không có quyền xem người dùng này")
    return svc.get_or_404(db, user_id)


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return svc.update_user(db, admin, svc.get_or_404(db, user_id), body)


@router.post("/users/{user_id}/suspend", response_model=UserOut)
def suspend_user(user_id: int, body: ReasonBody, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return svc.suspend_user(db, admin, svc.get_or_404(db, user_id), body.reason)


@router.post("/users/{user_id}/activate", response_model=UserOut)
def activate_user(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return svc.activate_user(db, admin, svc.get_or_404(db, user_id))


@router.delete("/users/{user_id}")
def delete_user(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    svc.delete_user(db, admin, svc.get_or_404(db, user_id))
    return {"status": "ok"}


# ------------------------------ Thẻ RFID ------------------------------
@router.post("/users/{user_id}/cards", response_model=UserOut, status_code=201)
def add_card(user_id: int, body: CardCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return svc.add_card(db, admin, svc.get_or_404(db, user_id), body.uid)


@router.post("/cards/{card_id}/revoke", response_model=UserOut)
def revoke_card(card_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return svc.revoke_card(db, admin, card_id)


# ------------------------------ Khuôn mặt ------------------------------
@router.put("/users/{user_id}/face", response_model=FaceEnrollResult)
async def enroll_face(user_id: int, images: list[UploadFile] = File(...),
                      actor: User = Depends(current_user), db: Session = Depends(get_db)):
    """Admin đăng ký cho bất kỳ ai; sinh viên chỉ đăng ký cho chính mình."""
    _self_or_admin(actor, user_id, "Chỉ được đăng ký khuôn mặt cho chính mình")
    user = svc.get_or_404(db, user_id)
    if not 1 <= len(images) <= 8:
        raise HTTPException(400, "Gửi từ 1 đến 8 ảnh")
    raw = [await f.read() for f in images]
    used, errors = svc.enroll_face(db, actor, user, raw)
    return {"status": "ok", "used_images": used, "errors": errors, "user": user}


@router.delete("/users/{user_id}/face", response_model=UserOut)
def delete_face(user_id: int, actor: User = Depends(current_user), db: Session = Depends(get_db)):
    _self_or_admin(actor, user_id, "Chỉ được xóa khuôn mặt của chính mình")
    return svc.delete_face(db, actor, svc.get_or_404(db, user_id))
