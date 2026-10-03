import cv2
import numpy as np
import time
import os
import threading
import uvicorn
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from insightface.app import FaceAnalysis

# ================= CẤU HÌNH HỆ THỐNG =================
CAMERA_RTSP = 0 
BACKEND_URL = "http://localhost:8080/api/v1/access/verify"
THINGSBOARD_URL = "http://192.168.1.100:8080/api/v1/DEVICE_TOKEN/telemetry"
CAMERA_ID = "cam_cua_chinh_01"
SIMILARITY_THRESHOLD = 0.50
REQUIRED_FRAMES = 5
# =====================================================

# Biến toàn cục để giao tiếp giữa API và AI Camera
ai_state = {
    "is_active": False,
    "expected_id": None,
    "timeout_at": 0
}

# --- KHỞI TẠO FASTAPI SERVER ---
api_app = FastAPI(title="AI Access Control Service")

# Định nghĩa cấu trúc dữ liệu đầu vào (Pydantic Model)
class TriggerRequest(BaseModel):
    student_id: str

@api_app.post("/api/ai/trigger")
def trigger_scan(request: TriggerRequest):
    """Endpoint nhận lệnh kích hoạt quét khuôn mặt từ Backend"""
    student_id = request.student_id
    
    if not student_id:
        raise HTTPException(status_code=400, detail="Missing student_id")
        
    ai_state["is_active"] = True
    ai_state["expected_id"] = student_id
    ai_state["timeout_at"] = time.time() + 15.0 
    print(f"[API] Đã nhận tín hiệu thẻ từ: {student_id}. Bắt đầu quét khuôn mặt...")
    
    return {"status": "success", "message": "AI started scanning"}

def run_server():
    """Chạy Uvicorn server ở Background Thread"""
    uvicorn.run(api_app, host="0.0.0.0", port=5050, log_level="error")
# ---------------------------------

def get_iso_timestamp():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

def compute_similarity(emb1, emb2):
    return np.dot(emb1, emb2) / (np.linalg.norm(emb1) * np.linalg.norm(emb2))

def load_known_embeddings():
    embeddings = {}
    if os.path.exists('embeddings'):
        for file in os.listdir('embeddings'):
            if file.endswith('.npy'):
                student_id = file.split('.')[0]
                embeddings[student_id] = np.load(os.path.join('embeddings', file))
    return embeddings

def main():
    # Kích hoạt luồng FastAPI Server chạy song song
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    app = FaceAnalysis(name='buffalo_l')
    app.prepare(ctx_id=-1, det_size=(640, 640))
    known_embeddings = load_known_embeddings()
    
    cap = cv2.VideoCapture(CAMERA_RTSP)
    match_count = 0

    print("HỆ THỐNG AI SẴN SÀNG (FastAPI Port 5050)... (Chờ quét thẻ)")

    while True:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.5)
            continue

        if not ai_state["is_active"]:
            cv2.putText(frame, "VUI LONG QUET THE...", (50, 50), 
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)
            cv2.imshow('Lab 2FA System', frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
            continue

        if time.time() > ai_state["timeout_at"]:
            print("!!! [BÁO ĐỘNG] Quá thời gian quy định không nhận diện được mặt hợp lệ.")
            ai_state["is_active"] = False
            match_count = 0
            continue

        faces = app.get(frame)
        expected_id = ai_state["expected_id"]
        
        cv2.putText(frame, f"DANG XAC MINH THE: {expected_id}", (50, 50), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 0), 2)
        
        time_left = int(ai_state["timeout_at"] - time.time())
        cv2.putText(frame, f"Thoi gian: {time_left}s", (50, 90), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

        is_matched = False
        
        for face in faces:
            if expected_id in known_embeddings:
                target_emb = face.normed_embedding
                known_emb = known_embeddings[expected_id]
                
                sim = compute_similarity(target_emb, known_emb)
                x1, y1, x2, y2 = map(int, face.bbox)
                
                if sim >= SIMILARITY_THRESHOLD:
                    is_matched = True
                    match_count += 1
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    cv2.putText(frame, f"Trung khop ({sim:.2f})", (x1, y1-10), 
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                else:
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)
                    cv2.putText(frame, "Sai chu the", (x1, y1-10), 
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

        if is_matched:
            if match_count >= REQUIRED_FRAMES:
                print(f">>> [MỞ CỬA] Xác thực 2 lớp thành công cho: {expected_id}")
                ai_state["is_active"] = False
                match_count = 0
        else:
            match_count = 0

        cv2.imshow('Lab 2FA System', frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()

if __name__ == '__main__':
    main()