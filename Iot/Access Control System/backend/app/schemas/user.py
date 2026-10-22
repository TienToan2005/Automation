from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .common import UTCDateTime


class CardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    id: int
    uid: str
    status: str                                                  # trạng thái lưu: active | revoked | disabled
    effective_status: str = Field(validation_alias="current_status")   # có tính hết hạn / khóa tạm
    expires_at: UTCDateTime | None = None
    locked_until: UTCDateTime | None = None
    lock_reason: str = ""
    lock_level: int = 0


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    student_id: str
    full_name: str
    role: str
    status: str
    suspend_reason: str
    has_face: bool
    face_enrolled_at: UTCDateTime | None
    cards: list[CardOut]


class UserCreate(BaseModel):
    student_id: str
    full_name: str
    password: str
    role: str = "student"
    card_uid: str | None = None


class UserUpdate(BaseModel):
    full_name: str | None = None
    role: str | None = None
    password: str | None = None


class ReasonBody(BaseModel):
    reason: str = ""


class CardCreate(BaseModel):
    uid: str


class CardActionBody(BaseModel):
    reason: str = ""


class CardExpiryBody(BaseModel):
    expires_at: datetime | None = None      # None = bỏ hạn
    reason: str = ""


class FaceEnrollResult(BaseModel):
    status: str
    used_images: int
    errors: list[dict]
    user: UserOut
