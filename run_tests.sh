#!/usr/bin/env bash
# Bộ nghiệm thu M1+M2. Chạy: ./run_tests.sh   (cần: openssl, python3 + pyhanko, poppler-utils)
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
export PKI_DIR="$HERE/test-data" ROOT_PASS=rootpw INT_PASS=intpw USER_PASS=userpw ORG="Demo Org"
export TOTP_ENC_KEY="${TOTP_ENC_KEY:-$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')}"
D=$PKI_DIR; P=$D/public; T=http://127.0.0.1:8318
PASS=0; FAIL=0
ok()   { echo "  [PASS] $1"; PASS=$((PASS+1)); }
bad()  { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }
chk()  { if eval "$2" >/dev/null 2>&1; then ok "$1"; else bad "$1"; fi; }
VA="--root $P/root.crt --chain $P/chain.pem --crl $P/int.crl --crl $P/root.crl"
sign() { python3 "$HERE/signer/sign.py" "$@" 2>&1 | grep -v FFTM; }
ver()  { python3 "$HERE/signer/verify.py" "$@" $VA; }

echo "== Chuẩn bị: PKI sạch + TSA =="
[ -f "$D/sample.pdf" ] || { echo "thiếu $D/sample.pdf"; exit 1; }
rm -rf "$D"/{root,int} "$D"/issued/* "$D"/tsa/* "$D"/public/* "$D"/*_out.pdf
( cd "$HERE/pki" && ./ca.sh init && ./ca.sh issue alice "Nguyễn Văn A" 365 && ./ca.sh issue bob "Trần Thị B" 365 ) >/dev/null 2>&1
( cd "$HERE/pki" && setsid nohup python3 tsa_server.py 8318 >/tmp/tsa.log 2>&1 </dev/null & ); sleep 1.5

echo "== M1: PKI =="
chk "Chuỗi Root->Intermediate->alice hợp lệ" "openssl verify -CAfile $P/root.crt -untrusted $P/int.crt $D/issued/alice.crt"
chk "Tên tiếng Việt trong chứng thư (UTF8)" "openssl x509 -in $D/issued/alice.crt -noout -subject -nameopt utf8,-esc_msb | grep -q 'Nguyễn Văn A'"
chk "Chứng thư có nonRepudiation" "openssl x509 -in $D/issued/alice.crt -noout -text | grep -q 'Non Repudiation'"
chk "File CRL tồn tại" "test -s $P/int.crl"

echo "== M2: ký 3 chế độ =="
sign $D/sample.pdf $D/text_out.pdf  --p12 $D/issued/bob.p12   --tsa $T --mode text  --page 1  --box 300,40,560,110 --reason "Cần bổ sung phụ lục" --title "Kế toán trưởng" --show name,title,date,reason >/dev/null
sign $D/sample.pdf $D/image_out.pdf --p12 $D/issued/alice.p12 --tsa $T --mode image --image $D/sig.png --page 2 --box 350,60,550,140 >/dev/null
sign $D/sample.pdf $D/both_out.pdf  --p12 $D/issued/alice.p12 --tsa $T --mode both  --image $D/sig.png --page -1 --box 40,40,330,120 --reason "Đã duyệt nội dung, đồng ý ban hành" >/dev/null
for m in text image both; do chk "Chế độ $m: ký + verify hợp lệ" "ver $D/${m}_out.pdf"; done
chk "Nhận xét (Reason) nằm trong chữ ký" "ver $D/both_out.pdf | grep -q 'Đã duyệt nội dung'"
chk "Có timestamp (B-T)" "ver $D/both_out.pdf | grep -q TIMESTAMP_TOKEN"
chk "Chữ ký hiển thị tiếng Việt (pdftotext)" "pdftotext -f 3 -l 3 $D/both_out.pdf - | grep -q 'Nguyễn Văn A'"

echo "== Ký nối tiếp (2 người) =="
sign $D/both_out.pdf $D/multi_out.pdf --p12 $D/issued/bob.p12 --tsa $T --mode text --page -1 --box 340,130,560,200 --reason "Đồng ý, ký duyệt" >/dev/null
chk "2 chữ ký đều hợp lệ" "ver $D/multi_out.pdf && [ \$(ver $D/multi_out.pdf | grep -c '\"field\"') -eq 2 ]"

echo "== Chống sửa đổi (tamper) =="
python3 - <<PY
b=bytearray(open("$D/both_out.pdf","rb").read()); b[400]^=1; open("$D/tamper_out.pdf","wb").write(b)
PY
chk "Sửa 1 byte -> verify phải FAIL" "! ver $D/tamper_out.pdf"
chk "Báo rõ intact=false" "ver $D/tamper_out.pdf | grep -q '\"intact\": false'"

echo "== Thu hồi chứng thư =="
( cd "$HERE/pki" && ./ca.sh revoke alice ) >/dev/null 2>&1
chk "Sau khi thu hồi alice -> verify FAIL" "! ver $D/both_out.pdf"
chk "Chữ ký của bob (chưa thu hồi) vẫn hợp lệ" "ver $D/text_out.pdf"

echo "== Kiểm tra chéo bằng pdfsig (poppler) =="
chk "pdfsig: chữ ký hợp lệ (digest khớp)" "pdfsig -nocert $D/text_out.pdf | grep -q 'Signature is Valid'"
chk "pdfsig: file bị sửa -> Digest Mismatch" "pdfsig -nocert $D/tamper_out.pdf | grep -q 'Digest Mismatch'"

echo "== M3: Word/Office -> PDF (LibreOffice headless sandbox) =="
rm -f "$D/docx_signed.pdf" "$D/sample.docx" "$D/sample.xlsx" "$D/sample.pptx"
if [ -z "${CONVERTER_URL:-}" ]; then
  rm -f "$D/sample.doc"
  docker image inspect esign-converter:latest >/dev/null 2>&1 || docker build -f "$HERE/converter/Dockerfile" -t esign-converter:latest "$HERE" >/dev/null
fi
python3 "$HERE/converter/make_samples.py" "$D" >/dev/null
if [ -n "${CONVERTER_URL:-}" ]; then
  chk "M3: Container converter cô lập mạng (--network none)" "python3 -c \"import json,urllib.request; assert not json.loads(urllib.request.urlopen('$CONVERTER_URL/netcheck').read())['egress']\""
else
  chk "M3: Container converter cô lập mạng (--network none)" "! docker run --rm --network none --entrypoint python3 esign-converter:latest -c 'import socket; s=socket.socket(); s.settimeout(2); s.connect((\"8.8.8.8\", 53))'"
fi
chk "M3: Chuyển đổi .docx (tiếng Việt + bảng + ảnh) -> PDF" "python3 $HERE/converter/convert.py $D/sample.docx $D/docx_out.pdf"
chk "M3: PDF từ .docx hiển thị đúng tiếng Việt (pdftotext)" "pdftotext $D/docx_out.pdf - | grep -q 'CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM' && pdftotext $D/docx_out.pdf - | grep -q 'Nguyễn Văn A'"
chk "M3: PDF từ .docx hiển thị đúng nội dung bảng (pdftotext)" "pdftotext $D/docx_out.pdf - | grep -q 'Chuyển đổi tài liệu Word sang PDF' && pdftotext $D/docx_out.pdf - | grep -q 'Trần Thị B'"
chk "M3: PDF từ .docx giữ nguyên ảnh nhúng (pdfimages)" "[ \$(pdfimages -list $D/docx_out.pdf | wc -l) -ge 3 ]"
chk "M3: Chuyển đổi .doc, .xlsx, .pptx -> PDF" "python3 $HERE/converter/convert.py $D/sample.doc $D/doc_out.pdf && python3 $HERE/converter/convert.py $D/sample.xlsx $D/xlsx_out.pdf && python3 $HERE/converter/convert.py $D/sample.pptx $D/pptx_out.pdf"
chk "M3: Chặn path traversal, XXE, external link, sai magic bytes, macro" "python3 $HERE/converter/test_security.py"
chk "M3: End-to-end .docx -> PDF -> ký số -> verify (pyHanko & pdfsig)" "sign $D/docx_out.pdf $D/docx_signed.pdf --p12 $D/issued/bob.p12 --tsa $T --mode both --image $D/sig.png --page 1 --box 300,40,550,120 --reason 'Đã duyệt tờ trình từ Word' >/dev/null && ver $D/docx_signed.pdf && pdfsig -nocert $D/docx_signed.pdf | grep -q 'Signature is Valid'"

if [ -n "${SIGNER_URL:-}" ] && [ -n "${CONVERTER_URL:-}" ]; then
  echo "== M4 + M5 + M6: Backend, Frontend Playwright, Caddy HTTPS, CRL & Backup =="
  ( cd "$HERE/pki" && ./ca.sh issue carol "Lê Văn C" 365 && ./ca.sh issue dave "Phạm Thị D" 365 && ./ca.sh issue erin "Hoàng Văn E" 365 && ./ca.sh issue frank "Đặng Thị F" 365 && ./ca.sh server test_srv caddy localhost 127.0.0.1 && ROOT_PASS="" ./ca.sh crl-int ) >/dev/null 2>&1
  chown 10002:10002 "$D"/issued/*.p12 2>/dev/null || true
  chown -R 10003:10003 "$D/tsa" 2>/dev/null || true
  if pytest -q "$HERE/backend/tests" --tb=short >/tmp/pytest_m46.log 2>&1; then
    ok "M4+M5+M6: Bộ test API, Playwright E2E, Tọa độ <3pt, Caddy, CRL, Backup (pytest)"
  else
    cat /tmp/pytest_m46.log
    bad "M4+M5+M6: Bộ test API, Playwright E2E, Tọa độ <3pt, Caddy, CRL, Backup (pytest)"
  fi
fi

echo; echo "KẾT QUẢ: $PASS pass, $FAIL fail"; exit $FAIL
