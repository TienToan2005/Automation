import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt

from .config import settings

_ITERATIONS = 200_000
_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"{salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), _ITERATIONS)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except ValueError:
        return False


def create_access_token(subject: str, role: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=settings.token_ttl_hours)
    return jwt.encode({"sub": subject, "role": role, "exp": exp}, settings.secret_key, algorithm=_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Ném jwt.PyJWTError nếu token sai hoặc hết hạn."""
    return jwt.decode(token, settings.secret_key, algorithms=[_ALGORITHM])


def tokens_match(supplied: str | None, expected: str) -> bool:
    return bool(supplied) and hmac.compare_digest(supplied, expected)
