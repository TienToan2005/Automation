import cv2
import numpy as np
import os
from insightface.app import FaceAnalysis

def enroll_faces():
    # Khởi tạo AI (dùng CPU)
    app = FaceAnalysis(name='buffalo_l')
    app.prepare(ctx_id=-1, det_size=(640, 640))
    
    if not os.path.exists('embeddings'):
        os.makedirs('embeddings')
        
    print("Đang quét thư mục faces_db...")
    for filename in os.listdir('faces_db'):
        if filename.endswith(('.jpg', '.png')):
            student_id = filename.split('.')[0]
            img_path = os.path.join('faces_db', filename)
            
            img = cv2.imread(img_path)
            faces = app.get(img)
            
            if len(faces) > 0:
                # Lấy khuôn mặt to nhất/rõ nhất
                embedding = faces[0].normed_embedding
                np.save(f'embeddings/{student_id}.npy', embedding)
                print(f"[OK] Đã trích xuất đặc trưng cho: {student_id}")
            else:
                print(f"[LỖI] Không tìm thấy khuôn mặt trong ảnh: {filename}")

if __name__ == '__main__':
    enroll_faces()