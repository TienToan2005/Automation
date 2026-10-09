from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from ..core.clock import utcnow
from ..core.database import Base


class AccessLog(Base):
    """Nhật ký ra vào và thao tác quản trị. Không xóa theo người dùng (giữ làm bằng chứng)."""

    __tablename__ = "access_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    event: Mapped[str] = mapped_column(String(20), index=True)
    student_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    result: Mapped[str] = mapped_column(String(12))
    reason: Mapped[str] = mapped_column(String(32), default="")
    session_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    pad_score: Mapped[float | None] = mapped_column(nullable=True)
    device: Mapped[str] = mapped_column(String(40), default="")
    snapshot: Mapped[str | None] = mapped_column(String(80), nullable=True)
    note: Mapped[str] = mapped_column(String(300), default="")
