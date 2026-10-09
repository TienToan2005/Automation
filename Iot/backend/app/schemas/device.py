from typing import Literal

from pydantic import BaseModel


class Heartbeat(BaseModel):
    """ESP32 gửi định kỳ (luồng 6). Phản hồi mang theo lệnh đang chờ."""
    device_id: str
    state: str = ""                  # WAIT_CARD | WAIT_FACE | UNLOCKED | LOCKOUT
    relay: int = 0                   # 1 = đang mở khóa
    rssi: int | None = None
    uptime_s: int = 0
    ip: str = ""
    tries: int = 0


class DeviceCommand(BaseModel):
    command: Literal["open_lock", "reset_lockout"]
    duration_ms: int = 0
    reason: str = ""


class HeartbeatResponse(BaseModel):
    commands: list[DeviceCommand]


class DeviceStatus(BaseModel):
    device_id: str
    online: bool
    last_seen_s: int | None          # số giây từ lần heartbeat cuối
    state: str
    relay: int
    rssi: int | None
    uptime_s: int
    ip: str
    tries: int


class RemoteCommandRequest(BaseModel):
    command: Literal["open_lock", "reset_lockout"]
    reason: str = ""


class EmergencyOpen(BaseModel):
    reason: str
