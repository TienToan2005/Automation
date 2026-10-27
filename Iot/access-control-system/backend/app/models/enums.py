from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    STUDENT = "student"


class UserStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class CardStatus(StrEnum):
    """Trạng thái LƯU của thẻ. Trạng thái HIỆU LỰC (có tính hết hạn / khóa tạm) do Card.effective_status() tính ra."""
    ACTIVE = "active"
    REVOKED = "revoked"          # báo mất / thu hồi vĩnh viễn: chỉ admin cấp lại thẻ mới
    DISABLED = "disabled"        # admin vô hiệu hóa, có thể bật lại


class EffectiveCardStatus(StrEnum):
    ACTIVE = "active"
    TEMPORARILY_LOCKED = "temporarily_locked"
    EXPIRED = "expired"
    DISABLED = "disabled"
    REVOKED = "revoked"


class SessionState(StrEnum):
    """Vòng đời một phiên xác thực (mỗi lần quẹt thẻ hợp lệ). Mọi trạng thái khác PENDING đều là cuối cùng."""
    PENDING = "pending"
    GRANTED = "granted"
    DENIED = "denied"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    ERROR = "error"


class Reason:
    """Mã lý do (chuỗi, lưu trong AccessLog.reason). Ánh xạ sang tên nghiệp vụ trong docs/NGHIEP_VU_XAC_THUC.md."""
    # thẻ / tài khoản
    UNKNOWN_CARD = "unknown_card"            # CARD_UNKNOWN
    CARD_REVOKED = "card_revoked"            # CARD_REVOKED
    CARD_EXPIRED = "card_expired"            # CARD_EXPIRED
    CARD_LOCKED = "card_locked"              # CARD_TEMPORARILY_LOCKED
    CARD_DISABLED = "card_disabled"          # CARD_INACTIVE
    ACCOUNT_SUSPENDED = "account_suspended"  # ACCESS_DENIED (danh tính bị đình chỉ)
    RATE_LIMITED = "rate_limited"
    # hệ thống
    AI_UNAVAILABLE = "ai_unavailable"        # SYSTEM_ERROR
    NOT_ENROLLED = "not_enrolled"
    # khuôn mặt
    FACE_MISMATCH = "face_mismatch"          # FACE_MISMATCH (đã xác nhận: đủ chất lượng và độ khớp thấp rõ rệt)
    SPOOF_PAD = "spoof_pad"                  # SPOOF_DETECTED (PAD chắc chắn)
    CHALLENGE_FAILED = "challenge_failed"    # LIVENESS_FAILED
    PAD_UNCERTAIN = "pad_uncertain"          # PAD chưa đủ tin cậy: KHÔNG phải tấn công
    FACE_NOT_DETECTED = "face_not_detected"  # FACE_NOT_DETECTED
    FACE_QUALITY_LOW = "face_quality_low"    # FACE_QUALITY_LOW
    FACE_INCONCLUSIVE = "face_inconclusive"  # FACE_MATCH_INCONCLUSIVE
    TIMEOUT = "timeout"                      # AUTH_TIMEOUT
    CANCELLED = "cancelled"                  # AUTH_CANCELLED
    SUPERSEDED = "superseded"                # AUTH_CANCELLED (có phiên mới thay thế)
    OK = "ok"                                # ACCESS_GRANTED
    # cửa
    DOOR_UNLOCKED = "door_unlocked"
    DOOR_LOCKED = "door_locked"


# Lỗi KHÔNG phải bằng chứng an ninh: không bao giờ tăng bộ đếm sai / khóa thẻ
NON_SECURITY_REASONS = frozenset({
    Reason.AI_UNAVAILABLE, Reason.NOT_ENROLLED, Reason.PAD_UNCERTAIN, Reason.FACE_NOT_DETECTED, Reason.FACE_QUALITY_LOW,
    Reason.FACE_INCONCLUSIVE, Reason.TIMEOUT, Reason.CANCELLED, Reason.SUPERSEDED, Reason.RATE_LIMITED,
})


class LogEvent(StrEnum):
    CARD_SCAN = "card_scan"
    FACE_VERIFY = "face_verify"
    EXIT_BUTTON = "exit_button"
    REMOTE_OPEN = "remote_open"
    ADMIN_ACTION = "admin_action"
    DOOR_EVENT = "door_event"


class AccessResult(StrEnum):
    GRANTED = "granted"
    DENIED = "denied"
    PENDING = "pending"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class AlertLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
