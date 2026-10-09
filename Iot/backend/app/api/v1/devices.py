"""Thiết bị ESP32: heartbeat (thiết bị gọi) và điều khiển từ xa (admin gọi từ web)."""


from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...core.clock import utcnow
from ...core.config import settings
from ...core.database import get_db
from ...models import AccessLog, AccessResult, Alert, LogEvent, User
from ...schemas.device import DeviceStatus, EmergencyOpen, Heartbeat, HeartbeatResponse, RemoteCommandRequest
from ...services import access_service, ai_client
from ...services.device_registry import registry
from ..deps import require_admin, require_device

router = APIRouter(tags=["devices"])


@router.post("/device/heartbeat", response_model=HeartbeatResponse, dependencies=[Depends(require_device)])
def heartbeat(body: Heartbeat):
    return {"commands": registry.heartbeat(body)}


@router.get("/devices", response_model=list[DeviceStatus])
def list_devices(_: User = Depends(require_admin)):
    return registry.statuses()


@router.post("/devices/{device_id}/commands")
def send_command(device_id: str, body: RemoteCommandRequest, admin: User = Depends(require_admin),
                 db: Session = Depends(get_db)):
    access_service.send_remote_command(db, admin, device_id, body.command, body.reason)
    return {"status": "queued"}


@router.post("/door/emergency-open")
def emergency_open(body: EmergencyOpen, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Mở cửa khẩn cấp từ web (bắt buộc ghi lý do) tới thiết bị mặc định."""
    access_service.send_remote_command(db, admin, settings.default_device_id, "open_lock", body.reason)
    return {"status": "queued"}


@router.get("/system/status")
def system_status(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    counted = (LogEvent.FACE_VERIFY, LogEvent.EXIT_BUTTON, LogEvent.REMOTE_OPEN)
    counts = dict(db.query(AccessLog.result, func.count())
                  .filter(AccessLog.ts >= today, AccessLog.event.in_(counted)).group_by(AccessLog.result).all())
    return {
        "today_granted": counts.get(AccessResult.GRANTED, 0),
        "today_denied": counts.get(AccessResult.DENIED, 0) + counts.get(AccessResult.TIMEOUT, 0),
        "open_alerts": db.query(Alert).filter(Alert.resolved.is_(False)).count(),
        "users": db.query(User).count(),
        "enrolled": db.query(User).filter(User.face_embedding.is_not(None)).count(),
        "ai": ai_client.health(),
        "default_device_id": settings.default_device_id,
    }
