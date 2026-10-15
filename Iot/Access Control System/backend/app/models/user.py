import json
from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.clock import utcnow
from ..core.database import Base
from .enums import Role, UserStatus


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(10), default=Role.STUDENT)
    status: Mapped[str] = mapped_column(String(12), default=UserStatus.ACTIVE)
    suspend_reason: Mapped[str] = mapped_column(String(200), default="")
    face_embedding: Mapped[str | None] = mapped_column(Text, nullable=True)   # JSON, 512 float đã chuẩn hóa
    face_enrolled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    cards: Mapped[list["Card"]] = relationship(back_populates="user", cascade="all, delete-orphan")  # noqa: F821

    @property
    def has_face(self) -> bool:
        return self.face_embedding is not None

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN

    @property
    def is_active(self) -> bool:
        return self.status == UserStatus.ACTIVE

    def get_embedding(self) -> list[float] | None:
        return json.loads(self.face_embedding) if self.face_embedding else None

    def set_embedding(self, embedding: list[float] | None) -> None:
        self.face_embedding = json.dumps(embedding) if embedding is not None else None
        self.face_enrolled_at = utcnow() if embedding is not None else None
