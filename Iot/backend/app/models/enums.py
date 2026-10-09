from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    STUDENT = "student"


class UserStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class CardStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


class LogEvent(StrEnum):
    CARD_SCAN = "card_scan"
    FACE_VERIFY = "face_verify"
    EXIT_BUTTON = "exit_button"
    REMOTE_OPEN = "remote_open"
    ADMIN_ACTION = "admin_action"


class AccessResult(StrEnum):
    GRANTED = "granted"
    DENIED = "denied"
    PENDING = "pending"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class AlertLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
