"""Nạp dữ liệu demo khớp với firmware Wokwi (thẻ A123, B456, C789).

Chạy từ thư mục backend:  python -m scripts.seed_demo

- Khuôn mặt lấy từ Iot/ai_service/embeddings/<mã_sv>.npy (đã tạo bằng enrollment.py), nên không cần chụp lại ảnh.
- Chạy lại nhiều lần an toàn: bản ghi đã có sẽ được giữ nguyên (chỉ bổ sung phần thiếu).
"""
import sys
from pathlib import Path

import numpy as np

from app.core.database import Base, SessionLocal, engine
from app.core.security import hash_password
from app.models import Card, User
from app.main import seed_admin

EMBEDDINGS = Path(__file__).resolve().parents[2] / "ai_service" / "embeddings"
DEMO_PASSWORD = "123456"

# (mã sinh viên, họ tên, UID thẻ nhập trên keypad Wokwi)
STUDENTS = [
    ("B23DCAT286", "Sinh viên B23DCAT286", "A123"),
    ("B23DCAT296", "Sinh viên B23DCAT296", "B456"),
    ("B23DCAT999", "Sinh viên chưa đăng ký mặt", "C789"),   # thẻ hợp lệ nhưng chưa có mặt -> bị từ chối
]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    Base.metadata.create_all(engine)
    seed_admin()
    with SessionLocal() as db:
        for student_id, name, uid in STUDENTS:
            user = db.query(User).filter(User.student_id == student_id).first()
            if user is None:
                user = User(student_id=student_id, full_name=name, password_hash=hash_password(DEMO_PASSWORD))
                db.add(user)
                print(f"[+] Tạo {student_id}")
            if not db.query(Card).filter(Card.uid == uid).first():
                user.cards.append(Card(uid=uid))
                print(f"[+] Gán thẻ {uid} cho {student_id}")
            npy = EMBEDDINGS / f"{student_id}.npy"
            if not user.has_face and npy.exists():
                vec = np.load(npy).astype(np.float32)
                vec = vec / np.linalg.norm(vec)
                user.set_embedding([round(float(v), 6) for v in vec])
                print(f"[+] Nạp khuôn mặt {student_id} từ {npy.name}")
        db.commit()
    print(f"Xong. Sinh viên đăng nhập web bằng mã SV, mật khẩu mặc định '{DEMO_PASSWORD}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
