"""Kiểm tra file upload + client gọi converter/signer qua HTTP nội bộ."""
from __future__ import annotations

import base64
import importlib.util
import io
import os
import re
import tempfile
from typing import Optional

import httpx
from fastapi import HTTPException
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PyPdfError

ALLOWED_EXTS = {".pdf", ".docx", ".doc", ".xlsx", ".pptx"}
_RE_ACTIVE_PDF = re.compile(rb"/(?:JavaScript|JS|Launch|RichMedia|EmbeddedFile)\b")


def _load_office_worker():
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (os.environ.get("CONVERTER_LIB", ""), os.path.join(here, "..", "..", "converter"), "/app/vendor"):
        f = os.path.join(p, "worker.py") if p else ""
        if f and os.path.isfile(f):
            spec = importlib.util.spec_from_file_location("esign_office_worker", f)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise RuntimeError("Không tìm thấy converter/worker.py (CONVERTER_LIB)")


office_worker = _load_office_worker()


def display_name(filename: str) -> str:
    """Chỉ lấy phần tên cuối, bỏ ký tự điều khiển. Tên này KHÔNG bao giờ dùng làm đường dẫn."""
    base = re.split(r"[\\/]", filename or "")[-1]
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip()
    return base[:150] or "document"


def file_ext(filename: str) -> str:
    return os.path.splitext(display_name(filename))[1].lower()


def inspect_pdf(data: bytes, max_pages: int = 500) -> dict:
    """Kiểm tra PDF hợp lệ, không mật khẩu, không nội dung chủ động. Trả số trang + kích thước trang."""
    if not data.startswith(b"%PDF-"):
        raise HTTPException(400, "Không phải file PDF hợp lệ (sai magic bytes)")
    if _RE_ACTIVE_PDF.search(data):
        raise HTTPException(400, "PDF chứa nội dung chủ động (JavaScript/Launch/nhúng file) bị từ chối")
    try:
        r = PdfReader(io.BytesIO(data), strict=False)
        if r.is_encrypted:
            raise HTTPException(400, "PDF có mật khẩu/mã hóa không được hỗ trợ")
        n = len(r.pages)
        if n < 1 or n > max_pages:
            raise HTTPException(400, f"Số trang không hợp lệ (1..{max_pages})")
        sizes, boxes = [], []
        for p in r.pages:
            mb = p.mediabox
            w, h = float(mb.width), float(mb.height)
            rot = int(p.get("/Rotate", 0) or 0) % 360
            if rot in (90, 270):
                w, h = h, w
            sizes.append([round(w, 2), round(h, 2)])
            boxes.append([round(float(mb.left), 2), round(float(mb.bottom), 2),
                          round(float(mb.right), 2), round(float(mb.top), 2)])
        return {"pages": n, "sizes": sizes, "boxes": boxes}
    except HTTPException:
        raise
    except (PyPdfError, ValueError, KeyError, TypeError, RecursionError, OSError):
        raise HTTPException(400, "PDF bị hỏng hoặc không đọc được")


def normalize_pdf_if_hybrid(data: bytes) -> bytes:
    """Chuẩn hóa PDF hybrid-reference (có /XRefStm ở bất kỳ revision nào) khi chưa có chữ ký."""
    if b"/XRefStm" not in data:
        return data
    try:
        r = PdfReader(io.BytesIO(data), strict=False)
        # Nếu PDF đã có chữ ký số thì giữ nguyên từng byte để không phá chữ ký
        acro = r.trailer.get("/Root", {}).get("/AcroForm")
        if acro:
            for fld in acro.get("/Fields", []):
                obj = fld.get_object()
                if obj.get("/FT") == "/Sig" and obj.get("/V") is not None:
                    return data
        w = PdfWriter()
        w.append(r)
        out = io.BytesIO()
        w.write(out)
        return out.getvalue()
    except Exception:
        return data


def validate_image(data: bytes, max_bytes: int) -> str:
    if len(data) > max_bytes:
        raise HTTPException(413, "Ảnh chữ ký quá lớn")
    try:
        im = Image.open(io.BytesIO(data))
        fmt = (im.format or "").upper()
        if fmt not in ("PNG", "JPEG"):
            raise HTTPException(400, "Ảnh chữ ký chỉ nhận PNG/JPEG")
        if im.width * im.height > 12_000_000 or im.width < 8 or im.height < 8:
            raise HTTPException(400, "Kích thước ảnh không hợp lệ")
        im.verify()
        return fmt
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "Ảnh chữ ký không hợp lệ")


class ServiceError(Exception):
    pass


def convert_office(settings, data: bytes, ext: str, orig_name: str) -> bytes:
    """Validate cục bộ rồi gửi sang converter (HTTP, mạng nội bộ). Tên gửi đi là 'document<ext>' (ASCII)."""
    with tempfile.TemporaryDirectory(prefix="up_") as d:
        p = os.path.join(d, "in" + ext)
        with open(p, "wb") as f:
            f.write(data)
        try:
            office_worker.validate_office_file(p, "document" + ext)
        except ValueError as exc:
            raise HTTPException(400, f"File Office bị từ chối: {exc}")
    try:
        r = httpx.post(settings.converter_url.rstrip("/") + "/convert", content=data,
                       headers={"X-Filename": "document" + ext, "X-Timeout": "60",
                                "Content-Type": "application/octet-stream"},
                       timeout=settings.converter_timeout)
    except httpx.HTTPError:
        raise HTTPException(503, "Dịch vụ chuyển đổi không khả dụng")
    if r.status_code == 400:
        raise HTTPException(400, "File Office bị từ chối: " + _detail(r))
    if r.status_code == 504:
        raise HTTPException(504, "Chuyển đổi quá thời gian")
    if r.status_code != 200 or not r.content.startswith(b"%PDF-"):
        raise HTTPException(502, "Chuyển đổi thất bại")
    return r.content


def _detail(r: httpx.Response) -> str:
    try:
        return str(r.json().get("detail", ""))[:300]
    except Exception:
        return ""


def _signer_headers(settings):
    return {"X-Signer-Token": settings.signer_token}


def signer_certs(settings) -> list:
    try:
        r = httpx.get(settings.signer_url.rstrip("/") + "/certs", headers=_signer_headers(settings), timeout=30)
    except httpx.HTTPError:
        raise HTTPException(503, "Dịch vụ ký không khả dụng")
    if r.status_code != 200:
        raise HTTPException(502, "Dịch vụ ký trả lỗi")
    return r.json().get("certs", [])


def signer_status(settings) -> list:
    try:
        r = httpx.get(settings.signer_url.rstrip("/") + "/status", headers=_signer_headers(settings), timeout=30)
    except httpx.HTTPError:
        raise HTTPException(503, "Dịch vụ ký không khả dụng")
    if r.status_code != 200:
        raise HTTPException(502, "Dịch vụ ký trả lỗi")
    return r.json().get("items", [])


def signer_sign(settings, payload: dict) -> bytes:
    """Gọi service signer. Trả bytes PDF đã ký; ValueError(msg) nếu signer từ chối (400)."""
    try:
        r = httpx.post(settings.signer_url.rstrip("/") + "/sign", json=payload,
                       headers=_signer_headers(settings), timeout=settings.signer_timeout)
    except httpx.HTTPError:
        raise ServiceError("Dịch vụ ký không khả dụng")
    if r.status_code == 400:
        raise ValueError(_detail(r) or "Yêu cầu ký bị từ chối")
    if r.status_code != 200 or not r.content.startswith(b"%PDF-"):
        raise ServiceError("Dịch vụ ký trả lỗi")
    return r.content


def signer_verify(settings, pdf: bytes) -> list:
    try:
        r = httpx.post(settings.signer_url.rstrip("/") + "/verify", content=pdf,
                       headers={**_signer_headers(settings), "Content-Type": "application/pdf"},
                       timeout=settings.signer_timeout)
    except httpx.HTTPError:
        raise HTTPException(503, "Dịch vụ xác minh không khả dụng")
    if r.status_code == 400:
        raise HTTPException(400, _detail(r) or "Không xác minh được")
    if r.status_code != 200:
        raise HTTPException(502, "Dịch vụ xác minh trả lỗi")
    return r.json().get("signatures", [])


def b64(data: Optional[bytes]) -> Optional[str]:
    return base64.b64encode(data).decode() if data else None
