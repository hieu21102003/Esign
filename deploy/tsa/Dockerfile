FROM debian:bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LANG=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends openssl python3 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 10003 tsa && useradd -u 10003 -g 10003 -d /nonexistent -s /usr/sbin/nologin tsa

WORKDIR /app/pki
# Dùng nguyên pki/tsa_server.py (không sửa): chỉ bọc để nghe 0.0.0.0 trong mạng nội bộ Compose
COPY pki/tsa_server.py pki/openssl.cnf /app/pki/
COPY deploy/tsa/run_tsa.py /app/run_tsa.py

USER 10003:10003
EXPOSE 8318
CMD ["python3", "/app/run_tsa.py"]
