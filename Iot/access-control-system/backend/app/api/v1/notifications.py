"""Thông báo tức thời cho admin (xem services/notifier.py)."""
from fastapi import APIRouter, Depends

from ...models import User
from ...schemas.notification import NotificationPage, NotifyTestResult
from ...services.notifier import notifier
from ..deps import require_admin

router = APIRouter(tags=["notifications"])


@router.get("/notifications", response_model=NotificationPage)
def list_notifications(after: int | None = None, limit: int = 20, _: User = Depends(require_admin)):
    """Có `after`: các thông báo mới hơn id đó (cũ -> mới). Không có `after`: `limit` thông báo gần nhất (lần mở trang)."""
    limit = min(max(limit, 1), 100)
    items = notifier.since(after, limit) if after is not None else notifier.recent(limit)
    return {"boot": notifier.boot, "last_id": notifier.last_id, "items": items}


@router.post("/notifications/test", response_model=NotifyTestResult)
def send_test_notification(admin: User = Depends(require_admin)):
    """Gửi thử tới chuông web và MỌI kênh ngoài đã cấu hình (bỏ qua mức tối thiểu và chống lặp); trả kết quả từng kênh."""
    n = notifier.publish("test", "info", "Thông báo thử", f"{admin.full_name} vừa gửi thông báo thử.", link="/dashboard")
    return {"web": True, "external": notifier.deliver(n)}
