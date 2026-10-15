from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
DEFAULT_SECRET_KEY = "dev-secret-change-me-dev-secret-change-me"


class Settings(BaseSettings):
    """Cấu hình đọc từ biến môi trường `LAB_*` hoặc file `.env` (xem .env.example)."""

    model_config = SettingsConfigDict(env_prefix="LAB_", env_file=BASE_DIR / ".env", extra="ignore")

    database_url: str = f"sqlite:///{(DATA_DIR / 'lab.db').as_posix()}"
    secret_key: str = DEFAULT_SECRET_KEY
    token_ttl_hours: int = 8

    # Token giữa các service / thiết bị (xem docs/api-spec.md)
    service_token: str = "dev-service-token"      # AI Service -> Backend (X-Service-Token)
    device_token: str = "dev-device-token"        # ESP32 -> Backend (X-Device-Token)

    ai_base_url: str = "http://localhost:5050"
    ai_timeout_s: float = 3.0
    face_timeout_s: int = 16                      # khớp SCAN_TIMEOUT_S của AI Service
    min_face_images: int = 3

    default_device_id: str = "door-esp32-01"
    unlock_ms: int = 5000                         # thời gian mở khóa khi admin mở từ xa
    device_offline_after_s: int = 10              # quá thời gian này không heartbeat = offline
    command_ttl_s: int = 30                       # lệnh chưa được thiết bị nhận sẽ bị hủy

    admin_id: str = "admin"
    admin_password: str = "admin123"

    # Múi giờ hiển thị trong báo cáo / tin nhắn. DB luôn lưu UTC; Việt Nam là UTC+7, không có giờ mùa hè
    utc_offset_hours: int = 7

    # Thông báo tức thời (xem services/notifier.py). Kênh nào đủ cấu hình thì tự bật.
    notify_min_level: str = "medium"              # info | medium | high: mức tối thiểu để đẩy ra Telegram / Email
    notify_cooldown_s: int = 30                   # cùng một loại sự kiện chỉ đẩy ra ngoài một lần trong khoảng này
    public_base_url: str = ""                     # vd http://192.168.1.10:8080, để tin nhắn kèm link tới trang web
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    smtp_host: str = ""
    smtp_port: int = 587                          # 465 = SSL; còn lại dùng STARTTLS nếu smtp_starttls
    smtp_starttls: bool = True
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""                           # bỏ trống = dùng smtp_user
    smtp_to: str = ""                             # nhiều người nhận ngăn cách bằng dấu phẩy

    export_max_rows: int = 200_000                # trần số dòng nhật ký khi xuất CSV

    @property
    def is_default_secret(self) -> bool:
        return self.secret_key == DEFAULT_SECRET_KEY


settings = Settings()
DATA_DIR.mkdir(exist_ok=True)
SNAPSHOT_DIR.mkdir(exist_ok=True)
