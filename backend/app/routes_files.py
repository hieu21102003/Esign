"""Upload, tải về, xác minh, ký, preset. Mọi endpoint đều kiểm tra phiên + 2FA + quyền sở hữu."""
from __future__ import annotations

import json
import math
import re
from typing import List, Literal, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from .audit import audit
from .deps import Ctx, client_ip, get_db, rate_limit, require_mfa
from .models import Certificate, SignJob, SignaturePreset, StoredFile, UserCertificate, utcnow
from .services import (ServiceError, b64, convert_office, display_name, file_ext, inspect_pdf,
                       normalize_pdf_if_hybrid, signer_sign, signer_verify, validate_image, ALLOWED_EXTS)
from .storage import get_file, read_bytes, remove_from_disk, store_bytes

router = APIRouter(prefix="/api", tags=["files"])
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def _file_out(f: StoredFile) -> dict:
    return {"id": f.id, "name": f.original_name, "kind": f.kind, "size": f.size, "sha256": f.sha256,
            "pages": f.pages, "parent_id": f.parent_id, "created_at": f.created_at.isoformat(),
            "expires_at": f.expires_at.isoformat()}


def _read_upload(request: Request, up: UploadFile) -> bytes:
    st = request.app.state.settings
    data = up.file.read(st.max_upload_bytes + 1)
    if len(data) > st.max_upload_bytes:
        raise HTTPException(413, f"File vượt quá {st.max_upload_bytes // (1024 * 1024)}MB")
    if not data:
        raise HTTPException(400, "File rỗng")
    return data


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/files", status_code=201)
def upload_file(request: Request, file: UploadFile = File(...), ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    st = request.app.state.settings
    rate_limit(request, "upload", str(ctx.user.id), st.upload_rate)
    name = display_name(file.filename or "")
    ext = file_ext(name)
    import hashlib
    try:
        if ext not in ALLOWED_EXTS:
            raise HTTPException(415, "Chỉ nhận .pdf, .docx, .doc, .xlsx, .pptx")
        data = _read_upload(request, file)
        orig_hash = hashlib.sha256(data).hexdigest()
        pdf = normalize_pdf_if_hybrid(data) if ext == ".pdf" else convert_office(st, data, ext, name)
        info = inspect_pdf(pdf, st.max_pages)
    except HTTPException as exc:
        audit(db, ctx.ip, "upload_rejected", user=ctx.user, status="denied", detail=f"{exc.detail}"[:300])
        raise
    stem = name.rsplit(".", 1)[0] if ext != ".pdf" else name[:-4]
    f = store_bytes(db, st, ctx.user, pdf, kind="upload", name=stem + ".pdf", pages=info["pages"])
    audit(db, ctx.ip, "upload", user=ctx.user, file_id=f.id, file_sha256=orig_hash,
          output_sha256=f.sha256 if ext != ".pdf" else None, detail=f"ext={ext}")
    return {**_file_out(f), "page_sizes": info["sizes"]}


@router.get("/files")
def list_files(ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    q = select(StoredFile).where(
        StoredFile.owner_id == ctx.user.id, StoredFile.deleted_at.is_(None),
        StoredFile.is_workflow.is_(False), StoredFile.expires_at > utcnow(), StoredFile.kind != "image",
    )
    return [_file_out(f) for f in db.scalars(q.order_by(StoredFile.created_at.desc()))]


@router.get("/files/{file_id}")
def file_info(request: Request, file_id: str, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    f = get_file(db, ctx, file_id)
    out = _file_out(f)
    if f.kind != "image":
        out["page_sizes"] = inspect_pdf(read_bytes(request.app.state.settings, f))["sizes"]
    return out


@router.get("/files/{file_id}/download")
def download(request: Request, file_id: str, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    f = get_file(db, ctx, file_id, kinds=("upload", "signed"))
    data = read_bytes(request.app.state.settings, f)
    audit(db, ctx.ip, "download", user=ctx.user, file_id=f.id, file_sha256=f.sha256)
    fname = f.original_name or "document.pdf"
    cd = f"attachment; filename=\"download.pdf\"; filename*=UTF-8''{quote(fname)}"
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": cd, "X-Content-Type-Options": "nosniff"})


@router.delete("/files/{file_id}")
def delete_file(request: Request, file_id: str, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    f = get_file(db, ctx, file_id)
    if f.is_workflow:
        raise HTTPException(409, "Không thể xóa file đang thuộc luồng ký")
    remove_from_disk(request.app.state.settings, f.id)
    f.deleted_at = utcnow()
    db.commit()
    audit(db, ctx.ip, "file_delete", user=ctx.user, file_id=f.id, file_sha256=f.sha256)
    return {"ok": True}


@router.post("/files/{file_id}/verify")
def verify_stored(request: Request, file_id: str, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    st = request.app.state.settings
    rate_limit(request, "verify", str(ctx.user.id), st.verify_rate)
    f = get_file(db, ctx, file_id, kinds=("upload", "signed"))
    sigs = signer_verify(st, read_bytes(st, f))
    audit(db, ctx.ip, "verify", user=ctx.user, file_id=f.id, file_sha256=f.sha256,
          detail=f"signatures={len(sigs)} all_ok={bool(sigs) and all(s.get('ok') for s in sigs)}")
    return {"file_id": f.id, "signatures": sigs, "all_ok": bool(sigs) and all(s.get("ok") for s in sigs)}


@router.post("/verify")
def verify_upload(request: Request, file: UploadFile = File(...), ctx: Ctx = Depends(require_mfa),
                  db=Depends(get_db)):
    """Xác minh một PDF tải lên (không lưu). Dùng cho trang xác minh."""
    st = request.app.state.settings
    rate_limit(request, "verify", str(ctx.user.id), st.verify_rate)
    import hashlib
    data = _read_upload(request, file)
    inspect_pdf(data, st.max_pages)
    sigs = signer_verify(st, data)
    audit(db, ctx.ip, "verify", user=ctx.user, file_sha256=hashlib.sha256(data).hexdigest(),
          detail=f"signatures={len(sigs)} all_ok={bool(sigs) and all(s.get('ok') for s in sigs)}")
    return {"signatures": sigs, "all_ok": bool(sigs) and all(s.get("ok") for s in sigs)}


@router.post("/signature-images", status_code=201)
def upload_image(request: Request, file: UploadFile = File(...), ctx: Ctx = Depends(require_mfa),
                 db=Depends(get_db)):
    st = request.app.state.settings
    rate_limit(request, "upload", str(ctx.user.id), st.upload_rate)
    data = file.file.read(st.max_image_bytes + 1)
    validate_image(data, st.max_image_bytes)
    f = store_bytes(db, st, ctx.user, data, kind="image", name=display_name(file.filename or "signature"))
    audit(db, ctx.ip, "image_upload", user=ctx.user, file_id=f.id, file_sha256=f.sha256)
    return {"id": f.id, "size": f.size, "sha256": f.sha256}


@router.get("/certificates")
def my_certificates(ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    """Chỉ các chứng thư admin đã gán cho tôi (và còn hiệu lực)."""
    q = (select(Certificate).join(UserCertificate, UserCertificate.cert_id == Certificate.id)
         .where(UserCertificate.user_id == ctx.user.id, Certificate.active.is_(True)))
    return [{"id": c.id, "subject": c.subject, "serial": c.serial, "not_after": c.not_after}
            for c in db.scalars(q)]


class SignParams(BaseModel):
    cert_id: int
    mode: Literal["text", "image", "both"] = "text"
    image_id: Optional[str] = None
    page: int = -1                      # 1-based; -1 = trang cuối
    box: List[float] = Field(min_length=4, max_length=4)
    reason: Optional[str] = Field(None, max_length=500)
    location: Optional[str] = Field(None, max_length=200)
    title: Optional[str] = Field(None, max_length=200)
    show: List[Literal["name", "title", "date", "location", "reason"]] = ["name", "date", "reason"]
    level: Literal["B-B", "B-T", "B-LT"] = "B-T"

    @field_validator("page")
    @classmethod
    def _page(cls, v: int) -> int:
        if v == 0 or v < -1:
            raise ValueError("page phải là số trang >= 1 hoặc -1 (trang cuối)")
        return v

    @field_validator("box")
    @classmethod
    def _box(cls, v: List[float]) -> List[float]:
        if not all(math.isfinite(x) for x in v):
            raise ValueError("box không hợp lệ")
        x1, y1, x2, y2 = v
        if x2 - x1 < 20 or y2 - y1 < 20:
            raise ValueError("box phải có kích thước tối thiểu 20pt")
        return v

    @field_validator("reason", "location", "title")
    @classmethod
    def _clean(cls, v):
        return _CTRL.sub(" ", v).strip() if v else v


class SignRequest(SignParams):
    file_id: str


def validate_sign_inputs(db, st, ctx: Ctx, body: SignParams, f: StoredFile, *, document_id: Optional[str] = None):
    """Kiểm tra chứng thư được gán, kích thước trang PDF, tọa độ ô ký và ảnh chữ ký."""
    cert = db.get(Certificate, body.cert_id)
    assigned = cert is not None and db.get(UserCertificate, (ctx.user.id, cert.id)) is not None
    if not assigned or not cert.active:
        audit(db, ctx.ip, "sign_denied", user=ctx.user, status="denied", file_id=f.id, file_sha256=f.sha256,
              document_id=document_id, detail="Chứng thư không được gán/không hoạt động")
        raise HTTPException(403, "Bạn không được phép dùng chứng thư này")

    pdf = read_bytes(st, f)
    info = inspect_pdf(pdf, st.max_pages)
    page = body.page if body.page > 0 else info["pages"] + 1 + body.page
    if not 1 <= page <= info["pages"]:
        raise HTTPException(400, f"Trang {body.page} không tồn tại (PDF có {info['pages']} trang)")
    bx1, by1, bx2, by2 = info["boxes"][page - 1]
    x1, y1, x2, y2 = body.box
    if x1 < bx1 - 0.5 or y1 < by1 - 0.5 or x2 > bx2 + 0.5 or y2 > by2 + 0.5:
        raise HTTPException(400, "Khung chữ ký nằm ngoài trang")

    img = None
    if body.mode in ("image", "both"):
        if not body.image_id:
            raise HTTPException(400, "Chế độ image/both cần image_id")
        img = read_bytes(st, get_file(db, ctx, body.image_id, owner_only=True, kinds=("image",)))
    return cert, pdf, info, img


@router.post("/sign")
def sign(body: SignRequest, request: Request, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    """Ký PDF. Yêu cầu: phiên hợp lệ + đã qua 2FA + là chủ file + chứng thư được admin gán cho người dùng."""
    st = request.app.state.settings
    rate_limit(request, "sign", str(ctx.user.id), st.sign_rate)
    f = get_file(db, ctx, body.file_id, owner_only=True, kinds=("upload", "signed"))
    if f.is_workflow:
        raise HTTPException(409, "File thuộc luồng ký, vui lòng ký trong màn hình Luồng ký")
    cert, pdf, info, img = validate_sign_inputs(db, st, ctx, body, f)

    params = body.model_dump(exclude={"file_id", "cert_id"})
    job = SignJob(user_id=ctx.user.id, input_file_id=f.id, cert_id=cert.id, params_json=json.dumps(params))
    db.add(job)
    db.commit()
    payload = {"key_ref": cert.key_ref, "pdf_b64": b64(pdf), "image_b64": b64(img), "mode": body.mode,
               "page": body.page, "box": body.box, "reason": body.reason, "location": body.location,
               "title": body.title, "show": body.show, "level": body.level}
    try:
        signed = signer_sign(st, payload)
    except ValueError as exc:  # signer từ chối: chứng thư hết hạn/thu hồi, tham số sai...
        job.status, job.error, job.finished_at = "failed", str(exc)[:400], utcnow()
        db.commit()
        audit(db, ctx.ip, "sign_failed", user=ctx.user, status="error", file_id=f.id, file_sha256=f.sha256,
              cert_key_ref=cert.key_ref, detail=str(exc)[:300])
        raise HTTPException(400, str(exc))
    except ServiceError as exc:
        job.status, job.error, job.finished_at = "failed", str(exc)[:400], utcnow()
        db.commit()
        audit(db, ctx.ip, "sign_failed", user=ctx.user, status="error", file_id=f.id, file_sha256=f.sha256,
              cert_key_ref=cert.key_ref, detail=str(exc)[:300])
        raise HTTPException(502, "Dịch vụ ký không khả dụng")

    stem = f.original_name[:-4] if f.original_name.lower().endswith(".pdf") else f.original_name
    stem = re.sub(r"-signed(-\d+)?$", "", stem)
    out = store_bytes(db, st, ctx.user, signed, kind="signed", name=f"{stem}-signed.pdf",
                      pages=info["pages"], parent_id=f.id)
    job.output_file_id, job.status, job.finished_at = out.id, "done", utcnow()
    db.commit()
    audit(db, ctx.ip, "sign", user=ctx.user, file_id=out.id, file_sha256=f.sha256, output_sha256=out.sha256,
          cert_key_ref=cert.key_ref, detail=f"job={job.id} mode={body.mode} level={body.level}")
    return {"job_id": job.id, "output_file": _file_out(out)}


class PresetIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    config: dict

    @field_validator("config")
    @classmethod
    def _cfg(cls, v: dict) -> dict:
        allowed = {"mode", "show", "reason", "location", "title", "box", "page", "level", "cert_id"}
        if set(v) - allowed or len(json.dumps(v)) > 4000:
            raise ValueError("config không hợp lệ")
        return v


@router.get("/presets")
def list_presets(ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    q = select(SignaturePreset).where(SignaturePreset.user_id == ctx.user.id).order_by(SignaturePreset.id)
    return [{"id": p.id, "name": p.name, "config": json.loads(p.config_json)} for p in db.scalars(q)]


@router.post("/presets", status_code=201)
def create_preset(body: PresetIn, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    p = SignaturePreset(user_id=ctx.user.id, name=body.name.strip(), config_json=json.dumps(body.config))
    db.add(p)
    db.commit()
    return {"id": p.id, "name": p.name, "config": body.config}


@router.delete("/presets/{pid}")
def delete_preset(pid: int, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    p = db.get(SignaturePreset, pid)
    if p is None or p.user_id != ctx.user.id:
        raise HTTPException(404, "Không tìm thấy preset")
    db.delete(p)
    db.commit()
    return {"ok": True}
