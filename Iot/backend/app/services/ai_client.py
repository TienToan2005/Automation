"""Client gọi AI Service (Iot/ai_service, cổng 5050)."""
import base64

import numpy as np
import requests

from ..core.config import settings


class AIUnavailable(Exception):
    pass


def _url(path: str) -> str:
    return f"{settings.ai_base_url}{path}"


def trigger(student_id: str, session_id: str, embedding: list[float]) -> dict:
    try:
        r = requests.post(_url("/api/ai/trigger"), timeout=settings.ai_timeout_s,
                          json={"student_id": student_id, "session_id": session_id, "embedding": embedding})
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError) as e:
        raise AIUnavailable(str(e))


def cancel() -> None:
    try:
        requests.post(_url("/api/ai/cancel"), timeout=1.5)
    except requests.RequestException:
        pass


def status() -> dict | None:
    try:
        return requests.get(_url("/api/ai/status"), timeout=1.5).json()
    except (requests.RequestException, ValueError):
        return None


def health() -> dict | None:
    try:
        return requests.get(_url("/api/ai/health"), timeout=2).json()
    except (requests.RequestException, ValueError):
        return None


def embed(image_bytes: bytes) -> dict:
    try:
        r = requests.post(_url("/api/ai/embed"), timeout=15,
                          json={"image_base64": base64.b64encode(image_bytes).decode()})
        if r.status_code == 400:
            return {"ok": False, "reason": "invalid_image"}
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError) as e:
        raise AIUnavailable(str(e))


def average_embeddings(embeddings: list[list[float]]) -> list[float]:
    """Trung bình các vector rồi chuẩn hóa lại (AI so khớp bằng cosine)."""
    mean = np.mean(np.asarray(embeddings, dtype=np.float32), axis=0)
    mean = mean / np.linalg.norm(mean)
    return [round(float(v), 6) for v in mean]
