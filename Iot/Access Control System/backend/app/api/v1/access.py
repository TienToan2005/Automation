"""Endpoint cho thiết bị/AI (ESP32, AI Service) và tra cứu nhật ký, cảnh báo."""
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from ...core.clock import local_day_start_utc, utcnow
from ...core.config import SNAPSHOT_DIR
from ...core.database import get_db
from ...models import AccessLog, Alert, User
from ...schemas.access import (AccessLogPage, AlertOut, CardScan, ExitEvent, ResolveBody, SessionStatus, VerifyEvent)
from ...services import access_service as svc
from ..deps import current_user, require_admin, require_device, require_service

router = APIRouter(tags=["access"])


# ------------------------------ Thiết bị / AI ------------------------------
@router.post("/access/card-scan", dependencies=[Depends(require_device)])
def card_scan(body: CardScan, db: Session = Depends(get_db)):
    code, content = svc.handle_card_scan(db, body)
    return JSONResponse(status_code=code, content=content)


@router.post("/access/verify", dependencies=[Depends(require_service)])
def verify(body: VerifyEvent, db: Session = Depends(get_db)):
    code, content = svc.handle_verify(db, body)
    return JSONResponse(status_code=code, content=content)


@router.get("/access/session/{session_id}", response_model=SessionStatus, dependencies=[Depends(require_device)])
def session_status(session_id: str, db: Session = Depends(get_db)):
    return svc.get_session_status(db, session_id)


@router.post("/access/session/{session_id}/cancel", dependencies=[Depends(require_device)])
def cancel_session(session_id: str, db: Session = Depends(get_db)):
    svc.cancel_session(db, session_id)
    return {"status": "ok"}


@router.post("/access/exit", dependencies=[Depends(require_device)])
def exit_button(body: ExitEvent, db: Session = Depends(get_db)):
    svc.log_exit_button(db, body.device_id)
    return {"status": "ok"}


# ------------------------------ Nhật ký / cảnh báo ------------------------------
def _parse_dt(value: str | None, end_of_day: bool = False) -> datetime | None:
    """`YYYY-MM-DD` là NGÀY địa phương (cùng múi giờ với giao diện): quy ra UTC để so với cột `ts`.
    Chuỗi có giờ (ISO) được coi là UTC như trước."""
    if not value:
        return None
    try:
        if len(value) <= 10:
            day = date.fromisoformat(value)
            return local_day_start_utc(day + timedelta(days=1)) - timedelta(seconds=1) if end_of_day else local_day_start_utc(day)
        return datetime.fromisoformat(value.replace("Z", ""))
    except ValueError:
        raise HTTPException(400, f"Thời gian không hợp lệ: {value}")


@router.get("/access-logs", response_model=AccessLogPage)
def access_logs(user: User = Depends(current_user), db: Session = Depends(get_db),
                date_from: str | None = None, date_to: str | None = None, result: str | None = None,
                reason: str | None = None, student_id: str | None = None, event: str | None = None,
                limit: int = 100, offset: int = 0):
    """Admin xem tất cả; sinh viên chỉ xem log của chính mình."""
    q = db.query(AccessLog)
    if not user.is_admin:
        q = q.filter(AccessLog.student_id == user.student_id)
    elif student_id:
        q = q.filter(AccessLog.student_id == student_id.strip())
    if d := _parse_dt(date_from):
        q = q.filter(AccessLog.ts >= d)
    if d := _parse_dt(date_to, end_of_day=True):
        q = q.filter(AccessLog.ts <= d)
    for column, value in ((AccessLog.result, result), (AccessLog.reason, reason), (AccessLog.event, event)):
        if value:
            q = q.filter(column == value)
    total = q.count()
    rows = q.order_by(AccessLog.ts.desc()).offset(max(offset, 0)).limit(min(max(limit, 1), 500)).all()
    return {"total": total, "items": rows}


@router.get("/snapshots/{name}")
def snapshot(name: str, _: User = Depends(require_admin)):
    path = SNAPSHOT_DIR / Path(name).name      # chặn path traversal
    if not path.is_file():
        raise HTTPException(404, "Không có ảnh")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/alerts", response_model=list[AlertOut])
def list_alerts(response: Response, _: User = Depends(require_admin), db: Session = Depends(get_db),
                include_resolved: bool = False, level: str | None = None, reason: str | None = None,
                limit: int = 200, offset: int = 0):
    """Phân trang bằng `limit`/`offset`; tổng số bản ghi khớp bộ lọc nằm ở header `X-Total-Count`."""
    q = db.query(Alert)
    if not include_resolved:
        q = q.filter(Alert.resolved.is_(False))
    if level:
        q = q.filter(Alert.level == level)
    if reason:
        q = q.filter(Alert.reason == reason)
    response.headers["X-Total-Count"] = str(q.count())
    return q.order_by(Alert.ts.desc()).offset(max(offset, 0)).limit(min(max(limit, 1), 500)).all()


@router.post("/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: int, body: ResolveBody | None = None, admin: User = Depends(require_admin),
                  db: Session = Depends(get_db)):
    alert = db.get(Alert, alert_id)
    if not alert:
        raise HTTPException(404, "Không tìm thấy cảnh báo")
    if not alert.resolved:      # gọi lại không ghi đè người / thời điểm xử lý đầu tiên
        alert.resolved, alert.resolved_by, alert.resolved_at = True, admin.student_id, utcnow()
        alert.resolution_note = (body.note if body else "").strip()
        db.commit()
    return {"status": "ok"}
