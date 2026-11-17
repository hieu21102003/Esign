FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer \
    libreoffice-calc \
    libreoffice-impress \
    libreoffice-common \
    fonts-noto-core \
    fonts-dejavu \
    fonts-liberation \
    fonts-liberation2 \
    fonts-crosextra-carlito \
    fonts-crosextra-caladea \
    fontconfig \
    python3 \
    && rm -rf /var/lib/apt/lists/*

COPY signer/fonts/NotoSans-Regular.ttf /usr/local/share/fonts/NotoSans-Regular.ttf
RUN fc-cache -f -v \
    && groupadd -g 10001 converter \
    && useradd -u 10001 -g 10001 -d /tmp -s /usr/sbin/nologin converter

WORKDIR /app
COPY converter/worker.py converter/server.py /app/

USER 10001:10001
ENTRYPOINT ["python3", "/app/worker.py"]
