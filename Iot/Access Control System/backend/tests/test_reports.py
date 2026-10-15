import io
from datetime import datetime

import pytest
from sqlalchemy import create_engine, text

from app.core.database import SessionLocal, add_missing_columns
from app.models import AccessLog, Alert
from app.services.report_service import outcome

DAY = "2026-01-15"        # giờ địa phương UTC+7: 15/01 00:00 = 14/01 17:00 UTC. Cố định trong quá khứ (xem data())


def add_log(ts, event="face_verify", result="granted", reason="ok", student_id="B23DCAT286", confidence=None, note=""):
    with SessionLocal() as db:
        db.add(AccessLog(ts=datetime.fromisoformat(ts), event=event, result=result, reason=reason, student_id=student_id,
                         confidence=confidence, pad_score=0.9 if confidence else None, device="d1", note=note))
        db.commit()


@pytest.fixture()
def data(admin, student):
    """Một ngày 15/01/2026 (giờ địa phương) có đủ loại sự kiện, kèm các dòng nằm sát hai biên của ngày."""
    add_log("2026-01-14T17:30:00", confidence=0.80, note="4000 ms, thử thách: blink")      # 00:30 ngày 15 (giờ 0)
    add_log("2026-01-15T03:00:00", confidence=0.90, note="6000 ms")                         # 10:00
    add_log("2026-01-15T03:30:00", student_id="S2", confidence=0.85, note="5000 ms")        # 10:30, người khác
    add_log("2026-01-15T16:59:59", confidence=0.88, note="5000 ms")                         # 23:59:59 vẫn là ngày 15
    add_log("2026-01-15T17:00:00", confidence=0.88, note="5000 ms")                         # 00:00 ngày 16
    add_log("2026-01-15T04:00:00", result="denied", reason="spoof_pad", student_id="S2")
    add_log("2026-01-15T04:05:00", event="card_scan", result="denied", reason="unknown_card", student_id=None)
    add_log("2026-01-15T05:00:00", result="timeout", reason="timeout", student_id="S2")
    add_log("2026-01-15T03:00:00", event="card_scan", result="pending", reason="scan_face")   # mở đầu phiên: không tính
    add_log("2026-01-15T06:00:00", event="exit_button", reason="exit_button", student_id=None)
    add_log("2026-01-15T07:00:00", event="remote_open", reason="remote_open", student_id="admin")
    with SessionLocal() as db:
        db.add(Alert(ts=datetime(2026, 1, 15, 4, 0), level="high", reason="spoof_pad", student_id="S2", message="m",
                     resolved=True, resolved_by="admin", resolved_at=datetime(2026, 1, 15, 4, 30)))
        db.add(Alert(ts=datetime(2026, 1, 15, 5, 0), level="medium", reason="face_mismatch", student_id="S2", message="m2"))
        db.commit()
    return admin


def summary(client, **params):
    r = client.get("/api/v1/reports/summary", params=params)
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------ Tổng hợp ------------------------------
def test_outcome_classification():
    assert outcome("face_verify", "granted") == "entry"
    assert outcome("card_scan", "denied") == "denied" and outcome("card_scan", "pending") is None
    assert outcome("face_verify", "timeout") == "timeout" and outcome("exit_button", "granted") == "exit_button"
    assert outcome("face_verify", "pending") is None


def test_summary_counts_in_local_day(data):
    s = summary(data, date_from=DAY, date_to=DAY)
    t = s["totals"]
    assert (t["entries"], t["denied"], t["timeouts"]) == (4, 2, 1)          # dòng 17:00Z thuộc ngày 16 nên bị loại
    assert (t["exit_button"], t["remote_open"], t["unique_users"]) == (1, 1, 2)
    assert t["success_rate"] == round(4 / 7, 4)
    assert t["avg_confidence"] == 0.858 and t["avg_duration_ms"] == 5000.0   # (4000+6000+5000+5000)/4
    assert s["range"]["days"] == 1 and s["range"]["utc_offset_hours"] == 7


def test_summary_buckets_by_local_hour_and_day(data):
    s = summary(data, date_from=DAY, date_to="2026-01-16")
    assert [d["date"] for d in s["by_day"]] == [DAY, "2026-01-16"]
    assert [d["entries"] for d in s["by_day"]] == [4, 1]
    hours = {h["hour"]: h for h in s["by_hour"]}
    assert len(s["by_hour"]) == 24
    assert hours[0]["entries"] == 2 and hours[23]["entries"] == 1          # 00:30 ngày 15 và 00:00 ngày 16 đều là giờ 0
    assert hours[11]["denied"] == 2 and hours[12]["denied"] == 1 and hours[10]["entries"] == 2


def test_summary_reasons_ranking_and_alerts(data):
    s = summary(data, date_from=DAY, date_to=DAY)
    assert {r["reason"]: r["count"] for r in s["denial_reasons"]} == {"spoof_pad": 1, "unknown_card": 1, "timeout": 1}
    assert next(r for r in s["denial_reasons"] if r["reason"] == "spoof_pad")["label"].startswith("Giả mạo")
    assert [(u["student_id"], u["entries"]) for u in s["top_users"]] == [("B23DCAT286", 3), ("S2", 1)]
    assert s["top_users"][0]["full_name"] == "SV A"
    assert s["top_denied"][0] == {"student_id": "S2", "full_name": "", "entries": 1, "denied": 2}
    a = s["alerts"]
    assert (a["total"], a["open"], a["resolved"], a["avg_resolve_minutes"]) == (2, 1, 1, 30.0)
    assert a["by_level"] == {"high": 1, "medium": 1}


def test_summary_empty_range_and_defaults(admin):
    s = summary(admin)
    assert s["range"]["days"] == 7 and s["range"]["to"] == s["range"]["today"]
    assert s["totals"]["entries"] == 0 and s["totals"]["success_rate"] is None and s["totals"]["avg_confidence"] is None
    assert len(s["by_day"]) == 7 and all(d["entries"] == 0 for d in s["by_day"])


@pytest.mark.parametrize("params", [{"date_from": "14/10/2026"}, {"date_from": "2026-01-16", "date_to": "2026-01-15"},
                                    {"date_from": "2024-01-01", "date_to": "2026-01-01"}])
def test_summary_rejects_bad_ranges(admin, params):
    assert admin.get("/api/v1/reports/summary", params=params).status_code == 400


def test_reports_are_admin_only(client, student):
    client.post("/api/v1/auth/logout")
    for path in ("summary", "export/logs.csv", "export/alerts.csv", "export/report.xlsx"):
        assert client.get(f"/api/v1/reports/{path}").status_code == 401
    client.post("/api/v1/auth/login", json={"student_id": "B23DCAT286", "password": "123456"})
    for path in ("summary", "export/logs.csv", "export/alerts.csv", "export/report.xlsx"):
        assert client.get(f"/api/v1/reports/{path}").status_code == 403
    assert client.get("/reports").status_code == 403


# ------------------------------ Xuất file ------------------------------
def test_logs_csv_export(data):
    add_log("2026-01-15T08:00:00", event="remote_open", result="granted", reason="remote_open", student_id="admin",
            note="=HYPERLINK(\"http://evil\")")
    r = data.get("/api/v1/reports/export/logs.csv", params={"date_from": DAY, "date_to": DAY})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"] == f'attachment; filename="nhat-ky_{DAY}_{DAY}.csv"'
    assert r.content.startswith(b"\xef\xbb\xbf")                            # BOM để Excel đọc đúng tiếng Việt
    lines = r.content.decode("utf-8-sig").strip().split("\r\n")
    assert lines[0].startswith("Thời gian (UTC+7),Mã SV,Họ tên,Sự kiện,Kết quả,Lý do,Mã lý do")
    assert len(lines) == 1 + 11                                             # 10 dòng của fixture + dòng vừa thêm; dòng 17:00Z thuộc ngày sau
    assert lines[1].startswith("2026-01-15 00:30:00,B23DCAT286,SV A,Quét mặt,Cho vào,")    # sắp theo thời gian, giờ địa phương
    assert "'=HYPERLINK" in r.text and ",=HYPERLINK" not in r.text         # chặn CSV injection


def test_logs_csv_filters(data):
    r = data.get("/api/v1/reports/export/logs.csv", params={"date_from": DAY, "date_to": DAY, "result": "denied", "student_id": "S2"})
    rows = r.content.decode("utf-8-sig").strip().split("\r\n")[1:]
    assert len(rows) == 1 and "Giả mạo" in rows[0] and "spoof_pad" in rows[0]


def test_alerts_csv_export(data):
    rows = data.get("/api/v1/reports/export/alerts.csv", params={"date_from": DAY, "date_to": DAY}).content.decode("utf-8-sig").split("\r\n")
    assert rows[0].startswith("Thời gian (UTC+7),Mức,Loại,Mã SV")
    assert "2026-01-15 11:00:00,Cao,Giả mạo (ảnh in / màn hình),S2" in rows[1] and "Đã xử lý,admin,2026-01-15 11:30:00" in rows[1]
    assert "Chưa xử lý" in rows[2]


def test_xlsx_export(data):
    openpyxl = pytest.importorskip("openpyxl")
    r = data.get("/api/v1/reports/export/report.xlsx", params={"date_from": DAY, "date_to": "2026-01-16"})
    assert r.status_code == 200 and r.headers["content-disposition"].endswith('.xlsx"')
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["Tổng quan", "Theo ngày", "Theo giờ", "Lý do từ chối", "Sinh viên", "Cảnh báo", "Nhật ký"]
    overview = {row[0]: row[1] for row in wb["Tổng quan"].iter_rows(min_row=2, values_only=True)}
    assert overview["Lượt vào hợp lệ (thẻ + khuôn mặt)"] == 5 and overview["Lượt bị từ chối"] == 2
    assert [row[:2] for row in wb["Theo ngày"].iter_rows(min_row=2, values_only=True)] == [(DAY, 4), ("2026-01-16", 1)]
    assert len(wb["Theo ngày"]._charts) == 1 and len(wb["Theo giờ"]._charts) == 1
    assert wb["Nhật ký"].max_row == 1 + 11 and wb["Cảnh báo"].max_row == 3
    assert [r[0] for r in wb["Sinh viên"].iter_rows(min_row=2, values_only=True)] == ["B23DCAT286", "S2"]


def test_xlsx_text_starting_with_equals_is_not_a_formula(admin):
    openpyxl = pytest.importorskip("openpyxl")
    add_log("2026-01-15T03:00:00", event="remote_open", result="granted", reason="remote_open", student_id="admin",
            note='=HYPERLINK("http://evil","bấm vào")')
    wb = openpyxl.load_workbook(io.BytesIO(admin.get("/api/v1/reports/export/report.xlsx", params={"date_from": DAY, "date_to": DAY}).content))
    cell = next(c for row in wb["Nhật ký"].iter_rows(min_row=2) for c in row if str(c.value).startswith("=HYPERLINK"))
    assert cell.data_type == "s" and cell.value == '=HYPERLINK("http://evil","bấm vào")'


def test_xlsx_without_openpyxl_is_501(admin, monkeypatch):
    import builtins
    real = builtins.__import__

    def no_openpyxl(name, *a, **k):
        if name.startswith("openpyxl"):
            raise ImportError(name)
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_openpyxl)
    r = admin.get("/api/v1/reports/export/report.xlsx")
    monkeypatch.undo()
    assert r.status_code == 501 and "openpyxl" in r.json()["detail"]


def test_report_page_renders_for_admin(admin):
    r = admin.get("/reports")
    assert r.status_code == 200 and "Báo cáo" in r.text


# ------------------------------ Lọc nhật ký theo ngày địa phương ------------------------------
def test_access_log_date_filter_uses_local_days(admin):
    add_log("2026-01-14T18:00:00", note="01:00 ngày 15")          # = 15/01 01:00 địa phương
    add_log("2026-01-15T18:00:00", note="01:00 ngày 16")
    items = admin.get("/api/v1/access-logs", params={"date_from": DAY, "date_to": DAY, "event": "face_verify"}).json()["items"]
    assert [i["note"] for i in items] == ["01:00 ngày 15"]
    items = admin.get("/api/v1/access-logs", params={"date_from": "2026-01-14T18:00:00"}).json()["items"]      # có giờ: UTC như cũ
    assert len(items) == 2


# ------------------------------ Cảnh báo: phân trang, ghi chú xử lý ------------------------------
def make_alerts(n=5):
    with SessionLocal() as db:
        for i in range(n):
            db.add(Alert(ts=datetime(2026, 1, 15, 1, i), level="high" if i % 2 == 0 else "medium",
                         reason="spoof_pad" if i % 2 == 0 else "face_mismatch", student_id="S1", message=f"m{i}"))
        db.commit()


def test_alerts_pagination_and_filters(admin):
    make_alerts()
    r = admin.get("/api/v1/alerts", params={"limit": 2, "offset": 2})
    assert [a["message"] for a in r.json()] == ["m2", "m1"] and r.headers["x-total-count"] == "5"      # mới nhất trước
    r = admin.get("/api/v1/alerts", params={"level": "high"})
    assert r.headers["x-total-count"] == "3" and {a["reason"] for a in r.json()} == {"spoof_pad"}
    assert admin.get("/api/v1/alerts", params={"reason": "face_mismatch"}).headers["x-total-count"] == "2"


def test_resolve_alert_records_who_when_and_note(admin):
    make_alerts(2)
    aid = admin.get("/api/v1/alerts").json()[0]["id"]
    assert admin.post(f"/api/v1/alerts/{aid}/resolve", json={"note": "  Đã hỏi sinh viên, nhầm thẻ  "}).status_code == 200
    a = next(x for x in admin.get("/api/v1/alerts", params={"include_resolved": True}).json() if x["id"] == aid)
    assert a["resolved"] and a["resolved_by"] == "admin" and a["resolution_note"] == "Đã hỏi sinh viên, nhầm thẻ"
    assert a["resolved_at"].endswith("Z")
    admin.post(f"/api/v1/alerts/{aid}/resolve", json={"note": "ghi đè?"})            # gọi lại: giữ nguyên bản ghi đầu
    a2 = next(x for x in admin.get("/api/v1/alerts", params={"include_resolved": True}).json() if x["id"] == aid)
    assert a2["resolution_note"] == a["resolution_note"] and a2["resolved_at"] == a["resolved_at"]
    assert len(admin.get("/api/v1/alerts").json()) == 1


def test_resolve_alert_without_body_and_unknown(admin):
    make_alerts(1)
    aid = admin.get("/api/v1/alerts").json()[0]["id"]
    assert admin.post(f"/api/v1/alerts/{aid}/resolve").status_code == 200           # tương thích client cũ không gửi body
    assert admin.post("/api/v1/alerts/9999/resolve").status_code == 404
    assert admin.post(f"/api/v1/alerts/{aid}/resolve", json={"note": "x" * 301}).status_code == 422


# ------------------------------ Migration cột còn thiếu ------------------------------
def test_add_missing_columns_upgrades_old_database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as c:       # bảng alerts của phiên bản cũ: chưa có resolved_at / resolution_note
        c.execute(text("CREATE TABLE alerts (id INTEGER PRIMARY KEY, ts DATETIME, level VARCHAR(10), reason VARCHAR(32), "
                       "student_id VARCHAR(32), message VARCHAR(300), snapshot VARCHAR(80), resolved BOOLEAN, resolved_by VARCHAR(32))"))
        c.execute(text("INSERT INTO alerts (ts, level, reason, message, resolved) VALUES ('2026-01-15 01:00:00', 'high', 'spoof_pad', 'cũ', 0)"))
    assert add_missing_columns(engine) == ["alerts.resolved_at", "alerts.resolution_note"]
    with engine.connect() as c:
        row = c.execute(text("SELECT message, resolved_at, resolution_note FROM alerts")).one()
    assert tuple(row) == ("cũ", None, "")
    assert add_missing_columns(engine) == []                      # chạy lại không làm gì
