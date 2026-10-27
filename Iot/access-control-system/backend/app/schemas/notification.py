from pydantic import BaseModel, ConfigDict, Field, computed_field

from .common import UTCDateTime


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    ts: UTCDateTime
    kind: str
    level: str
    title: str
    message: str
    student_id: str | None
    link: str
    snapshot: str | None = Field(default=None, exclude=True)

    @computed_field
    @property
    def snapshot_url(self) -> str | None:
        return f"/api/v1/snapshots/{self.snapshot}" if self.snapshot else None


class NotificationPage(BaseModel):
    """`boot` đổi mỗi lần backend khởi động: id bắt đầu lại từ 1 nên trình duyệt phải đặt lại con trỏ."""
    boot: str
    last_id: int
    items: list[NotificationOut]


class ChannelResult(BaseModel):
    ok: bool
    error: str | None = None


class NotifyTestResult(BaseModel):
    web: bool = True
    external: dict[str, ChannelResult]     # chỉ gồm các kênh đã cấu hình (telegram, email); rỗng nếu chưa cấu hình kênh nào
