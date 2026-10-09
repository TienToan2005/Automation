"""Các trang HTML (Jinja2). Dữ liệu được trang tự tải qua /api/v1 bằng JavaScript."""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..api.deps import current_user
from ..core.config import settings
from ..core.database import get_db
from ..models import User

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals.update(MIN_FACE_IMAGES=settings.min_face_images)


class NotAuthenticated(Exception):
    pass


def page_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Giống current_user nhưng chuyển hướng về /login thay vì trả 401."""
    try:
        return current_user(request, db)
    except HTTPException:
        raise NotAuthenticated()


def page_admin(user: User = Depends(page_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Chỉ quản trị viên mới được vào trang này")
    return user


def render(request: Request, name: str, user: User | None, **ctx):
    return templates.TemplateResponse(request, name, {"user": user, **ctx})


@router.get("/login")
def login_page(request: Request):
    return render(request, "login.html", None)


@router.get("/")
def home(user: User = Depends(page_user)):
    return RedirectResponse("/dashboard" if user.is_admin else "/me")


@router.get("/dashboard")
def dashboard(request: Request, user: User = Depends(page_admin)):
    return render(request, "dashboard.html", user)


@router.get("/users")
def users_page(request: Request, user: User = Depends(page_admin)):
    return render(request, "users.html", user)


@router.get("/users/{user_id}")
def user_detail(request: Request, user_id: int, user: User = Depends(page_admin)):
    return render(request, "user_detail.html", user, target_id=user_id)


@router.get("/logs")
def logs_page(request: Request, user: User = Depends(page_user)):
    return render(request, "logs.html", user)


@router.get("/alerts")
def alerts_page(request: Request, user: User = Depends(page_admin)):
    return render(request, "alerts.html", user)


@router.get("/me")
def me_page(request: Request, user: User = Depends(page_user)):
    return render(request, "me.html", user, target_id=user.id)
