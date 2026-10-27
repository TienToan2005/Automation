"""Báo cáo ra vào (admin): số liệu cho trang /reports và các file xuất."""
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ...core.database import get_db
from ...models import User
from ...services import report_service as svc
from ..deps import require_admin

router = APIRouter(prefix="/reports", tags=["reports"])

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _attachment(name: str) -> dict:
    return {"Content-Disposition": f'attachment; filename="{name}"'}


@router.get("/summary")
def summary(date_from: str | None = None, date_to: str | None = None, _: User = Depends(require_admin),
            db: Session = Depends(get_db)):
    """Số liệu tổng hợp theo NGÀY địa phương (gồm cả hai đầu). Mặc định 7 ngày gần nhất."""
    start, end = svc.parse_range(date_from, date_to)
    return svc.build_summary(db, start, end)


@router.get("/export/logs.csv")
def export_logs(date_from: str | None = None, date_to: str | None = None, result: str | None = None,
                event: str | None = None, reason: str | None = None, student_id: str | None = None,
                _: User = Depends(require_admin)):
    start, end = svc.parse_range(date_from, date_to)
    rows = svc.iter_logs_csv(start, end, result=result, event=event, reason=reason, student_id=student_id)
    return StreamingResponse(rows, media_type="text/csv; charset=utf-8", headers=_attachment(f"nhat-ky_{start}_{end}.csv"))


@router.get("/export/alerts.csv")
def export_alerts(date_from: str | None = None, date_to: str | None = None, _: User = Depends(require_admin)):
    start, end = svc.parse_range(date_from, date_to)
    return StreamingResponse(svc.iter_alerts_csv(start, end), media_type="text/csv; charset=utf-8",
                             headers=_attachment(f"canh-bao_{start}_{end}.csv"))


@router.get("/export/report.xlsx")
def export_report(date_from: str | None = None, date_to: str | None = None, _: User = Depends(require_admin),
                  db: Session = Depends(get_db)):
    start, end = svc.parse_range(date_from, date_to)
    try:
        content = svc.build_workbook(db, start, end)
    except ImportError:
        raise HTTPException(501, "Chưa cài openpyxl nên không xuất được Excel. Chạy: pip install openpyxl (hoặc dùng xuất CSV)")
    return Response(content, media_type=XLSX, headers=_attachment(f"bao-cao_{start}_{end}.xlsx"))
