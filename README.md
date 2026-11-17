# esign — ký số PDF nội bộ (M1 + M2)

CA tự dựng (Root + Intermediate) + TSA + công cụ ký/xác minh PDF chuẩn **PAdES**.
Ký 3 kiểu hiển thị (chữ / ảnh / ảnh+chữ), có **nhận xét (Reason)** nằm trong chữ ký, ký nối tiếp nhiều người.

## Cài đặt (Ubuntu)
    sudo apt install -y openssl poppler-utils
    pip install -r requirements.txt

## 1. Dựng CA (một lần)
    cd pki
    export PKI_DIR=/srv/esign/pki ROOT_PASS='...' INT_PASS='...' ORG="Tên công ty"
    ./ca.sh init                                  # Root 20 năm, Intermediate 10 năm, TSA, CRL
    USER_PASS='...' ./ca.sh issue alice "Nguyễn Văn A" 365   # -> $PKI_DIR/issued/alice.p12
    ./ca.sh revoke alice                          # thu hồi + cập nhật CRL
    ./ca.sh crl                                   # làm mới CRL (nên chạy cron mỗi 7-14 ngày)
**Sau `init`:** chép `root/private/root.key` ra USB/offline rồi xóa khỏi server.
**Người nhận** import `public/root.crt` (Root CA) vào máy một lần thì Adobe/Foxit mới báo "tin cậy".

TSA (dấu thời gian RFC 3161), chỉ nghe localhost:

    PKI_DIR=/srv/esign/pki python3 pki/tsa_server.py 8318

## 2. Ký PDF
    export USER_PASS='...'
    python3 signer/sign.py in.pdf out.pdf --p12 $PKI_DIR/issued/alice.p12 --tsa http://127.0.0.1:8318 \
        --mode both --image sig.png --page -1 --box 40,40,330,120 \
        --reason "Đã duyệt nội dung" --show name,date,reason

| Tham số | Ý nghĩa |
|---|---|
| `--mode text\|image\|both` | chỉ chữ / chỉ ảnh / ảnh + chữ |
| `--reason` | **nhận xét**, được ký bảo vệ, không sửa được sau khi ký |
| `--show` | trường hiện trong khối chữ: `name,title,date,location,reason` |
| `--title`, `--location` | chức danh, nơi ký |
| `--page` | 1-based; `-1` = trang cuối |
| `--box x1,y1,x2,y2` | vị trí (pt, gốc dưới-trái, A4 = 595x842) |
| `--level B-B\|B-T\|B-LT` | không timestamp / có timestamp (mặc định) / nhúng sẵn thông tin thu hồi |

Ký tiếp lên file đã ký: dùng file đó làm `input` (incremental update, không phá chữ ký trước).

## 3. Xác minh
    python3 signer/verify.py out.pdf --root $PKI_DIR/public/root.crt --chain $PKI_DIR/public/chain.pem \
        --crl $PKI_DIR/public/int.crl --crl $PKI_DIR/public/root.crl
Trả JSON từng chữ ký; exit code 0 chỉ khi **tất cả** đều `ok` (nguyên vẹn + hợp lệ + tin cậy + chưa thu hồi).

## 4. Chuyển đổi Word/Office -> PDF (M3)
Build container LibreOffice headless sandbox (chỉ định kỳ khi cập nhật):
    docker build -f converter/Dockerfile -t esign-converter:latest .

Chuyển đổi `.docx`, `.doc`, `.xlsx`, `.pptx` sang PDF trong container không có mạng (`--network none`), giới hạn CPU/RAM và timeout 60 giây:
    python3 converter/convert.py in.docx out.pdf --timeout 60

## 5. Nghiệm thu
    ./run_tests.sh      # 25 kiểm tra (M1+M2+M3): PKI, 3 chế độ, nhận xét, timestamp, ký nối tiếp, sửa 1 byte, thu hồi, pdfsig, converter sandbox & bảo mật

## Lưu ý quan trọng
- **Font:** dùng font **1000 đơn vị/em** (mặc định Noto Sans đi kèm trong `signer/fonts/`). Với pyHanko 0.38, font
  2048 đơn vị/em (DejaVu, Liberation, Arial) bị giãn chữ gần gấp đôi trong khối chữ ký.
- Khóa TSA không mã hóa (dịch vụ chạy tự động); quyền 600, chỉ user chạy TSA đọc được.
- PDF có mật khẩu bị từ chối. Ký xong mà sửa file → chữ ký báo "đã bị thay đổi".
- `test-data/` chỉ chứa dữ liệu demo (mật khẩu `rootpw/intpw/userpw`), không dùng cho thật.
