import logging

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _sql_literal(value) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def add_missing_columns(bind=None) -> list[str]:
    """Migration tối thiểu: `create_all` không thêm cột vào bảng đã có, nên cột mới thêm vào model sẽ làm DB cũ báo
    "no such column". Hàm này `ALTER TABLE ADD COLUMN` cho các cột còn thiếu (chỉ cột cho phép NULL hoặc có giá trị mặc
    định hằng số). Không đổi kiểu / xóa cột; thay đổi lớn hơn thì dùng Alembic. Trả về danh sách "bảng.cột" đã thêm."""
    log = logging.getLogger("db")
    bind = bind or engine
    added = []
    existing = inspect(bind)
    with bind.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not existing.has_table(table.name):
                continue
            have = {c["name"] for c in existing.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(dialect=bind.dialect)}"
                if not col.nullable:
                    default = getattr(col.default, "arg", None)
                    if not isinstance(default, (str, int, float, bool)):
                        log.warning("Bảng %s thiếu cột %s (NOT NULL, không có mặc định hằng số): "
                                    "hãy migrate tay hoặc xóa data/lab.db", table.name, col.name)
                        continue
                    ddl += f" NOT NULL DEFAULT {_sql_literal(default)}"
                conn.execute(text(ddl))
                added.append(f"{table.name}.{col.name}")
    if added:
        log.warning("Đã thêm cột còn thiếu vào DB cũ: %s", ", ".join(added))
    return added
