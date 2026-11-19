#!/usr/bin/env python3
"""Service `ca`: nơi DUY NHẤT đọc khóa Intermediate CA (int.key) & INT_PASS để tự động cấp/thu hồi cert nội bộ.

Chỉ nghe trong mạng nội bộ Compose (không publish port). Root CA (`root.key`) tuyệt đối không mount vào đây.
Mọi request (trừ /health) phải kèm header X-CA-Token. Không ghi log bí mật.

  GET  /health
  POST /issue    (JSON: {key_ref, common_name, days}) -> metadata chứng thư vừa tạo
  POST /revoke   (JSON: {key_ref})                    -> thu hồi cert + làm mới int.crl
  POST /crl-int                                       -> làm mới int.crl
"""
from __future__ import annotations

import hmac
import json
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cryptography import x509
from cryptography.x509.oid import NameOID

KEY_REF_RE = re.compile(r"^[A-Za-z0-9_-]{2,64}$")
_BAD_CN_CHARS = re.compile(r"[/\\+\x00-\x1f\x7f]")
MAX_BODY = 64 * 1024
CA_LOCK = threading.Lock()


def _secret(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    if v:
        return v
    f = os.environ.get(name + "_FILE")
    if f:
        with open(f, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    return default


PKI_DIR = os.environ.get("PKI_DIR", "/pki")
ORG = os.environ.get("ORG", "Tecco1")
CRL_BASE = os.environ.get("CRL_BASE", "http://localhost/pki")
CA_SCRIPT = os.environ.get("CA_SCRIPT", "/app/pki/ca.sh")
CA_TOKEN = _secret("CA_TOKEN")
INT_PASS = _secret("INT_PASS")
P12_PASSPHRASE = _secret("P12_PASSPHRASE")


class CAError(Exception):
    def __init__(self, msg: str, code: int = 400):
        super().__init__(msg)
        self.code = code


def validate_key_ref(ref) -> str:
    if not isinstance(ref, str) or not KEY_REF_RE.match(ref):
        raise CAError("key_ref chỉ gồm chữ, số, _, - (2-64 ký tự)", 400)
    return ref


def validate_cn(cn) -> str:
    if not isinstance(cn, str):
        raise CAError("Tên hiển thị (CN) không hợp lệ", 400)
    s = cn.strip()
    if not s or len(s) > 120 or _BAD_CN_CHARS.search(s):
        raise CAError("Tên hiển thị không được rỗng hoặc chứa ký tự đặc biệt (/ \\ +)", 400)
    return s


def validate_days(days) -> int:
    try:
        d = int(days)
    except (TypeError, ValueError) as exc:
        raise CAError("Số ngày hiệu lực không hợp lệ", 400) from exc
    if not 1 <= d <= 1825:
        raise CAError("Số ngày hiệu lực phải từ 1 đến 1825 ngày", 400)
    return d


def _cn(cert) -> str:
    attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    return attrs[0].value if attrs else cert.subject.rfc4514_string()


def _ca_env() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "PKI_DIR": PKI_DIR,
        "ORG": ORG,
        "CRL_BASE": CRL_BASE,
        "INT_PASS": INT_PASS,
        "USER_PASS": P12_PASSPHRASE,
        "RANDFILE": "/tmp/.rnd",
        "TMPDIR": "/tmp",
    }


def _chmod_crl() -> None:
    for fn in ("int.crl", "int.crl.der"):
        p = os.path.join(PKI_DIR, "public", fn)
        if os.path.isfile(p):
            os.chmod(p, 0o644)


def do_issue(req: dict) -> dict:
    key_ref = validate_key_ref(req.get("key_ref"))
    cn = validate_cn(req.get("common_name"))
    days = validate_days(req.get("days", 365))
    if not INT_PASS or not P12_PASSPHRASE:
        raise CAError("CA chưa được cấu hình đầy đủ passphrase", 500)

    issued_dir = os.path.realpath(os.path.join(PKI_DIR, "issued"))
    crt_path = os.path.realpath(os.path.join(issued_dir, key_ref + ".crt"))
    p12_path = os.path.realpath(os.path.join(issued_dir, key_ref + ".p12"))
    if os.path.dirname(crt_path) != issued_dir or os.path.dirname(p12_path) != issued_dir:
        raise CAError("Đường dẫn chứng thư không hợp lệ", 400)

    with CA_LOCK:
        os.makedirs(issued_dir, exist_ok=True)
        if os.path.exists(crt_path) or os.path.exists(p12_path):
            raise CAError("Chứng thư đã tồn tại", 409)
        res = subprocess.run(
            ["bash", CA_SCRIPT, "issue", key_ref, cn, str(days)],
            env=_ca_env(),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if res.returncode != 0 or not os.path.isfile(p12_path) or not os.path.isfile(crt_path):
            sys.stderr.write(f"ca issue failed rc={res.returncode}\n")
            raise CAError("Không thể cấp chứng thư mới", 500)
        os.chmod(p12_path, 0o640)
        os.chmod(crt_path, 0o644)
        cert = x509.load_pem_x509_certificate(open(crt_path, "rb").read())

    return {
        "key_ref": key_ref,
        "subject": _cn(cert),
        "subject_dn": cert.subject.rfc4514_string(),
        "serial": format(cert.serial_number, "x"),
        "not_before": cert.not_valid_before_utc.isoformat(),
        "not_after": cert.not_valid_after_utc.isoformat(),
    }


def do_revoke(req: dict) -> dict:
    key_ref = validate_key_ref(req.get("key_ref"))
    if not INT_PASS:
        raise CAError("CA chưa được cấu hình INT_PASS", 500)

    issued_dir = os.path.realpath(os.path.join(PKI_DIR, "issued"))
    crt_path = os.path.realpath(os.path.join(issued_dir, key_ref + ".crt"))
    if os.path.dirname(crt_path) != issued_dir or not os.path.isfile(crt_path):
        raise CAError("Không tìm thấy chứng thư để thu hồi", 404)

    with CA_LOCK:
        res = subprocess.run(
            ["bash", CA_SCRIPT, "revoke", key_ref],
            env=_ca_env(),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if res.returncode != 0:
            if "already revoked" in (res.stderr or "").lower():
                crl_res = subprocess.run(
                    ["bash", CA_SCRIPT, "crl-int"],
                    env=_ca_env(),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if crl_res.returncode != 0:
                    raise CAError("Không làm mới được CRL", 500)
            else:
                sys.stderr.write(f"ca revoke failed rc={res.returncode}\n")
                raise CAError("Thu hồi chứng thư thất bại", 500)
        _chmod_crl()
    return {"ok": True, "key_ref": key_ref, "revoked": True}


def do_crl_int() -> dict:
    if not INT_PASS:
        raise CAError("CA chưa được cấu hình INT_PASS", 500)
    with CA_LOCK:
        res = subprocess.run(
            ["bash", CA_SCRIPT, "crl-int"],
            env=_ca_env(),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if res.returncode != 0:
            sys.stderr.write(f"ca crl-int failed rc={res.returncode}\n")
            raise CAError("Không làm mới được CRL", 500)
        _chmod_crl()
    return {"ok": True}


class Handler(BaseHTTPRequestHandler):
    server_version = "esign-ca"

    def _json(self, code: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _authed(self) -> bool:
        tok = self.headers.get("X-CA-Token", "")
        if CA_TOKEN and hmac.compare_digest(tok.encode(), CA_TOKEN.encode()):
            return True
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if 0 < n <= 4096:
                self.rfile.read(n)
        except ValueError:
            pass
        self._json(401, {"detail": "Unauthorized"})
        return False

    def _body(self) -> dict | None:
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY:
            self._json(413, {"detail": "Kích thước body không hợp lệ"})
            return None
        if n == 0:
            return {}
        raw = self.rfile.read(n)
        try:
            data = json.loads(raw)
        except ValueError:
            self._json(400, {"detail": "JSON không hợp lệ"})
            return None
        if not isinstance(data, dict):
            self._json(400, {"detail": "Body phải là JSON object"})
            return None
        return data

    def do_GET(self):
        if self.path == "/health":
            return self._json(200, {"status": "ok"})
        self._json(404, {"detail": "Not found"})

    def do_POST(self):
        if not self._authed():
            return
        body = self._body()
        if body is None:
            return
        try:
            if self.path == "/issue":
                return self._json(201, do_issue(body))
            if self.path == "/revoke":
                return self._json(200, do_revoke(body))
            if self.path == "/crl-int":
                return self._json(200, do_crl_int())
            self._json(404, {"detail": "Not found"})
        except CAError as exc:
            self._json(exc.code, {"detail": str(exc)})
        except Exception as exc:
            sys.stderr.write(f"ca error: {type(exc).__name__}\n")
            self._json(500, {"detail": "Lỗi nội bộ dịch vụ CA"})

    def log_message(self, fmt, *args):  # không ghi log request (tránh lộ dữ liệu)
        pass


def main():
    if not CA_TOKEN:
        sys.exit("Thiếu CA_TOKEN")
    if not INT_PASS:
        sys.exit("Thiếu INT_PASS (hoặc INT_PASS_FILE)")
    if not P12_PASSPHRASE:
        sys.exit("Thiếu P12_PASSPHRASE (hoặc P12_PASSPHRASE_FILE)")
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "9001"))), Handler).serve_forever()


if __name__ == "__main__":
    main()
