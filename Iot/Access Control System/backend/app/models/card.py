from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.clock import utcnow
from ..core.database import Base
from .enums import CardStatus, EffectiveCardStatus
from .user import User


class Card(Base):
    __tablename__ = "cards"

    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(10), default=CardStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)       # None = không hết hạn
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)     # khóa tạm tự hết hiệu lực
    lock_reason: Mapped[str] = mapped_column(String(64), default="")
    lock_level: Mapped[int] = mapped_column(Integer, default=0)                         # bậc cooldown: 5, 15, 30 phút...
    last_locked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    failures_reset_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # mốc bắt đầu đếm lại (admin mở khóa)
    user: Mapped[User] = relationship(back_populates="cards")

    @property
    def current_status(self) -> str:
        return self.effective_status()

    def effective_status(self, now: datetime | None = None) -> str:
        """Trạng thái hiệu lực tại thời điểm `now`. Thứ tự ưu tiên: thu hồi > vô hiệu > hết hạn > khóa tạm > hoạt động.
        Hết cooldown KHÔNG bao giờ biến thẻ thu hồi / vô hiệu / hết hạn thành hoạt động trở lại."""
        now = now or utcnow()
        if self.status == CardStatus.REVOKED:
            return EffectiveCardStatus.REVOKED
        if self.status == CardStatus.DISABLED:
            return EffectiveCardStatus.DISABLED
        if self.expires_at is not None and self.expires_at <= now:
            return EffectiveCardStatus.EXPIRED
        if self.locked_until is not None and self.locked_until > now:
            return EffectiveCardStatus.TEMPORARILY_LOCKED
        return EffectiveCardStatus.ACTIVE
