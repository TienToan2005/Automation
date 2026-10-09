from pydantic import BaseModel, ConfigDict, Field, computed_field

from .common import UTCDateTime


# ---------- Luồng 1: ESP32 quẹt thẻ ----------
class CardScan(BaseModel):
    device_id: str
    uid: str
    timestamp: str | None = None


# ---------- Luồng 3: AI Service báo kết quả ----------
class Liveness(BaseModel):
    passed: bool = False
    pad_score: float | None = None
    challenge_type: str | None = None


class VerifyEvent(BaseModel):
    camera_id: str = ""
    student_id: str
    confidence_score: float = 0.0
    timestamp: str | None = None
    session_id: str
    result: str
    reason: str = ""
    liveness: Liveness = Field(default_factory=Liveness)
    duration_ms: int = 0
    snapshot_jpeg_base64: str | None = None


class ExitEvent(BaseModel):
    device_id: str = ""


class SessionStatus(BaseModel):
    """ESP32 poll để biết phiên quét mặt đã xong chưa (kèm gợi ý hiển thị OLED)."""
    status: str                       # pending | granted | denied | timeout | cancelled
    reason: str = ""
    hint: str = ""
    challenge_type: str = ""          # turn_left | turn_right | blink
    challenge_text: str = ""
    time_left: int = 0


# ---------- Nhật ký / cảnh báo ----------
class AccessLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    ts: UTCDateTime
    event: str
    student_id: str | None
    result: str
    reason: str
    session_id: str | None
    confidence: float | None
    pad_score: float | None
    device: str
    note: str
    snapshot: str | None = Field(default=None, exclude=True)

    @computed_field
    @property
    def snapshot_url(self) -> str | None:
        return f"/api/v1/snapshots/{self.snapshot}" if self.snapshot else None


class AccessLogPage(BaseModel):
    total: int
    items: list[AccessLogOut]


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    ts: UTCDateTime
    level: str
    reason: str
    student_id: str | None
    message: str
    resolved: bool
    snapshot: str | None = Field(default=None, exclude=True)

    @computed_field
    @property
    def snapshot_url(self) -> str | None:
        return f"/api/v1/snapshots/{self.snapshot}" if self.snapshot else None
