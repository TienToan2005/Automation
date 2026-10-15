"""Trạng thái thiết bị ESP32 và hàng đợi lệnh điều khiển từ web.

Lưu trong bộ nhớ của tiến trình: đủ cho 1 instance. Nếu chạy nhiều worker/instance
thì chuyển sang Redis hoặc bảng DB (giao diện của lớp này giữ nguyên).
"""
import threading
import time
from collections import deque
from dataclasses import dataclass, field

from ..core.config import settings
from ..schemas.device import DeviceCommand, DeviceStatus, Heartbeat


@dataclass
class _Device:
    hb: Heartbeat
    last_seen: float
    commands: deque = field(default_factory=deque)   # (hạn_dùng, DeviceCommand)
    offline_notified: bool = False                   # đã báo "mất kết nối" cho đợt offline hiện tại


class DeviceRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._devices: dict[str, _Device] = {}

    def heartbeat(self, hb: Heartbeat, events: list[str] | None = None) -> list[DeviceCommand]:
        """Cập nhật trạng thái và trả các lệnh còn hạn đang chờ thiết bị này.

        Nếu truyền `events`, các chuyển trạng thái đáng báo được thêm vào: "online" (lần đầu thấy thiết bị hoặc
        kết nối lại sau khi mất) và "lockout" (vừa chuyển sang khóa tạm)."""
        now = time.time()
        with self._lock:
            dev = self._devices.get(hb.device_id)
            if dev is None:
                dev = self._devices[hb.device_id] = _Device(hb=hb, last_seen=now)
                prev_state, was_offline = "", True
            else:
                prev_state = dev.hb.state
                was_offline = dev.offline_notified or now - dev.last_seen > settings.device_offline_after_s
            if events is not None:
                if was_offline:
                    events.append("online")
                if hb.state == "LOCKOUT" and prev_state != "LOCKOUT":
                    events.append("lockout")
            dev.hb, dev.last_seen, dev.offline_notified = hb, now, False
            pending = [cmd for expires, cmd in dev.commands if expires > now]
            dev.commands.clear()
            return pending

    def pop_newly_offline(self) -> list[str]:
        """Các thiết bị vừa quá hạn heartbeat kể từ lần gọi trước (mỗi đợt offline chỉ trả một lần)."""
        now, gone = time.time(), []
        with self._lock:
            for device_id, dev in self._devices.items():
                if not dev.offline_notified and now - dev.last_seen > settings.device_offline_after_s:
                    dev.offline_notified = True
                    gone.append(device_id)
        return gone

    def is_online(self, device_id: str) -> bool:
        with self._lock:
            dev = self._devices.get(device_id)
            return bool(dev) and time.time() - dev.last_seen <= settings.device_offline_after_s

    def enqueue(self, device_id: str, command: DeviceCommand) -> bool:
        """Đưa lệnh vào hàng đợi. Trả False nếu thiết bị chưa từng/không còn kết nối."""
        if not self.is_online(device_id):
            return False
        with self._lock:
            self._devices[device_id].commands.append((time.time() + settings.command_ttl_s, command))
        return True

    def statuses(self) -> list[DeviceStatus]:
        now = time.time()
        with self._lock:
            out = []
            for device_id, dev in sorted(self._devices.items()):
                age = now - dev.last_seen
                out.append(DeviceStatus(
                    device_id=device_id, online=age <= settings.device_offline_after_s, last_seen_s=int(age),
                    state=dev.hb.state, relay=dev.hb.relay, rssi=dev.hb.rssi, uptime_s=dev.hb.uptime_s,
                    ip=dev.hb.ip, tries=dev.hb.tries))
            return out

    def clear(self) -> None:
        with self._lock:
            self._devices.clear()


registry = DeviceRegistry()
