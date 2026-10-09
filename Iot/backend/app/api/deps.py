"""Dependency dùng chung cho các router: xác thực người dùng, service và thiết bị."""
import jwt
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.database import get_db
from ..core.security import decode_access_token, tokens_match
from ..models import User


def _token_from_request(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:]
    return request.cookies.get("access_token")


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = _token_from_request(request)
    if not token:
        raise HTTPException(401, "Chưa đăng nhập")
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError:
        raise HTTPException(401, "Phiên đăng nhập không hợp lệ hoặc đã hết hạn")
    user = db.query(User).filter(User.student_id == payload["sub"]).first()
    # Đọc lại từ DB mỗi request: khóa tài khoản / đổi vai trò có hiệu lực ngay
    if not user or not user.is_active:
        raise HTTPException(401, "Tài khoản không tồn tại hoặc đã bị khóa")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Chỉ quản trị viên mới có quyền này")
    return user


def require_service(x_service_token: str | None = Header(None)) -> None:
    if not tokens_match(x_service_token, settings.service_token):
        raise HTTPException(401, "Service token không hợp lệ")


def require_device(x_device_token: str | None = Header(None)) -> None:
    if not tokens_match(x_device_token, settings.device_token):
        raise HTTPException(401, "Device token không hợp lệ")
