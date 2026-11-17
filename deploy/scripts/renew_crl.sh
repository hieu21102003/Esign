#!/usr/bin/env bash
# Làm mới Intermediate CRL định kỳ (KHÔNG cần root.key hay ROOT_PASS).
# Nên chạy bằng cron mỗi 7 ngày: 0 3 * * 0 INT_PASS_FILE=/etc/esign/int_pass /opt/esign/deploy/scripts/renew_crl.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")/../.." && pwd)"
export PKI_DIR="${PKI_DIR:-$HERE/pki-data}"
if [ -n "${INT_PASS_FILE:-}" ] && [ -f "$INT_PASS_FILE" ]; then
  export INT_PASS
  INT_PASS="$(tr -d '\r\n' < "$INT_PASS_FILE")"
fi
"$HERE/pki/ca.sh" crl-int
echo "Đã làm mới $PKI_DIR/public/int.crl"
