import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .api.v1.router import api_router
from .core.config import settings
from .core.database import Base, SessionLocal, engine
from .core.security import hash_password
from .models import Role, User
from .web import routes as web

# Console Windows mặc định cp1252 sẽ lỗi khi in tiếng Việt
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def seed_admin() -> None:
    with SessionLocal() as db:
        if not db.query(User).filter(User.role == Role.ADMIN).first():
            db.add(User(student_id=settings.admin_id, full_name="Quản trị viên", role=Role.ADMIN,
                        password_hash=hash_password(settings.admin_password)))
            db.commit()
            print(f"[SETUP] Đã tạo tài khoản admin '{settings.admin_id}' - hãy đổi mật khẩu ngay.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    seed_admin()
    if settings.is_default_secret:
        print("[CẢNH BÁO] Đang dùng LAB_SECRET_KEY mặc định, chỉ phù hợp khi phát triển.")
    yield


app = FastAPI(title="Lab Access Control Backend", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "web" / "static")), name="static")
app.include_router(api_router)
app.include_router(web.router)


@app.exception_handler(web.NotAuthenticated)
async def not_authenticated(request, exc):
    return RedirectResponse("/login", status_code=303)
