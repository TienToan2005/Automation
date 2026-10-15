from pydantic import BaseModel, ConfigDict

from .common import UTCDateTime


class CardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    uid: str
    status: str


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


class FaceEnrollResult(BaseModel):
    status: str
    used_images: int
    errors: list[dict]
    user: UserOut
