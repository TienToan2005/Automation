from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from ...core.config import settings
from ...core.database import get_db
from ...core.security import create_access_token, hash_password, verify_password
from ...models import User
from ...schemas.auth import LoginRequest, PasswordChange
from ...schemas.user import UserOut
from ..deps import current_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
def login(body: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.student_id == body.student_id.strip()).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Sai mã sinh viên hoặc mật khẩu")
    if not user.is_active:
        raise HTTPException(403, "Tài khoản đã bị khóa")
    token = create_access_token(user.student_id, user.role)
    response.set_cookie("access_token", token, httponly=True, samesite="lax", max_age=settings.token_ttl_hours * 3600)
    return {"access_token": token, "user": UserOut.model_validate(user)}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie("access_token")
    return {"status": "ok"}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user


@router.post("/me/password")
def change_password(body: PasswordChange, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(400, "Mật khẩu cũ không đúng")
    if len(body.new_password) < 6:
        raise HTTPException(400, "Mật khẩu mới tối thiểu 6 ký tự")
    user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"status": "ok"}
