"""Pipeline đăng ký khuôn mặt, dùng chung cho hai nơi:

* `analyze_image()` : được `main.py` gọi trong `POST /api/ai/embed` (Web quản lý -> Backend -> AI).
* `enroll_faces()`  : chạy tay `python enrollment.py` để trích vector từ thư mục `faces_db/` (chế độ offline).

Các bước kiểm tra cho MỖI ảnh (dừng ở lỗi đầu tiên):
    giải mã ảnh -> đúng 1 khuôn mặt -> mặt đủ lớn & detector tự tin -> đủ nét -> không phải ảnh giả (PAD)
    -> trả vector 512 chiều đã chuẩn hóa L2 (cùng không gian với vector dùng khi so khớp ở main.py).

Module này không import `main` (tránh vòng lặp); mô hình và ngưỡng được truyền vào từ bên gọi.
"""
import base64
import os

import cv2
import numpy as np

EMBEDDING_DIM = 512
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MIN_DET_SCORE = 0.6


def decode_image(data):
    """Nhận bytes hoặc chuỗi base64 (chấp nhận cả data URL). Trả ảnh BGR, hoặc ném ValueError."""
    if isinstance(data, str):
        data = base64.b64decode(data.split(",", 1)[-1], validate=False)
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("image too large")
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("cannot decode image")
    return img


def sharpness(img, bbox):
    """Độ nét = phương sai Laplacian của vùng mặt (đưa về 160x160 để không phụ thuộc kích thước mặt)."""
    h, w = img.shape[:2]
    x1, y1, x2, y2 = [int(v) for v in bbox]
    crop = img[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
    if crop.size == 0:
        return 0.0
    gray = cv2.cvtColor(cv2.resize(crop, (160, 160)), cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def inspect_image(face_app, pad, img, min_face_width, pad_threshold, min_sharpness):
    """Kiểm tra chất lượng một ảnh. Trả (kết quả, khuôn mặt); KHÔNG kèm vector.

    kết quả: dict {ok, reason, face_width, det_score, sharpness, pad_score} (chỉ {ok, reason} khi chưa có đúng một mặt).
    reason: ok | no_face | multiple_faces | low_quality (mặt nhỏ / detector kém tự tin) | blurry | spoof_suspected
    khuôn mặt: đối tượng insightface (landmark, bbox...) khi có đúng một mặt, ngược lại None. Có cả khi ảnh không đạt
    chất lượng, để bên gọi (quét có hướng dẫn) vẫn đọc được tư thế đầu.
    """
    faces = face_app.get(img)
    if len(faces) == 0:
        return {"ok": False, "reason": "no_face"}, None
    # Chỉ tính người thật sự đứng trong khung: bỏ phát hiện nhỏ / kém tự tin ở nền (ảnh treo tường, hình in, bóng người xa)
    near = [x for x in faces if (x.bbox[2] - x.bbox[0]) >= min_face_width * 0.5 and x.det_score >= MIN_DET_SCORE]
    if len(near) > 1:
        return {"ok": False, "reason": "multiple_faces"}, None
    f = near[0] if near else max(faces, key=lambda x: x.bbox[2] - x.bbox[0])
    width = float(f.bbox[2] - f.bbox[0])
    sharp = sharpness(img, f.bbox)
    base = {"face_width": round(width, 1), "det_score": round(float(f.det_score), 3), "sharpness": round(sharp, 1),
            "pad_score": None}
    if width < min_face_width * 0.75 or f.det_score < MIN_DET_SCORE:
        return {"ok": False, "reason": "low_quality", **base}, f
    if sharp < min_sharpness:
        return {"ok": False, "reason": "blurry", **base}, f
    if pad is not None and pad.enabled:
        score = pad.score(img, f.bbox)
        base["pad_score"] = round(score, 3)
        if score < pad_threshold:
            return {"ok": False, "reason": "spoof_suspected", **base}, f
    return {"ok": True, "reason": "ok", **base}, f


def analyze_image(face_app, pad, img, min_face_width, pad_threshold, min_sharpness):
    """Như inspect_image nhưng khi ảnh đạt thì kèm "embedding" (vector 512 chiều đã chuẩn hóa L2)."""
    res, face = inspect_image(face_app, pad, img, min_face_width, pad_threshold, min_sharpness)
    if res["ok"]:
        emb = np.asarray(face.normed_embedding, dtype=np.float32)
        assert emb.shape == (EMBEDDING_DIM,), emb.shape
        res["embedding"] = [round(float(v), 6) for v in emb]
    return res


def enroll_faces():
    """Chế độ offline: faces_db/<mã>.jpg|png -> embeddings/<mã>.npy (đọc bởi AI khi /trigger không kèm vector)."""
    from insightface.app import FaceAnalysis
    app = FaceAnalysis(name='buffalo_l', allowed_modules=['detection', 'recognition'])
    app.prepare(ctx_id=-1, det_size=(640, 640))

    os.makedirs('embeddings', exist_ok=True)
    print("Đang quét thư mục faces_db...")
    for filename in os.listdir('faces_db'):
        if filename.lower().endswith(('.jpg', '.jpeg', '.png')):
            student_id = filename.split('.')[0]
            img = cv2.imread(os.path.join('faces_db', filename))
            faces = app.get(img) if img is not None else []
            if faces:
                # Lấy khuôn mặt to nhất trong ảnh
                face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
                np.save(f'embeddings/{student_id}.npy', face.normed_embedding)
                print(f"[OK] Đã trích xuất đặc trưng cho: {student_id}")
            else:
                print(f"[LỖI] Không tìm thấy khuôn mặt trong ảnh: {filename}")


if __name__ == '__main__':
    enroll_faces()
