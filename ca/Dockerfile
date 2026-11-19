FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LANG=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends bash openssl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "cryptography>=42" \
    && groupadd -g 10002 signer && useradd -u 10005 -g 10002 -d /nonexistent -s /usr/sbin/nologin ca

WORKDIR /app
COPY ca/service.py /app/service.py
COPY pki/ca.sh pki/openssl.cnf /app/pki/
RUN chmod 755 /app/pki/ca.sh

USER 10005:10002
EXPOSE 9001
CMD ["python3", "/app/service.py"]
