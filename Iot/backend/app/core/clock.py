from datetime import datetime, timezone


def utcnow() -> datetime:
    """Giờ UTC không kèm tzinfo (cách DB lưu thời gian trong dự án này)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
