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

    @property
    def is_default_secret(self) -> bool:
        return self.secret_key == DEFAULT_SECRET_KEY


settings = Settings()
DATA_DIR.mkdir(exist_ok=True)
SNAPSHOT_DIR.mkdir(exist_ok=True)
