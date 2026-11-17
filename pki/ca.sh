#!/usr/bin/env bash
# CA nội bộ bằng OpenSSL. Dùng:
#   ./ca.sh init
#   ./ca.sh issue <name> "<Tên hiển thị>" [số_ngày]
#   ./ca.sh server <name> <DNS_hoặc_IP>...
#   ./ca.sh revoke <name>
#   ./ca.sh crl
#   ./ca.sh crl-int
# Biến môi trường:
#   PKI_DIR      thư mục dữ liệu (mặc định ./data)
#   ROOT_PASS    passphrase khóa Root     (bắt buộc cho init)
#   INT_PASS     passphrase khóa Intermediate (bắt buộc)
#   USER_PASS    passphrase file .p12 của người ký (bắt buộc cho issue)
#   CRL_BASE     URL gốc chứa CRL (mặc định http://localhost:8080/pki)
#   ORG          tên tổ chức (mặc định "Internal")
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export PKI_DIR="${PKI_DIR:-$HERE/data}"
export CRL_BASE="${CRL_BASE:-http://localhost:8080/pki}"
ORG="${ORG:-Internal}"
CNF="$HERE/openssl.cnf"
need() { [ -n "${!1:-}" ] || { echo "Thiếu biến môi trường $1" >&2; exit 1; }; }

cmd_init() {
  need ROOT_PASS; need INT_PASS
  [ -e "$PKI_DIR/root/root.crt" ] && { echo "Đã init rồi ($PKI_DIR)"; exit 1; }
  for d in root int; do
    mkdir -p "$PKI_DIR/$d"/{private,newcerts}; chmod 700 "$PKI_DIR/$d/private"
    : > "$PKI_DIR/$d/index.txt"; echo 1000 > "$PKI_DIR/$d/serial"; echo 1000 > "$PKI_DIR/$d/crlnumber"
  done
  mkdir -p "$PKI_DIR"/{issued,tsa,public}

  # Root CA (20 năm) - nên chép khóa này ra nơi offline sau khi init
  openssl genrsa -aes256 -passout env:ROOT_PASS -out "$PKI_DIR/root/private/root.key" 4096 2>/dev/null
  openssl req -new -x509 -sha256 -days 7300 -key "$PKI_DIR/root/private/root.key" -passin env:ROOT_PASS \
    -subj "/O=$ORG/CN=$ORG Root CA" -config "$CNF" -extensions v3_root -out "$PKI_DIR/root/root.crt"

  # Intermediate CA (10 năm)
  openssl genrsa -aes256 -passout env:INT_PASS -out "$PKI_DIR/int/private/int.key" 4096 2>/dev/null
  openssl req -new -sha256 -key "$PKI_DIR/int/private/int.key" -passin env:INT_PASS \
    -subj "/O=$ORG/CN=$ORG Intermediate CA" -config "$CNF" -out "$PKI_DIR/int/int.csr"
  openssl ca -batch -notext -config "$CNF" -name ca_root -passin env:ROOT_PASS \
    -extensions v3_intermediate -days 3650 -in "$PKI_DIR/int/int.csr" -out "$PKI_DIR/int/int.crt"

  cat "$PKI_DIR/int/int.crt" "$PKI_DIR/root/root.crt" > "$PKI_DIR/public/chain.pem"
  cp "$PKI_DIR/root/root.crt" "$PKI_DIR/public/root.crt"
  cp "$PKI_DIR/int/int.crt" "$PKI_DIR/public/int.crt"

  # TSA: khóa dịch vụ không mã hóa (service chạy tự động), quyền 600
  openssl genrsa -out "$PKI_DIR/tsa/tsa.key" 3072 2>/dev/null; chmod 600 "$PKI_DIR/tsa/tsa.key"
  openssl req -new -sha256 -key "$PKI_DIR/tsa/tsa.key" -subj "/O=$ORG/CN=$ORG Timestamp Authority" \
    -config "$CNF" -out "$PKI_DIR/tsa/tsa.csr"
  openssl ca -batch -notext -config "$CNF" -name ca_int -passin env:INT_PASS \
    -extensions v3_tsa -days 1825 -in "$PKI_DIR/tsa/tsa.csr" -out "$PKI_DIR/tsa/tsa.crt"
  cat "$PKI_DIR/int/int.crt" "$PKI_DIR/root/root.crt" > "$PKI_DIR/tsa/tsa-chain.pem"
  echo 01 > "$PKI_DIR/tsa/tsaserial"
  cmd_crl
  echo "OK. Root CA để import vào máy người nhận: $PKI_DIR/public/root.crt"
}

cmd_crl_int() {
  need INT_PASS
  openssl ca -config "$CNF" -name ca_int -passin env:INT_PASS -gencrl -out "$PKI_DIR/public/int.crl"
  openssl crl -in "$PKI_DIR/public/int.crl" -outform DER -out "$PKI_DIR/public/int.crl.der"
}

cmd_crl() {
  need ROOT_PASS; need INT_PASS
  openssl ca -config "$CNF" -name ca_root -passin env:ROOT_PASS -gencrl -out "$PKI_DIR/public/root.crl"
  openssl crl -in "$PKI_DIR/public/root.crl" -outform DER -out "$PKI_DIR/public/root.crl.der"
  cmd_crl_int
}

cmd_issue() {
  need INT_PASS; need USER_PASS
  local name="$1" cn="$2" days="${3:-365}"
  [[ "$name" =~ ^[a-zA-Z0-9_-]+$ ]] || { echo "name chỉ gồm a-z 0-9 _ -" >&2; exit 1; }
  local out="$PKI_DIR/issued/$name"
  [ -e "$out.crt" ] && { echo "Chứng thư '$name' đã tồn tại" >&2; exit 1; }
  openssl genrsa -out "$out.key" 3072 2>/dev/null; chmod 600 "$out.key"
  openssl req -new -utf8 -sha256 -key "$out.key" -subj "/O=$ORG/CN=$cn" -config "$CNF" -out "$out.csr"
  openssl ca -batch -notext -config "$CNF" -name ca_int -passin env:INT_PASS \
    -extensions v3_signer -days "$days" -in "$out.csr" -out "$out.crt"
  openssl pkcs12 -export -inkey "$out.key" -in "$out.crt" -certfile "$PKI_DIR/public/chain.pem" \
    -name "$cn" -passout env:USER_PASS -out "$out.p12"
  shred -u "$out.key" 2>/dev/null || rm -f "$out.key"   # khóa rời chỉ còn trong .p12 đã mã hóa
  rm -f "$out.csr"
  echo "Đã cấp: $out.p12 (hết hạn sau $days ngày)"
}

cmd_server() {
  need INT_PASS
  local name="$1"
  shift
  [[ "$name" =~ ^[a-zA-Z0-9_-]+$ ]] || { echo "name chỉ gồm a-z 0-9 _ -" >&2; exit 1; }
  [ "$#" -ge 1 ] || { echo "Cần ít nhất 1 DNS hoặc IP cho SAN" >&2; exit 1; }
  mkdir -p "$PKI_DIR/server"
  local out="$PKI_DIR/server/$name" san="" item
  for item in "$@"; do
    if [[ "$item" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ || "$item" == *:* ]]; then
      san="${san:+$san,}IP:$item"
    else
      san="${san:+$san,}DNS:$item"
    fi
  done
  local extf
  extf="$(mktemp)"
  cat "$CNF" > "$extf"
  printf "\n[v3_server_san]\nbasicConstraints=critical,CA:false\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\nsubjectAltName=%s\n" "$san" >> "$extf"
  openssl genrsa -out "$out.key" 3072 2>/dev/null; chmod 600 "$out.key"
  openssl req -new -sha256 -key "$out.key" -subj "/O=$ORG/CN=$1" -config "$CNF" -out "$out.csr"
  openssl ca -batch -notext -config "$extf" -name ca_int -passin env:INT_PASS \
    -extensions v3_server_san -days 365 -in "$out.csr" -out "$out.leaf.crt"
  cat "$out.leaf.crt" "$PKI_DIR/public/int.crt" > "$out.crt"
  rm -f "$out.csr" "$out.leaf.crt" "$extf"
  echo "Đã cấp TLS server: $out.crt / $out.key (SAN: $san)"
}

cmd_revoke() {
  need INT_PASS
  local crt="$PKI_DIR/issued/$1.crt"; [ -e "$crt" ] || { echo "Không thấy $1" >&2; exit 1; }
  openssl ca -config "$CNF" -name ca_int -passin env:INT_PASS -revoke "$crt" -crl_reason keyCompromise
  cmd_crl_int
}

case "${1:-}" in
  init)    cmd_init ;;
  issue)   shift; cmd_issue "$@" ;;
  server)  shift; cmd_server "$@" ;;
  revoke)  shift; cmd_revoke "$@" ;;
  crl)     cmd_crl ;;
  crl-int) cmd_crl_int ;;
  *) sed -n '2,15p' "$0"; exit 1 ;;
esac
