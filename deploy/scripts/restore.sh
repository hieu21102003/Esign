#!/usr/bin/env bash
# Khôi phục bản backup mã hóa vào thư mục đích sạch để kiểm tra/phục hồi.
# Dùng:
#   BACKUP_PASSPHRASE_FILE=/path/to/pass.txt ./deploy/scripts/restore.sh <backup_file> <target_dir>
#   BACKUP_AGE_IDENTITY=/path/to/age-key.txt ./deploy/scripts/restore.sh <backup_file.age> <target_dir>
set -euo pipefail

BACKUP_FILE="${1:?Thiếu đường dẫn file backup}"
TARGET_DIR="${2:?Thiếu thư mục đích}"
[ -f "$BACKUP_FILE" ] || { echo "Không thấy $BACKUP_FILE" >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
TAR_FILE="$WORK/restore.tar.gz"

case "$BACKUP_FILE" in
  *.age)
    [ -n "${BACKUP_AGE_IDENTITY:-}" ] || { echo "Thiếu BACKUP_AGE_IDENTITY" >&2; exit 1; }
    age -d -i "$BACKUP_AGE_IDENTITY" -o "$TAR_FILE" "$BACKUP_FILE"
    ;;
  *.gpg)
    [ -n "${BACKUP_PASSPHRASE_FILE:-}" ] || { echo "Thiếu BACKUP_PASSPHRASE_FILE" >&2; exit 1; }
    gpg --batch --yes --pinentry-mode loopback --passphrase-file "$BACKUP_PASSPHRASE_FILE" \
      -d -o "$TAR_FILE" "$BACKUP_FILE"
    ;;
  *.enc)
    [ -n "${BACKUP_PASSPHRASE_FILE:-}" ] || { echo "Thiếu BACKUP_PASSPHRASE_FILE" >&2; exit 1; }
    openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in "$BACKUP_FILE" -out "$TAR_FILE" \
      -pass "file:$BACKUP_PASSPHRASE_FILE"
    ;;
  *)
    echo "Định dạng backup không hỗ trợ (.age/.gpg/.enc)" >&2
    exit 1
    ;;
esac

mkdir -p "$TARGET_DIR"
tar -C "$TARGET_DIR" -xzf "$TAR_FILE"

# Xác minh không có root.key lọt vào bản khôi phục
if find "$TARGET_DIR" -name "root.key" | grep -q .; then
  echo "CẢNH BÁO: Phát hiện root.key trong bản backup!" >&2
  exit 1
fi
echo "Khôi phục thành công vào $TARGET_DIR"
