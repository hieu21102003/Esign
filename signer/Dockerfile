FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LANG=C.UTF-8
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && groupadd -g 10002 signer && useradd -u 10002 -g 10002 -d /nonexistent -s /usr/sbin/nologin signer

WORKDIR /app
COPY signer/esign_core.py signer/service.py /app/
COPY signer/fonts /app/fonts
RUN chmod -R a+rX /app

USER 10002:10002
EXPOSE 9000
CMD ["python3", "/app/service.py"]
