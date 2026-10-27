from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..core.clock import utcnow
from ..core.database import Base
from .enums import SessionState


class AuthSession(Base):
    """Một lần quẹt thẻ hợp lệ = một phiên xác thực. Đây là nguồn sự thật duy nhất về trạng thái phiên:
    chuyển PENDING -> trạng thái cuối chỉ một lần, bằng UPDATE có điều kiện (nguyên tử), nên callback đến muộn,
    timeout đồng thời hay gọi kết thúc lặp lại đều không đổi được phiên đã đóng."""

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    card_id: Mapped[int | None] = mapped_column(ForeignKey("cards.id", ondelete="SET NULL"), nullable=True, index=True)
    student_id: Mapped[str] = mapped_column(String(32), index=True)
    device_id: Mapped[str] = mapped_column(String(40), default="")
    state: Mapped[str] = mapped_column(String(12), default=SessionState.PENDING, index=True)
    reason: Mapped[str] = mapped_column(String(32), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)   # số lần THỬ SAI MẶT đã ghi trong phiên (phiên vẫn mở)

    @property
    def is_open(self) -> bool:
        return self.state == SessionState.PENDING
