"""Nâng cấp DB cũ (trước khi có trạng thái thẻ + bảng phiên): dữ liệu giữ nguyên, cột mới có giá trị mặc định an toàn."""
from sqlalchemy import create_engine, inspect, text

from app.core.database import Base, add_missing_columns
from app import models  # noqa: F401  (đăng ký bảng)

OLD_SCHEMA = [
    "CREATE TABLE users (id INTEGER PRIMARY KEY, student_id VARCHAR(32) UNIQUE, full_name VARCHAR(100), password_hash VARCHAR(200),"
    " role VARCHAR(10), status VARCHAR(12), suspend_reason VARCHAR(200), face_embedding TEXT, face_enrolled_at DATETIME, created_at DATETIME)",
    "CREATE TABLE cards (id INTEGER PRIMARY KEY, uid VARCHAR(32) UNIQUE, user_id INTEGER, status VARCHAR(10), created_at DATETIME)",
    "INSERT INTO users (id, student_id, full_name, password_hash, role, status, suspend_reason, created_at)"
    " VALUES (1, 'B23DCAT286', 'SV A', 'x', 'student', 'active', '', '2026-10-01 00:00:00')",
    "INSERT INTO cards (id, uid, user_id, status, created_at) VALUES (1, 'A123', 1, 'active', '2026-10-01 00:00:00')",
    "INSERT INTO cards (id, uid, user_id, status, created_at) VALUES (2, 'DEAD', 1, 'revoked', '2026-10-01 00:00:00')",
]


def test_old_database_is_upgraded_in_place_without_losing_data(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'old.db').as_posix()}")
    with engine.begin() as conn:
        for stmt in OLD_SCHEMA:
            conn.execute(text(stmt))

    Base.metadata.create_all(engine)              # tạo bảng mới (auth_sessions) nhưng không đụng bảng cũ
    added = add_missing_columns(engine)           # đúng hai bước app/main.py chạy lúc khởi động

    cols = {c["name"] for c in inspect(engine).get_columns("cards")}
    assert {"expires_at", "locked_until", "lock_reason", "lock_level", "last_locked_at", "failures_reset_at"} <= cols
    assert "cards.lock_level" in added and inspect(engine).has_table("auth_sessions")
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT uid, status, lock_level, lock_reason, locked_until, expires_at FROM cards ORDER BY id")).all()
    assert rows == [("A123", "active", 0, "", None, None), ("DEAD", "revoked", 0, "", None, None)]
    assert add_missing_columns(engine) == [], "chạy lại phải không làm gì"
