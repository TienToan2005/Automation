from datetime import date, datetime, time, timedelta, timezone

from .config import settings


def utcnow() -> datetime:
    """Giờ UTC không kèm tzinfo (cách DB lưu thời gian trong dự án này)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---- Giờ địa phương (báo cáo, lọc theo ngày). DB lưu UTC, "một ngày" của người dùng là ngày theo UTC+7 ----
def local_offset() -> timedelta:
    return timedelta(hours=settings.utc_offset_hours)


def to_local(dt_utc: datetime) -> datetime:
    return dt_utc + local_offset()


def local_today() -> date:
    return to_local(utcnow()).date()


def local_day_start_utc(day: date) -> datetime:
    """Thời điểm 00:00 của ngày `day` theo giờ địa phương, quy ra UTC (không kèm tzinfo)."""
    return datetime.combine(day, time.min) - local_offset()
