"""Thông báo tức thời.

Một sự kiện (cảnh báo an ninh, mở cửa từ xa, thiết bị mất kết nối...) đi tới:
  1. Chuông trên web: trình duyệt của admin hỏi GET /api/v1/notifications?after=<id> mỗi ~2 giây.
  2. Telegram và 3. Email: chỉ bật khi đủ cấu hình (LAB_TELEGRAM_*, LAB_SMTP_*). Chỉ đẩy từ mức LAB_NOTIFY_MIN_LEVEL,
     mỗi loại sự kiện tối đa một lần trong LAB_NOTIFY_COOLDOWN_S giây, và gửi ở luồng nền để không làm chậm request.

Hàng đợi của chuông nằm trong bộ nhớ (mất khi khởi động lại backend). Bằng chứng lâu dài vẫn là Alert / AccessLog trong DB.
"""
import logging
import queue
import smtplib
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import requests

from ..core.clock import to_local, utcnow
from ..core.config import SNAPSHOT_DIR, settings

log = logging.getLogger("notifier")

LEVELS = {"info": 0, "medium": 1, "high": 2}
LEVEL_ICON = {"info": "ℹ️", "medium": "⚠️", "high": "🚨"}
LEVEL_VI = {"info": "Thông tin", "medium": "Trung bình", "high": "Cao"}
ALERT_TITLE = {
    "spoof_pad": "Phát hiện giả mạo khuôn mặt",
    "challenge_failed": "Không qua thử thách người thật",
    "face_mismatch": "Sai khuôn mặt (nghi dùng thẻ của người khác)",
    "revoked_card_used": "Thẻ đã thu hồi vừa được quẹt",
}
HISTORY = 200          # số thông báo gần nhất giữ trong bộ nhớ


@dataclass(frozen=True)
class Notification:
    id: int
    ts: datetime
    kind: str          # alert | remote_open | device_online | device_offline | device_lockout | ai_unavailable | test
    level: str         # info | medium | high
    title: str
    message: str
    student_id: str | None = None
    snapshot: str | None = None
    link: str = ""     # đường dẫn trang web liên quan, vd /alerts


# ------------------------------ Cấu hình kênh ------------------------------
def telegram_configured() -> bool:
    return bool(settings.telegram_bot_token and settings.telegram_chat_id)


def _recipients() -> list[str]:
    return [a.strip() for a in settings.smtp_to.split(",") if a.strip()]


def email_configured() -> bool:
    return bool(settings.smtp_host and _recipients() and (settings.smtp_from or settings.smtp_user))


def channels() -> dict:
    return {"web": True, "telegram": telegram_configured(), "email": email_configured(),
            "min_level": settings.notify_min_level, "cooldown_s": settings.notify_cooldown_s}


# ------------------------------ Soạn tin ------------------------------
def format_text(n: Notification) -> str:
    offset = settings.utc_offset_hours
    lines = [f"{LEVEL_ICON.get(n.level, '')} {n.title}".strip(), n.message,
             f"Lúc: {to_local(n.ts).strftime('%H:%M:%S %d/%m/%Y')} (UTC{offset:+d})"]
    if settings.public_base_url and n.link:
        lines.append(f"Chi tiết: {settings.public_base_url.rstrip('/')}{n.link}")
    return "\n".join(lines)


def _snapshot_path(n: Notification) -> Path | None:
    if not n.snapshot:
        return None
    path = SNAPSHOT_DIR / Path(n.snapshot).name        # chặn path traversal
    return path if path.is_file() else None


def _send_telegram(n: Notification) -> None:
    base = f"https://api.telegram.org/bot{settings.telegram_bot_token}"
    text, photo = format_text(n), _snapshot_path(n)
    if photo:   # ảnh nghi phạm đi kèm cảnh báo (caption tối đa 1024 ký tự)
        r = requests.post(f"{base}/sendPhoto", timeout=10, data={"chat_id": settings.telegram_chat_id, "caption": text[:1024]},
                          files={"photo": (photo.name, photo.read_bytes(), "image/jpeg")})
    else:
        r = requests.post(f"{base}/sendMessage", timeout=10,
                          data={"chat_id": settings.telegram_chat_id, "text": text, "disable_web_page_preview": "true"})
    if not r.ok:
        try:
            detail = r.json().get("description", "")
        except ValueError:
            detail = r.text[:120]
        raise RuntimeError(f"Telegram trả HTTP {r.status_code}: {detail}")


def _send_email(n: Notification) -> None:
    msg = EmailMessage()
    msg["Subject"] = f"[Lab Access] {LEVEL_VI.get(n.level, n.level)}: {n.title}"
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = ", ".join(_recipients())
    msg.set_content(format_text(n))
    photo = _snapshot_path(n)
    if photo:
        msg.add_attachment(photo.read_bytes(), maintype="image", subtype="jpeg", filename=photo.name)
    use_ssl = settings.smtp_port == 465
    with (smtplib.SMTP_SSL if use_ssl else smtplib.SMTP)(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        if not use_ssl and settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)


def _redact(text: str) -> str:
    """Lỗi của requests chứa URL, mà URL Telegram có token bot: không để lọt vào log hay phản hồi API."""
    for secret in (settings.telegram_bot_token, settings.smtp_password):
        if secret:
            text = text.replace(secret, "***")
    return text


def _try(fn, n: Notification) -> dict:
    try:
        fn(n)
        return {"ok": True, "error": None}
    except Exception as e:      # mạng hỏng, sai token, SMTP từ chối...: không bao giờ làm hỏng nghiệp vụ chính
        err = _redact(f"{type(e).__name__}: {e}")
        log.warning("Gửi thông báo qua %s thất bại: %s", fn.__name__.removeprefix("_send_"), err)
        return {"ok": False, "error": err}


class Notifier:
    def __init__(self) -> None:
        self.boot = uuid.uuid4().hex[:8]         # đổi mỗi lần backend khởi động: trình duyệt biết để đặt lại con trỏ id
        self._lock = threading.Lock()
        self._items: deque[Notification] = deque(maxlen=HISTORY)
        self._next_id = 1
        self._last_pushed: dict[str, float] = {}
        self._outbox: queue.Queue[Notification] = queue.Queue()
        self._worker: threading.Thread | None = None

    # ---- hàng đợi cho chuông web ----
    @property
    def last_id(self) -> int:
        with self._lock:
            return self._next_id - 1

    def since(self, after: int, limit: int = 50) -> list[Notification]:
        with self._lock:
            return [n for n in self._items if n.id > after][:limit]

    def recent(self, limit: int = 20) -> list[Notification]:
        with self._lock:
            return list(self._items)[-limit:]

    def reset(self) -> None:
        """Dùng trong test: xóa lịch sử và trạng thái chống lặp."""
        self.flush()
        with self._lock:
            self._items.clear()
            self._next_id = 1
            self._last_pushed.clear()

    # ---- phát hành ----
    def publish(self, kind: str, level: str, title: str, message: str, *, student_id: str | None = None,
                snapshot: str | None = None, link: str = "", dedupe_key: str | None = None) -> Notification:
        with self._lock:
            n = Notification(self._next_id, utcnow(), kind, level, title, message, student_id, snapshot, link)
            self._next_id += 1
            self._items.append(n)
            push = self._should_push(n, dedupe_key)
        if push:
            self._ensure_worker()
            self._outbox.put(n)
        return n

    def _should_push(self, n: Notification, dedupe_key: str | None) -> bool:
        """Gọi khi đang giữ _lock."""
        if LEVELS.get(n.level, 0) < LEVELS.get(settings.notify_min_level, LEVELS["medium"]):
            return False
        if not (telegram_configured() or email_configured()):
            return False
        key, now = dedupe_key or f"{n.kind}:{n.student_id or ''}", time.monotonic()
        last = self._last_pushed.get(key)
        if last is not None and now - last < settings.notify_cooldown_s:
            return False
        self._last_pushed[key] = now
        return True

    def deliver(self, n: Notification) -> dict:
        """Gửi ngay (đồng bộ) tới mọi kênh ngoài đã cấu hình; trả kết quả từng kênh. Không xét mức / chống lặp."""
        out = {}
        if telegram_configured():
            out["telegram"] = _try(_send_telegram, n)
        if email_configured():
            out["email"] = _try(_send_email, n)
        return out

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._run, name="notifier", daemon=True)
                self._worker.start()

    def _run(self) -> None:
        while True:
            n = self._outbox.get()
            try:
                self.deliver(n)
            finally:
                self._outbox.task_done()

    def flush(self, timeout: float = 5.0) -> None:
        """Chờ gửi xong các tin đang xếp hàng (test, hoặc khi tắt máy)."""
        end = time.monotonic() + timeout
        while self._outbox.unfinished_tasks and time.monotonic() < end:
            time.sleep(0.01)

    # ---- sự kiện nghiệp vụ: câu chữ nằm hết ở đây ----
    def alert(self, level: str, reason: str, message: str, *, student_id: str | None, student_name: str | None = None,
              snapshot: str | None = None) -> Notification:
        who = f"{student_name} ({student_id})" if student_name and student_id else (student_id or "không xác định")
        return self.publish("alert", str(level), ALERT_TITLE.get(reason, "Cảnh báo an ninh"), f"{message}. Liên quan: {who}.",
                            student_id=student_id, snapshot=snapshot, link="/alerts", dedupe_key=f"alert:{reason}:{student_id or ''}")

    def remote_open(self, admin_name: str, device_id: str, reason: str) -> Notification:
        return self.publish("remote_open", "info", "Mở cửa từ xa", f"{admin_name} mở cửa {device_id}. Lý do: {reason}",
                            link="/logs", dedupe_key=f"remote_open:{device_id}")

    def ai_unavailable(self, student_id: str, student_name: str) -> Notification:
        return self.publish("ai_unavailable", "medium", "AI Service không phản hồi",
                            f"Lượt quẹt thẻ của {student_name} ({student_id}) bị từ chối vì không gọi được AI. "
                            "Kiểm tra AI Service (cổng 5050) và camera.", student_id=student_id, link="/dashboard",
                            dedupe_key="ai_unavailable")

    def device_event(self, kind: str, device_id: str, hb=None) -> Notification:
        """kind: online | offline | lockout"""
        if kind == "online":
            extra = f" (IP {hb.ip}, WiFi {hb.rssi} dBm)" if hb is not None and hb.ip else ""
            return self.publish("device_online", "info", "Thiết bị đã kết nối", f"{device_id} đang hoạt động{extra}.",
                                link="/dashboard", dedupe_key=f"device:online:{device_id}")
        if kind == "offline":
            return self.publish("device_offline", "medium", "Thiết bị mất kết nối",
                                f"{device_id} không gửi heartbeat quá {settings.device_offline_after_s} giây. "
                                "Kiểm tra nguồn và WiFi của thiết bị.", link="/dashboard", dedupe_key=f"device:offline:{device_id}")
        return self.publish("device_lockout", "medium", "Cửa bị khóa tạm",
                            f"{device_id} bị khóa tạm sau nhiều lần xác thực sai. Có thể gỡ khóa trên trang Tổng quan.",
                            link="/dashboard", dedupe_key=f"device:lockout:{device_id}")

    def check_devices(self, registry) -> list[str]:
        """Gọi định kỳ: báo thiết bị vừa quá hạn heartbeat. Trả danh sách thiết bị vừa được báo."""
        gone = registry.pop_newly_offline()
        for device_id in gone:
            self.device_event("offline", device_id)
        return gone


notifier = Notifier()
