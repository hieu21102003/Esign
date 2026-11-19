# Hướng dẫn triển khai & vận hành `esign` (Ubuntu + Docker)

Tài liệu này liệt kê các lệnh để bạn **tự chạy trên server Ubuntu**. Không dùng mật khẩu mặc định; khóa `root.key` chỉ dùng một lần lúc khởi tạo rồi đưa ra lưu trữ offline.

---

## 1. Chuẩn bị server & tường lửa (UFW)
```bash
sudo apt update && sudo apt install -y docker.io docker-compose-plugin openssl gnupg acl ufw
sudo usermod -aG docker "$USER"

# Chỉ mở SSH và HTTPS (443) cho dải mạng nội bộ / VPN (ví dụ 10.0.0.0/8, 192.168.1.0/24)
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from 192.168.1.0/24 to any port 22 proto tcp
sudo ufw allow from 192.168.1.0/24 to any port 443 proto tcp
# (Tùy chọn nếu muốn tự động chuyển hướng HTTP -> HTTPS từ LAN)
sudo ufw allow from 192.168.1.0/24 to any port 80 proto tcp
sudo ufw enable
sudo ufw status verbose
```

---

## 2. Khởi tạo PKI & cấp chứng thư
```bash
cd /opt/esign
mkdir -p pki-data secrets

# Đặt biến môi trường tạm trong phiên shell (KHÔNG lưu file trên server)
export PKI_DIR=/opt/esign/pki-data
export ORG="Tecco1"
# CRL phải dùng http:// để Adobe/Foxit tải được mà không lỗi TLS
export CRL_BASE="http://192.168.1.66/pki"
read -s -p "ROOT_PASS: " ROOT_PASS; echo; export ROOT_PASS
read -s -p "INT_PASS:  " INT_PASS;  echo; export INT_PASS

./pki/ca.sh init
```

**Đưa `root.key` ra khỏi server (bắt buộc):**
```bash
# Chép /opt/esign/pki-data/root/private/root.key ra USB/kho offline, sau đó xóa khỏi server:
shred -u /opt/esign/pki-data/root/private/root.key
unset ROOT_PASS
```
> **Lưu ý:** `root.crl` có hạn **365 ngày**. Chỉ khi cần làm mới `root.crl` (mỗi năm 1 lần) mới tạm đưa `root.key` vào máy offline để ký lại `root.crl`. Mọi thao tác thường ngày (`issue`, `revoke`, `crl-int`) chỉ dùng Intermediate CA.

**Cấp chứng thư HTTPS cho Caddy (dùng profile `v3_server`, KHÔNG dùng chứng thư ký số):**
```bash
# Thay esign.internal và 192.168.1.66 bằng hostname/IP thật của server
./pki/ca.sh server server esign.internal 192.168.1.66

# Caddy chạy với cap_drop: [ALL], cần server.key thuộc root:root quyền 600
sudo chown root:root /opt/esign/pki-data/server/server.key
sudo chmod 600 /opt/esign/pki-data/server/server.key
```

**Cấp chứng thư ký số (.p12) cho người ký:**
```bash
read -s -p "USER_PASS (.p12 passphrase): " USER_PASS; echo; export USER_PASS
./pki/ca.sh issue alice "Nguyễn Văn A" 365
./pki/ca.sh issue bob   "Trần Thị B" 365

# Phân quyền chặt cho các container non-root (`ca` UID 10005:10002, `signer` GID 10002, `tsa` UID 10003)
sudo chown -R 10005:10005 /opt/esign/pki-data/int
sudo chown -R 10005:10002 /opt/esign/pki-data/issued /opt/esign/pki-data/public
sudo chmod 750 /opt/esign/pki-data/issued
sudo chmod 640 /opt/esign/pki-data/issued/*.p12
sudo chown -R 10003:10003 /opt/esign/pki-data/tsa
sudo chmod 600 /opt/esign/pki-data/tsa/tsa.key
```
> Import `/opt/esign/pki-data/public/root.crt` một lần vào máy người dùng (trình duyệt & Adobe/Foxit) để tin cậy cả HTTPS nội bộ lẫn chữ ký số PAdES.

---

## 3. Tạo thư mục `secrets/` (quyền 600, tách riêng khỏi DB & backup)
```bash
cd /opt/esign
umask 077
python3 -c "import secrets; print(secrets.token_urlsafe(48), end='')" > secrets/signer_token
python3 -c "import secrets; print(secrets.token_urlsafe(48), end='')" > secrets/ca_token
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode(), end='')" > secrets/totp_enc_key
python3 -c "import secrets; print(secrets.token_urlsafe(24), end='')" > secrets/db_password
printf '%s' "$USER_PASS" > secrets/p12_passphrase
printf '%s' "$INT_PASS" > secrets/int_passphrase
unset USER_PASS INT_PASS

# Bắt buộc tự đặt mật khẩu admin (>= 10 ký tự, có chữ hoa, chữ thường, chữ số):
read -s -p "ADMIN_PASSWORD: " APW; echo
printf '%s' "$APW" > secrets/admin_password
unset APW

chmod 700 secrets
chmod 400 secrets/*
umask 022  # khôi phục umask mặc định để git pull về sau không tạo file quyền 600

# Phân quyền đúng theo UID từng container (ca=10005, signer=10002, api=10004, postgres=70):
sudo chown 10005:10005 secrets/int_passphrase
sudo chown 10004:10004 secrets/totp_enc_key secrets/admin_password
sudo chown root:root secrets/signer_token secrets/ca_token secrets/p12_passphrase secrets/db_password
sudo setfacl -m u:10002:r secrets/p12_passphrase
sudo setfacl -m u:10005:r secrets/p12_passphrase
sudo setfacl -m u:10002:r,u:10004:r secrets/signer_token
sudo setfacl -m u:10005:r,u:10004:r secrets/ca_token
sudo setfacl -m u:70:r,u:10004:r secrets/db_password

cp .env.example .env
# Khóa Caddy chỉ nghe trên IP LAN/VPN (vì Docker bỏ qua UFW nếu bind 0.0.0.0):
printf 'HTTP_PORT=192.168.1.66:80\nHTTPS_PORT=192.168.1.66:443\n' >> .env
```

---

## 4. Khởi chạy hệ thống
```bash
cd /opt/esign
docker compose up -d --build
docker compose ps
```
Kiểm tra: chỉ `caddy` mở cổng `80` và `443`; `db`, `api`, `web`, `signer`, `converter`, `tsa` chỉ chạy trong mạng nội bộ.

---

## 5. Vận hành định kỳ (CRL, Cảnh báo hết hạn, Backup mã hóa)

### Làm mới Intermediate CRL tự động (mỗi 7 ngày, không cần `root.key`)
```bash
# Lưu INT_PASS vào file bảo vệ riêng (quyền 600, root đọc) nếu chạy cron tự động:
# Ví dụ /etc/esign/int_pass (chmod 600)
0 3 * * 0 PKI_DIR=/opt/esign/pki-data INT_PASS_FILE=/etc/esign/int_pass /opt/esign/deploy/scripts/renew_crl.sh >> /var/log/esign-crl.log 2>&1
```
> Nếu `int.crl` hoặc `root.crl` quá hạn, service `signer` sẽ tự động **từ chối ký**. Trang **Quản trị** và log backend (`docker compose logs api`) sẽ cảnh báo khi chứng thư người ký, chứng thư TSA, Intermediate CA hoặc CRL còn `<= 30` ngày.

### Backup mã hóa & kiểm tra khôi phục
```bash
# Khóa/passphrase mã hóa backup PHẢI lưu tách khỏi server (VD: password manager / kho offline)
BACKUP_PASSPHRASE_FILE=/root/backup-pass.txt /opt/esign/deploy/scripts/backup.sh /var/backups/esign

# Test khôi phục vào thư mục sạch:
BACKUP_PASSPHRASE_FILE=/root/backup-pass.txt /opt/esign/deploy/scripts/restore.sh \
  /var/backups/esign/esign-backup-<TIMESTAMP>.tar.gz.gpg /tmp/restore-verify
```
> Bản backup chứa DB, `issued/*.p12`, `int/`, `tsa/`, `public/`, cấu hình — **không** chứa `root.key` và **không** chứa `secrets/`.
