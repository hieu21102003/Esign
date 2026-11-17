FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LANG=C.UTF-8
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && groupadd -g 10004 api && useradd -u 10004 -g 10004 -d /nonexistent -s /usr/sbin/nologin api \
    && mkdir -p /data && chown 10004:10004 /data && chmod 700 /data

WORKDIR /app
COPY backend/app /app/app
# Dùng lại bộ kiểm tra Office của converter (chỉ thư viện chuẩn) để từ chối file độc hại trước khi gửi đi
COPY converter/worker.py /app/vendor/worker.py

ENV DATA_DIR=/data
USER 10004:10004
EXPOSE 8000
# 1 worker: rate limit giữ trong bộ nhớ
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-server-header", "--workers", "1"]
