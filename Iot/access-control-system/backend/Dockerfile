FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
WORKDIR /srv
COPY requirements.txt .
# bỏ phần "chỉ cần để chạy test" (pytest, httpx) khỏi ảnh chạy thật
RUN sed '/Chỉ cần để chạy test/,$d' requirements.txt > req.run.txt && pip install --no-cache-dir -r req.run.txt
COPY app ./app
COPY scripts ./scripts
EXPOSE 8080
# DB SQLite + ảnh nghi phạm nằm ở /srv/data (gắn volume để không mất khi tạo lại container)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
