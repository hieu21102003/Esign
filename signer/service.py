#!/usr/bin/env python3
"""Service `signer`: nơi DUY NHẤT đọc khóa .p12 và passphrase.

Chỉ nghe trong mạng nội bộ Compose (không publish port). Mọi request (trừ /health) phải kèm
header X-Signer-Token. Passphrase lấy từ biến môi trường / secret file, không ghi log.

  GET  /health
  GET  /certs            -> danh sách chứng thư (.p12) trong KEYS_DIR (chỉ metadata công khai)
  POST /sign   (JSON)    -> PDF đã ký
  POST /verify (PDF raw) -> kết quả xác minh JSON
"""
from __future__ import annotations

import base64
import hmac
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from asn1crypto import crl as asn1crl, pem  # noqa: E402
from cryptography.hazmat.primitives.serialization import pkcs12  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402

from esign_core import Appearance, make_vc, sign_pdf, verify_pdf  # noqa: E402

KEY_REF_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_BODY = int(os.environ.get("SIGNER_MAX_BODY", str(80 * 1024 * 1024)))
LEVELS = {"B-B", "B-T", "B-LT"}
MODES = {"text", "image", "both"}
SHOW = {"name", "title", "date", "location", "reason"}


def _secret(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    if v:
        return v
    f = os.environ.get(name + "_FILE")
    if f:
        with open(f, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    return default


KEYS_DIR = os.environ.get("KEYS_DIR", "/pki/issued")
PKI_PUBLIC = os.environ.get("PKI_PUBLIC", "/pki/public")
TSA_URL = os.environ.get("TSA_URL") or None
SIGNER_TOKEN = _secret("SIGNER_TOKEN")
P12_PASSPHRASE = _secret("P12_PASSPHRASE")


class SignerError(Exception):
    """Lỗi do dữ liệu/ngữ cảnh yêu cầu (trả 400)."""


def _p12_path(key_ref: str) -> str:
    if not isinstance(key_ref, str) or not KEY_REF_RE.match(key_ref):
        raise SignerError("key_ref không hợp lệ")
    base = os.path.realpath(KEYS_DIR)
    path = os.path.realpath(os.path.join(base, key_ref + ".p12"))
    if os.path.dirname(path) != base or not os.path.isfile(path):
        raise SignerError("Không tìm thấy chứng thư")
    return path


def _load_cert(path: str):
    with open(path, "rb") as f:
        data = f.read()
    _key, cert, _extra = pkcs12.load_key_and_certificates(data, P12_PASSPHRASE.encode())
    if cert is None:
        raise SignerError("File .p12 không chứa chứng thư")
    return cert


def _cn(cert) -> str:
    attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    return attrs[0].value if attrs else cert.subject.rfc4514_string()


def _crl_paths():
    return [p for p in (os.path.join(PKI_PUBLIC, "int.crl"), os.path.join(PKI_PUBLIC, "root.crl")) if os.path.isfile(p)]


def _load_crl(path: str):
    data = open(path, "rb").read()
    if pem.detect(data):
        data = pem.unarmor(data)[2]
    return asn1crl.CertificateList.load(data)


def _check_crl_freshness(now: datetime) -> None:
    paths = _crl_paths()
    if not any(os.path.basename(p) == "int.crl" for p in paths):
        raise SignerError("Thiếu file CRL (int.crl)")
    for p in paths:
        lst = _load_crl(p)
        nu = lst["tbs_cert_list"]["next_update"].native
        if nu is None or nu <= now:
            raise SignerError(f"CRL đã quá hạn ({os.path.basename(p)}), cần làm mới trước khi ký")


def _revoked_serials() -> set:
    out = set()
    for p in _crl_paths():
        lst = _load_crl(p)
        for rc in lst["tbs_cert_list"]["revoked_certificates"] or []:
            out.add(int(rc["user_certificate"].native))
    return out


def _status_item(kind: str, name: str, expires_at: datetime, now: datetime) -> dict:
    days_left = round((expires_at - now).total_seconds() / 86400, 1)
    st = "expired" if days_left <= 0 else ("expiring_soon" if days_left <= 30 else "ok")
    return dict(kind=kind, name=name, expires_at=expires_at.isoformat(), days_left=days_left, status=st)


def pki_status() -> list:
    from cryptography import x509 as cx509
    now = datetime.now(timezone.utc)
    items = []
    if os.path.isdir(KEYS_DIR):
        for fn in sorted(os.listdir(KEYS_DIR)):
            if fn.endswith(".p12") and KEY_REF_RE.match(fn[:-4]):
                try:
                    c = _load_cert(os.path.join(KEYS_DIR, fn))
                    items.append(_status_item("signer_cert", f"{fn[:-4]} ({_cn(c)})", c.not_valid_after_utc, now))
                except Exception:
                    items.append(dict(kind="signer_cert", name=fn[:-4], expires_at="", days_left=-1, status="error"))
    tsa_crt = os.path.join(os.path.dirname(os.path.abspath(KEYS_DIR)), "tsa", "tsa.crt")
    for kind, label, path in (
        ("tsa_cert", "TSA Certificate", tsa_crt),
        ("intermediate_ca", "Intermediate CA", os.path.join(PKI_PUBLIC, "int.crt")),
    ):
        if os.path.isfile(path):
            raw = open(path, "rb").read()
            c = cx509.load_pem_x509_certificate(raw) if b"BEGIN CERTIFICATE" in raw else cx509.load_der_x509_certificate(raw)
            items.append(_status_item(kind, label, c.not_valid_after_utc, now))
    for p in _crl_paths():
        nu = _load_crl(p)["tbs_cert_list"]["next_update"].native
        if nu is not None:
            items.append(_status_item("crl", os.path.basename(p), nu, now))
    return items


def list_certs() -> list:
    res = []
    if not os.path.isdir(KEYS_DIR):
        return res
    revoked = None
    for fn in sorted(os.listdir(KEYS_DIR)):
        if not fn.endswith(".p12"):
            continue
        ref = fn[:-4]
        if not KEY_REF_RE.match(ref):
            continue
        try:
            cert = _load_cert(os.path.join(KEYS_DIR, fn))
            if revoked is None:
                revoked = _revoked_serials()
            res.append(dict(
                key_ref=ref, subject=_cn(cert), subject_dn=cert.subject.rfc4514_string(),
                serial=format(cert.serial_number, "x"),
                not_before=cert.not_valid_before_utc.isoformat(),
                not_after=cert.not_valid_after_utc.isoformat(),
                revoked=cert.serial_number in revoked))
        except Exception:  # sai passphrase / file hỏng: không lộ chi tiết
            res.append(dict(key_ref=ref, error="Không đọc được chứng thư"))
    return res


def do_sign(req: dict) -> bytes:
    path = _p12_path(req.get("key_ref"))
    cert = _load_cert(path)
    now = datetime.now(timezone.utc)
    if cert.not_valid_after_utc < now or cert.not_valid_before_utc > now:
        raise SignerError("Chứng thư hết hạn hoặc chưa có hiệu lực")
    _check_crl_freshness(now)
    if cert.serial_number in _revoked_serials():
        raise SignerError("Chứng thư đã bị thu hồi")

    mode = req.get("mode", "text")
    level = req.get("level", "B-T")
    if mode not in MODES or level not in LEVELS:
        raise SignerError("mode/level không hợp lệ")
    page = int(req.get("page", -1))
    if page == 0 or page < -1:
        raise SignerError("page không hợp lệ")
    box = tuple(float(x) for x in req.get("box", ()))
    if len(box) != 4 or not (box[2] > box[0] and box[3] > box[1]):
        raise SignerError("box không hợp lệ")
    show = tuple(s for s in req.get("show", ["name", "date", "reason"]) if s in SHOW)
    try:
        pdf = base64.b64decode(req.get("pdf_b64", ""), validate=True)
        img = base64.b64decode(req["image_b64"], validate=True) if req.get("image_b64") else None
    except Exception as exc:
        raise SignerError("Dữ liệu base64 không hợp lệ") from exc
    if not pdf.startswith(b"%PDF-"):
        raise SignerError("Không phải PDF")
    if mode in ("image", "both") and not img:
        raise SignerError("Chế độ image/both cần ảnh chữ ký")

    with tempfile.TemporaryDirectory(prefix="sg_") as d:
        pin, pout, pimg = (os.path.join(d, n) for n in ("in.pdf", "out.pdf", "sig.img"))
        open(pin, "wb").write(pdf)
        if img:
            open(pimg, "wb").write(img)
        ap = Appearance(mode=mode, image_path=pimg if img else None,
                        page=(page - 1 if page > 0 else page), box=box, show=show,
                        title=req.get("title") or None)
        vc = make_vc(os.path.join(PKI_PUBLIC, "root.crt"), os.path.join(PKI_PUBLIC, "chain.pem"),
                     _crl_paths(), revocation="soft-fail") if level == "B-LT" else None
        try:
            sign_pdf(pin, pout, path, P12_PASSPHRASE, ap, reason=req.get("reason") or None,
                     location=req.get("location") or None, tsa_url=TSA_URL, validation_ctx=vc, level=level)
        except ValueError as exc:
            raise SignerError(str(exc)) from exc
        return open(pout, "rb").read()


def do_verify(pdf: bytes) -> list:
    if not pdf.startswith(b"%PDF-"):
        raise SignerError("Không phải PDF")
    root, chain = os.path.join(PKI_PUBLIC, "root.crt"), os.path.join(PKI_PUBLIC, "chain.pem")
    if not os.path.isfile(root):
        raise SignerError("Chưa có dữ liệu PKI công khai để xác minh")
    vc = make_vc(root, chain if os.path.isfile(chain) else None, _crl_paths(), revocation="require")
    with tempfile.TemporaryDirectory(prefix="sg_") as d:
        p = os.path.join(d, "v.pdf")
        open(p, "wb").write(pdf)
        return verify_pdf(p, vc)


class Handler(BaseHTTPRequestHandler):
    server_version = "esign-signer"

    def _json(self, code: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self) -> bool:
        tok = self.headers.get("X-Signer-Token", "")
        if SIGNER_TOKEN and hmac.compare_digest(tok.encode(), SIGNER_TOKEN.encode()):
            return True
        self._json(401, {"detail": "Unauthorized"})
        return False

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = -1
        if n <= 0 or n > MAX_BODY:
            self._json(413, {"detail": "Kích thước body không hợp lệ"})
            return None
        return self.rfile.read(n)

    def do_GET(self):
        if self.path == "/health":
            return self._json(200, {"status": "ok"})
        if not self._authed():
            return
        if self.path == "/certs":
            return self._json(200, {"certs": list_certs()})
        if self.path == "/status":
            return self._json(200, {"items": pki_status()})
        self._json(404, {"detail": "Not found"})

    def do_POST(self):
        if not self._authed():
            return
        body = self._body()
        if body is None:
            return
        try:
            if self.path == "/sign":
                out = do_sign(json.loads(body))
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
                return
            if self.path == "/verify":
                return self._json(200, {"signatures": do_verify(body)})
            self._json(404, {"detail": "Not found"})
        except SignerError as exc:
            self._json(400, {"detail": str(exc)})
        except Exception as exc:  # không rò rỉ chi tiết nội bộ (đường dẫn, passphrase...)
            sys.stderr.write(f"signer error: {type(exc).__name__}\n")
            self._json(500, {"detail": "Lỗi nội bộ khi ký/xác minh"})

    def log_message(self, fmt, *args):  # không ghi log request (tránh lộ dữ liệu)
        pass


def main():
    if not SIGNER_TOKEN:
        sys.exit("Thiếu SIGNER_TOKEN")
    if not P12_PASSPHRASE:
        sys.exit("Thiếu P12_PASSPHRASE (hoặc P12_PASSPHRASE_FILE)")
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "9000"))), Handler).serve_forever()


if __name__ == "__main__":
    main()
