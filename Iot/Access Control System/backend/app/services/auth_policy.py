"""Chính sách xác thực: giới hạn tần suất, lý do từ chối của thẻ, bộ đếm lỗi an ninh và khóa tạm (cooldown).

Nguyên tắc (chi tiết và lý do ở docs/NGHIEP_VU_XAC_THUC.md):
  * Chỉ BẰNG CHỨNG AN NINH đã xác nhận mới tăng bộ đếm: face_mismatch (đủ chất lượng + độ khớp thấp rõ rệt),
    spoof_pad (PAD chắc chắn) và challenge_failed. Lỗi kỹ thuật / thu nhận / timeout không bao giờ tăng bộ đếm.
  * Mỗi loại một bộ đếm riêng, tính từ bảng auth_sessions nên bền vững qua khởi động lại.
  * Khóa theo THẺ (không theo người, không theo cửa), tạm thời, tăng dần có trần; hết hạn khóa không làm sống lại
    thẻ đã thu hồi / vô hiệu / hết hạn.
  * Mở khóa không xóa lịch sử: chỉ đặt mốc bắt đầu đếm lại (failures_reset_at).
"""
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..core.clock import utcnow
from ..core.config import settings
from ..models import (AccessLog, AccessResult, Alert, AlertLevel, AuthSession, Card, EffectiveCardStatus, LogEvent, Reason,
                      SessionState)

CARD_DENIAL = {
    EffectiveCardStatus.REVOKED: Reason.CARD_REVOKED,
    EffectiveCardStatus.DISABLED: Reason.CARD_DISABLED,
    EffectiveCardStatus.EXPIRED: Reason.CARD_EXPIRED,
    EffectiveCardStatus.TEMPORARILY_LOCKED: Reason.CARD_LOCKED,
}


def card_denial_reason(card: Card, now: datetime) -> str | None:
    """Lý do thẻ không được dùng tại thời điểm `now`, hoặc None nếu thẻ đang hoạt động."""
    return CARD_DENIAL.get(card.effective_status(now))


# ------------------------------ Giới hạn tần suất (trong bộ nhớ) ------------------------------
class RateLimiter:
    """Cửa sổ trượt theo khóa. Chỉ để chống tự động hóa / spam: KHÔNG phải trạng thái bảo mật (khóa thẻ nằm trong DB)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._hits: dict[str, deque] = defaultdict(deque)
        self._blocked: set[str] = set()

    def check(self, key: str, limit: int, window_s: float = 60.0) -> tuple[bool, bool]:
        """Ghi một lần thử. Trả về (được phép, vừa bắt đầu bị chặn): cờ thứ hai chỉ True ở lần chặn đầu của mỗi đợt,
        để chỉ ghi nhật ký một lần thay vì mỗi lần spam."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > window_s:
                hits.popleft()
            if len(hits) >= limit:
                first = key not in self._blocked
                self._blocked.add(key)
                return False, first
            self._blocked.discard(key)
            hits.append(now)
            return True, False

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()
            self._blocked.clear()


limiter = RateLimiter()


# ------------------------------ Bộ đếm + khóa tạm ------------------------------
def _counter_rules() -> dict[str, tuple[int, int]]:
    """lý do -> (ngưỡng, cửa sổ giây). Đọc settings mỗi lần gọi để cấu hình đổi được lúc chạy / trong test."""
    return {
        Reason.FACE_MISMATCH: (settings.mismatch_lock_threshold, settings.mismatch_window_s),
        Reason.SPOOF_PAD: (settings.spoof_lock_threshold, settings.spoof_window_s),
        Reason.CHALLENGE_FAILED: (settings.liveness_lock_threshold, settings.liveness_window_s),
    }


def lock_duration_s(level: int) -> int:
    """Bậc 1 = lock_base_s, mỗi bậc nhân 3 (5, 15, 45 phút...), tối đa lock_cap_s (30 phút)."""
    return min(settings.lock_base_s * 3 ** (max(level, 1) - 1), settings.lock_cap_s)


def count_failures(db: Session, card: Card, reason: str, now: datetime) -> int:
    """Số phiên bị từ chối vì `reason` của thẻ này trong cửa sổ, tính từ mốc reset (nếu có)."""
    rule = _counter_rules().get(reason)
    if rule is None:
        return 0
    since = now - timedelta(seconds=rule[1])
    if card.failures_reset_at and card.failures_reset_at > since:
        since = card.failures_reset_at
    if reason == Reason.FACE_MISMATCH:
        # Sai mặt không đóng phiên nên đếm theo từng LẦN THỬ ghi trong nhật ký (kể cả phiên cũ bị từ chối vì sai mặt)
        return (db.query(AccessLog)
                .join(AuthSession, AuthSession.session_id == AccessLog.session_id)
                .filter(AuthSession.card_id == card.id, AccessLog.event == LogEvent.FACE_VERIFY,
                        AccessLog.reason == reason, AccessLog.result == AccessResult.DENIED, AccessLog.ts > since)
                .count())
    return (db.query(AuthSession)
            .filter(AuthSession.card_id == card.id, AuthSession.state == SessionState.DENIED,
                    AuthSession.reason == reason, AuthSession.ended_at > since)
            .count())


def register_security_failure(db: Session, card: Card, reason: str, now: datetime) -> bool:
    """Gọi SAU KHI phiên đã chuyển sang DENIED vì `reason`. Khóa tạm thẻ nếu bộ đếm tương ứng chạm ngưỡng.
    Trả về True nếu vừa khóa. Không commit (người gọi commit)."""
    rule = _counter_rules().get(reason)
    if rule is None or reason in (Reason.AI_UNAVAILABLE, Reason.TIMEOUT):
        return False
    if card.effective_status(now) == EffectiveCardStatus.TEMPORARILY_LOCKED:
        return False
    if count_failures(db, card, reason, now) < rule[0]:
        return False

    quiet = card.last_locked_at is None or (now - card.last_locked_at).total_seconds() > settings.lock_level_decay_s
    card.lock_level = 1 if quiet else card.lock_level + 1
    seconds = lock_duration_s(card.lock_level)
    card.locked_until = now + timedelta(seconds=seconds)
    card.lock_reason = reason
    card.last_locked_at = now
    # Đợt sau đếm lại từ đầu, lịch sử vẫn giữ. Lấy giờ MỚI (sau mọi dòng nhật ký vừa ghi) để lần sai gây ra khóa không bị đếm lại
    card.failures_reset_at = utcnow() + timedelta(microseconds=1)
    student_id = card.user.student_id
    note = f"Khóa tạm thẻ {card.uid} {seconds // 60} phút (bậc {card.lock_level}) vì {reason} x{rule[0]}"
    db.add(AccessLog(event=LogEvent.ADMIN_ACTION, student_id=student_id, result=AccessResult.DENIED,
                     reason="card_locked", device="system", note=note))
    db.add(Alert(level=AlertLevel.MEDIUM, reason="card_locked", student_id=student_id, message=note))
    return True


def remaining_text(card: Card, now: datetime) -> str:
    seconds = max(0, int((card.locked_until - now).total_seconds())) if card.locked_until else 0
    minutes = max(1, (seconds + 59) // 60)
    return f"còn khoảng {minutes} phút"
