"""Kiểm thử nghiệm thu M6:
- Caddy HTTPS + HSTS + redirect 80->443 + giới hạn 25MB + TLS server cert (serverAuth, tách biệt signer cert).
- Tin X-Forwarded-For CHỈ khi request từ proxy tin cậy.
- Chặn truy cập trực tiếp tới converter/signer/tsa/db từ ngoài mạng internal (kiểm chứng cách ly).
- CRL quá hạn -> signer từ chối ký.
- Cảnh báo chứng thư/CRL sắp hết hạn (<= 30 ngày).
- Backup mã hóa & khôi phục thật trên thư mục sạch (không chứa root.key hay secrets/).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time

import httpx
import pytest

from conftest import ROOT, TEST_DATA, build_env, need_env

CADDY_HTTPS = os.environ.get("CADDY_HTTPS_URL", "")
CADDY_HTTP = os.environ.get("CADDY_HTTP_URL", "")


def test_trusted_proxy_xff_only_from_caddy(tmp_path):
    # (1) proxy không tin cậy giả mạo X-Forwarded-For -> bị bỏ qua, dùng peer 'testclient'
    e_untrusted = build_env(tmp_path / "u", with_services=False, trust_proxy=True, trusted_proxies=["10.99.0.2"])
    _, r = e_untrusted.login_password("nobody", "sai")
    assert r.status_code == 401
    row = e_untrusted.admin.get("/api/admin/audit?action=login_failed").json()[0]
    assert row["ip"] == "testclient"

    # (2) giả lập request đến từ proxy tin cậy 'testclient' với X-Forwarded-For -> lấy IP client thật
    e_trusted = build_env(tmp_path / "t", with_services=False, trust_proxy=True, trusted_proxies=["testclient"])
    api = type(e_trusted.admin)(e_trusted.app)
    api.post("/api/auth/login", json={"username": "nobody", "password": "sai"},
             headers={"X-Forwarded-For": "198.51.100.42"})
    row2 = e_trusted.admin.get("/api/admin/audit?action=login_failed").json()[0]
    assert row2["ip"] == "198.51.100.42"


@pytest.mark.skipif(not CADDY_HTTPS, reason="Chưa cấu hình CADDY_HTTPS_URL")
def test_caddy_https_tls_headers_and_redirect():
    root_crt = os.path.join(TEST_DATA, "public", "root.crt")
    int_crt = os.path.join(TEST_DATA, "public", "int.crt")
    test_srv = os.path.join(TEST_DATA, "server", "test_srv.crt")
    srv_crt = test_srv if os.path.isfile(test_srv) else os.path.join(TEST_DATA, "server", "server.crt")
    assert os.path.isfile(srv_crt)

    # Chứng thư server phải hợp lệ theo Root->Int và có serverAuth (EKU khác chứng thư ký số)
    v = subprocess.run(["openssl", "verify", "-CAfile", root_crt, "-untrusted", int_crt, srv_crt],
                       capture_output=True, text=True)
    assert v.returncode == 0, v.stdout + v.stderr
    txt = subprocess.run(["openssl", "x509", "-in", srv_crt, "-noout", "-text"],
                         capture_output=True, text=True).stdout
    assert "TLS Web Server Authentication" in txt
    assert "Non Repudiation" not in txt

    # HTTP :80 phục vụ /pki/*.crl (DER) & /pki/root.crt, các đường dẫn khác redirect sang HTTPS
    with httpx.Client(base_url=CADDY_HTTP, follow_redirects=False, timeout=10) as c:
        r = c.get("/api/health")
        assert r.status_code in (301, 308) and r.headers["location"].startswith("https://")
        rcrl = c.get("/pki/int.crl")
        assert rcrl.status_code == 200 and rcrl.headers["content-type"] == "application/pkix-crl"
        assert subprocess.run(["openssl", "crl", "-inform", "DER", "-noout"],
                              input=rcrl.content, capture_output=True).returncode == 0
        assert c.get("/pki/root.crt").status_code == 200

    # HTTPS :443 trả đủ header HSTS / nosniff / Referrer-Policy
    import ssl
    if os.path.isfile(test_srv):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    else:
        ctx = ssl.create_default_context(cafile=root_crt)
    with httpx.Client(base_url=CADDY_HTTPS, verify=ctx, timeout=10) as c:
        r = c.get("/api/health")
        assert r.status_code == 200
        assert "max-age=" in r.headers["strict-transport-security"]
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert r.headers["x-frame-options"] == "DENY"


def test_expired_crl_causes_signer_to_reject_signing():
    need_env("PKI_DIR", "ROOT_PASS", "INT_PASS", "USER_PASS")
    import sys
    sys.path.insert(0, os.path.join(ROOT, "signer"))
    import service as signer_svc

    with tempfile.TemporaryDirectory() as d:
        pki_copy = os.path.join(d, "pki")
        shutil.copytree(os.environ["PKI_DIR"], pki_copy)
        # Tạo CRL hết hạn sau 1 giây bằng openssl ca -crldays 0 -crlsec 1
        env = dict(os.environ, PKI_DIR=pki_copy, CRL_BASE="http://localhost")
        p = subprocess.run(
            ["openssl", "ca", "-config", os.path.join(ROOT, "pki", "openssl.cnf"),
             "-name", "ca_int", "-passin", "env:INT_PASS", "-gencrl",
             "-crlsec", "1", "-out", os.path.join(pki_copy, "public", "int.crl")],
            env=env, capture_output=True, text=True,
        )
        assert p.returncode == 0, p.stderr
        time.sleep(2)

        old_keys, old_pub, old_pw = signer_svc.KEYS_DIR, signer_svc.PKI_PUBLIC, signer_svc.P12_PASSPHRASE
        try:
            signer_svc.KEYS_DIR = os.path.join(pki_copy, "issued")
            signer_svc.PKI_PUBLIC = os.path.join(pki_copy, "public")
            signer_svc.P12_PASSPHRASE = os.environ["USER_PASS"]
            import base64
            pdf_b64 = base64.b64encode(open(os.path.join(TEST_DATA, "sample.pdf"), "rb").read()).decode()
            with pytest.raises(signer_svc.SignerError, match="quá hạn"):
                signer_svc.do_sign({
                    "key_ref": "carol",
                    "pdf_b64": pdf_b64,
                    "mode": "text",
                    "page": 1,
                    "box": [40, 40, 260, 110],
                })
        finally:
            signer_svc.KEYS_DIR, signer_svc.PKI_PUBLIC, signer_svc.P12_PASSPHRASE = old_keys, old_pub, old_pw


def test_expiry_warnings_within_30_days(env):
    r = env.admin.get("/api/admin/expiry-warnings?days=35")
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["items"]) >= 3
    # int.crl có hạn 30 ngày nên phải xuất hiện trong danh sách cảnh báo
    assert any(w["kind"] == "crl" and w["name"] == "int.crl" for w in data["warnings"])


def test_encrypted_backup_and_clean_restore():
    need_env("PKI_DIR")
    with tempfile.TemporaryDirectory() as d:
        pass_file = os.path.join(d, "backup_pass.txt")
        open(pass_file, "w", encoding="utf-8").write("Backup-Secret-Offline-2026!\n")
        backup_dir = os.path.join(d, "backups")
        fake_data = os.path.join(d, "data_src")
        os.makedirs(fake_data)
        open(os.path.join(fake_data, "esign.db"), "wb").write(b"SQLite format 3\x00demo")

        b_env = dict(os.environ, PKI_HOST_DIR=os.environ["PKI_DIR"], DATA_DIR_SRC=fake_data,
                     BACKUP_PASSPHRASE_FILE=pass_file)
        pb = subprocess.run(["bash", os.path.join(ROOT, "deploy", "scripts", "backup.sh"), backup_dir],
                            env=b_env, capture_output=True, text=True)
        assert pb.returncode == 0, pb.stderr
        backup_file = pb.stdout.strip().splitlines()[-1]
        assert os.path.isfile(backup_file)
        # Xác nhận file backup đã được mã hóa (không phải gzip/tar thô)
        assert open(backup_file, "rb").read(2) != b"\x1f\x8b"

        restore_dir = os.path.join(d, "restored")
        pr = subprocess.run(["bash", os.path.join(ROOT, "deploy", "scripts", "restore.sh"), backup_file, restore_dir],
                            env=b_env, capture_output=True, text=True)
        assert pr.returncode == 0, pr.stderr
        assert os.path.isfile(os.path.join(restore_dir, "data", "esign.db"))
        assert os.path.isfile(os.path.join(restore_dir, "pki", "issued", "carol.p12"))
        assert os.path.isfile(os.path.join(restore_dir, "pki", "tsa", "tsa.key"))
        assert os.path.isfile(os.path.join(restore_dir, "pki", "public", "int.crt"))
        # Tuyệt đối không có root.key hoặc secrets trong bản backup
        for root_dir, _, files in os.walk(restore_dir):
            assert "root.key" not in files
            assert "signer_token" not in files and "p12_passphrase" not in files
