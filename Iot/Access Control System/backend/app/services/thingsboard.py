"""Đẩy sự kiện cửa (mỗi dòng AccessLog mới) lên ThingsBoard qua HTTP device API.

Tự tắt khi LAB_THINGSBOARD_URL để trống. Chỉ gửi SAU khi giao dịch DB đã commit (rollback thì không gửi gì),
và gửi ở luồng nền: ThingsBoard chậm hoặc mất mạng thì cửa vẫn hoạt động bình thường, lỗi chỉ ghi log.
URL có dạng https://thingsboard.cloud/api/v1/<TOKEN>/telemetry, chứa token nên không bao giờ in ra log.
"""
import logging
import threading

import requests
from sqlalchemy import event
from sqlalchemy.orm import Session, object_session

from ..core.config import settings
from ..models import AccessLog, AccessResult

log = logging.getLogger("thingsboard")
_KEY = "thingsboard_pending"
TIMEOUT_S = 5


def enabled() -> bool:
    return bool(settings.thingsboard_url)


def _payload(row: AccessLog) -> dict:
    p = {"event": row.event, "result": row.result, "granted": int(row.result == AccessResult.GRANTED),
         "reason": row.reason or "", "device": row.device or "", "student_id": row.student_id or ""}
    if row.confidence is not None:
        p["confidence"] = round(row.confidence, 3)
    if row.pad_score is not None:
        p["pad_score"] = round(row.pad_score, 3)
    return p     # không gửi "ts": ThingsBoard lấy giờ lúc nhận


def _post(payload: dict) -> None:
    try:
        requests.post(settings.thingsboard_url, json=payload, timeout=TIMEOUT_S).raise_for_status()
    except Exception as e:
        log.warning("Gui ThingsBoard that bai: %s", type(e).__name__)     # không in e: thông báo lỗi có thể chứa URL (token)


def _dispatch(payload: dict) -> None:
    threading.Thread(target=_post, args=(payload,), daemon=True).start()


@event.listens_for(AccessLog, "after_insert")
def _collect(_mapper, _conn, row: AccessLog) -> None:
    if not enabled():
        return
    s = object_session(row)
    if s is not None:
        s.info.setdefault(_KEY, []).append(_payload(row))


@event.listens_for(Session, "after_commit")
def _flush(session: Session) -> None:
    for p in session.info.pop(_KEY, []):
        _dispatch(p)


@event.listens_for(Session, "after_rollback")
def _drop(session: Session) -> None:
    session.info.pop(_KEY, None)
