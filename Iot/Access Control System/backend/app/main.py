import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .api.v1.router import api_router
from .core.config import settings
from .core.database import Base, SessionLocal, add_missing_columns, engine
from .core.security import hash_password
from .models import Role, User
from .services.device_registry import registry
from .services.notifier import notifier
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


async def device_monitor() -> None:
    """Mỗi vài giây kiểm tra thiết bị nào vừa quá hạn heartbeat để báo "mất kết nối"."""
    while True:
        await asyncio.sleep(2)
        try:
            notifier.check_devices(registry)
        except Exception:
            logging.getLogger("notifier").exception("Lỗi khi kiểm tra thiết bị")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    add_missing_columns(engine)
    seed_admin()
    if settings.is_default_secret:
        print("[CẢNH BÁO] Đang dùng LAB_SECRET_KEY mặc định, chỉ phù hợp khi phát triển.")
    monitor = asyncio.create_task(device_monitor())
    try:
        yield
    finally:
        monitor.cancel()
        await asyncio.to_thread(notifier.flush, 3)      # cho tin đang xếp hàng kịp gửi trước khi tắt


app = FastAPI(title="Lab Access Control Backend", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "web" / "static")), name="static")
app.include_router(api_router)
app.include_router(web.router)


@app.exception_handler(web.NotAuthenticated)
async def not_authenticated(request, exc):
    return RedirectResponse("/login", status_code=303)
