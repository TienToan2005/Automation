from sqlalchemy.orm import Session

from ..models import AccessLog, AccessResult, LogEvent, User


def audit(db: Session, actor: User, note: str, student_id: str | None = None) -> None:
    """Ghi một thao tác quản trị vào nhật ký (chưa commit)."""
    db.add(AccessLog(event=LogEvent.ADMIN_ACTION, student_id=student_id, result=AccessResult.GRANTED,
                     reason="admin", device=actor.student_id, note=note))
