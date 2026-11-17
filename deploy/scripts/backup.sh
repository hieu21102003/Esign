#!/usr/bin/env bash
# Backup mã hóa hệ thống esign (DB + issued/*.p12 + int CA + tsa + public + cấu hình).
# KHÔNG bao gồm root/root.key và KHÔNG bao gồm thư mục secrets/.
# Dùng:
#   BACKUP_PASSPHRASE_FILE=/path/to/offline-pass.txt ./deploy/scripts/backup.sh /var/backups/esign
# Hoặc mã hóa khóa công khai bằng age/gpg:
#   BACKUP_AGE_RECIPIENT=age1... ./deploy/scripts/backup.sh /var/backups/esign
set -euo pipefail

HERE="$(cd "$(dirname "$0")/../.." && pwd)"
OUT_DIR="${1:-/var/backups/esign}"
PKI_HOST_DIR="${PKI_HOST_DIR:-$HERE/pki-data}"
DATA_DIR_SRC="${DATA_DIR_SRC:-}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$OUT_DIR"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$WORK/bundle/pki" "$WORK/bundle/config"

# 1. PKI trên server (CHỈ int, issued, tsa, public, server — TUYỆT ĐỐI KHÔNG root/root.key)
for sub in int issued tsa public server; do
  if [ -d "$PKI_HOST_DIR/$sub" ]; then
    cp -a "$PKI_HOST_DIR/$sub" "$WORK/bundle/pki/$sub"
  fi
done
rm -rf "$WORK/bundle/pki/root"

# 2. Cấu hình (KHÔNG copy secrets/ và KHÔNG copy root.key)
for cfg in docker-compose.yml .env; do
  [ -f "$HERE/$cfg" ] && cp -a "$HERE/$cfg" "$WORK/bundle/config/$cfg"
done
[ -f "$HERE/deploy/caddy/Caddyfile" ] && cp -a "$HERE/deploy/caddy/Caddyfile" "$WORK/bundle/config/Caddyfile"

# 3. Cơ sở dữ liệu & file tạm (nếu chạy Postgres trong Compose hoặc SQLite cục bộ)
if [ -n "$DATA_DIR_SRC" ] && [ -d "$DATA_DIR_SRC" ]; then
  mkdir -p "$WORK/bundle/data"
  cp -a "$DATA_DIR_SRC/." "$WORK/bundle/data/"
elif command -v docker >/dev/null 2>&1 && docker compose -f "$HERE/docker-compose.yml" ps -q db 2>/dev/null | grep -q .; then
  docker compose -f "$HERE/docker-compose.yml" exec -T db pg_dump -U "${DB_USER:-esign}" "${DB_NAME:-esign}" > "$WORK/bundle/db.sql"
fi

TAR_FILE="$WORK/esign-backup-$TS.tar.gz"
tar -C "$WORK/bundle" -czf "$TAR_FILE" .

if [ -n "${BACKUP_AGE_RECIPIENT:-}" ] && command -v age >/dev/null 2>&1; then
  OUT_FILE="$OUT_DIR/esign-backup-$TS.tar.gz.age"
  age -r "$BACKUP_AGE_RECIPIENT" -o "$OUT_FILE" "$TAR_FILE"
elif [ -n "${BACKUP_PASSPHRASE_FILE:-}" ] && [ -f "${BACKUP_PASSPHRASE_FILE:-}" ]; then
  if command -v gpg >/dev/null 2>&1; then
    OUT_FILE="$OUT_DIR/esign-backup-$TS.tar.gz.gpg"
    gpg --batch --yes --pinentry-mode loopback --passphrase-file "$BACKUP_PASSPHRASE_FILE" \
      --symmetric --cipher-algo AES256 -o "$OUT_FILE" "$TAR_FILE"
  else
    OUT_FILE="$OUT_DIR/esign-backup-$TS.tar.gz.enc"
    openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -in "$TAR_FILE" -out "$OUT_FILE" \
      -pass "file:$BACKUP_PASSPHRASE_FILE"
  fi
else
  echo "Thiếu BACKUP_AGE_RECIPIENT hoặc BACKUP_PASSPHRASE_FILE (không lưu mật khẩu backup trên server nếu không cần tự động)" >&2
  exit 1
fi

chmod 600 "$OUT_FILE"
echo "$OUT_FILE"
