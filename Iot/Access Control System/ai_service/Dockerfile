FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 AI_SHOW_WINDOW=0 AI_USE_GPU=0
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libglib2.0-0 libgl1 libxcb1 libsm6 libxext6 libxrender1 && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
# insightface kéo theo opencv-python (bản có giao diện) nên cần các thư viện đồ họa ở trên. Container KHÔNG dùng được webcam USB của Windows: nguồn hình là URL ESP32-CAM.
RUN pip install --no-cache-dir opencv-python-headless insightface onnxruntime numpy requests fastapi uvicorn pydantic
COPY main.py liveness.py enrollment.py ./
COPY models ./models
EXPOSE 5050
# Model buffalo_l (~280 MB) tự tải về /root/.insightface lần đầu: gắn volume để khỏi tải lại
CMD ["python", "main.py"]
