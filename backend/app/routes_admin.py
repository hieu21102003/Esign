"""API quản trị: người dùng, chứng thư, gán chứng thư, đọc audit (chỉ đọc)."""
from __future__ import annotations

import logging
import re
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, desc, select

from .audit import audit
from .deps import Ctx, get_db, require_admin
from .models import AuditLog, Certificate, RecoveryCode, SessionRow, User, UserCertificate, utcnow
from .security import check_password_policy, hash_password
from .services import signer_certs, signer_status

log = logging.getLogger("esign.pki")

router = APIRouter(prefix="/api/admin", tags=["admin"])
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
KEY_REF_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _clean_email(v: Optional[str]) -> Optional[str]:
    if not v or not v.strip():
        return None
    s = v.strip().lower()
    if len(s) > 255 or not EMAIL_RE.match(s):
        raise HTTPException(400, "Email không hợp lệ")
    return s


class UserIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)
    display_name: str = Field("", max_length=120)
    email: Optional[str] = Field(None, max_length=255)
    role: Literal["admin", "user"] = "user"


class EmailIn(BaseModel):
    email: Optional[str] = Field(None, max_length=255)


class CertIn(BaseModel):
    key_ref: str = Field(max_length=64)


class AssignIn(BaseModel):
    cert_ids: List[int] = Field(max_length=200)


def _user_out(u: User) -> dict:
    return {"id": u.id, "username": u.username, "display_name": u.display_name, "email": u.email, "role": u.role,
            "is_active": u.is_active, "totp_enabled": u.totp_enabled,
            "locked": bool(u.locked_until and u.locked_until > utcnow()), "created_at": u.created_at.isoformat()}


def _get_user(db, uid: int) -> User:
    u = db.get(User, uid)
    if u is None:
        raise HTTPException(404, "Không tìm thấy người dùng")
    return u


@router.post("/users", status_code=201)
def create_user(body: UserIn, request: Request, ctx: Ctx = Depends(require_admin), db=Depends(get_db)):
    st = request.app.state.settings
    username = body.username.strip().lower()
    if not USERNAME_RE.match(username):
        raise HTTPException(400, "Username chỉ gồm a-z 0-9 . _ - (2-64 ký tự)")
    err = check_password_policy(body.password, st.min_password_length)
    if err:
        raise HTTPException(400, err)
    if db.scalar(select(User).where(User.username == username)):
        raise HTTPException(409, "Username đã tồn tại")
    u = User(username=username, display_name=body.display_name.strip(), email=_clean_email(body.email),
             password_hash=hash_password(body.password), role=body.role)
    db.add(u)
    db.commit()
    audit(db, ctx.ip, "user_create", user=ctx.user, detail=f"target={username} role={u.role}")
    return _user_out(u)


@router.get("/users")
def list_users(ctx: Ctx = Depends(require_admin), db=Depends(get_db)):
    return [_user_out(u) for u in db.scalars(select(User).order_by(User.id))]


def _set_active(uid: int, active: bool, ctx: Ctx, db) -> dict:
    u = _get_user(db, uid)
    if u.id == ctx.user.id and not active:
        raise HTTPException(400, "Không thể tự vô hiệu hóa chính mình")
    u.is_active = active
    if not active:
        db.execute(delete(SessionRow).where(SessionRow.user_id == u.id))
    db.commit()
    audit(db, ctx.ip, "user_enable" if active else "user_disable", user=ctx.user, detail=f"target={u.username}")
    return _user_out(u)


@router.post("/users/{uid}/disable")
def disable_user(uid: int, ctx: Ctx = Depends(require_admin), db=Depends(get_db)):
    return _set_active(uid, False, ctx, db)


@router.post("/users/{uid}/enable")
def enable_user(uid: int, ctx: Ctx = Depends(require_admin), db=Depends(get_db)):
    return _set_active(uid, True, ctx, db)


@router.post("/users/{uid}/unlock")
def unlock_user(uid: int, ctx: Ctx = Depends(require_admin), db=Depends(get_db)):
    u = _get_user(db, uid)
    u.locked_until, u.failed_attempts = None, 0
    db.commit()
    audit(db, ctx.ip, "user_unlock", user=ctx.user, detail=f"target={u.username}")
    return _user_out(u)


@router.put("/users/{uid}/email")
def update_user_email(uid: int, body: EmailIn, ctx: Ctx = Depends(require_admin), db=Depends(get_db)):
    u = _get_user(db, uid)
    u.email = _clean_email(body.email)
    db.commit()
    audit(db, ctx.ip, "user_update_email", user=ctx.user, detail=f"target={u.username}")
    return _user_out(u)


@router.post("/users/{uid}/reset-2fa")
def reset_2fa(uid: int, ctx: Ctx = Depends(require_admin), db=Depends(get_db)):
    """Mất thiết bị + mất mã khôi phục: xóa TOTP, người dùng phải đăng ký lại ở lần đăng nhập sau."""
    u = _get_user(db, uid)
    u.totp_secret_enc, u.totp_enabled, u.totp_last_step = None, False, 0
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == u.id))
    db.execute(delete(SessionRow).where(SessionRow.user_id == u.id))
    db.commit()
    audit(db, ctx.ip, "user_reset_2fa", user=ctx.user, detail=f"target={u.username}")
    return _user_out(u)


def _cert_out(c: Certificate) -> dict:
    return {"id": c.id, "key_ref": c.key_ref, "subject": c.subject, "serial": c.serial,
            "not_after": c.not_after, "active": c.active}


@router.get("/certificates/available")
def available_certs(request: Request, ctx: Ctx = Depends(require_admin), db=Depends(get_db)):
    """Chứng thư (.p12) signer đang giữ + trạng thái đã đăng ký vào hệ thống hay chưa."""
    registered = {c.key_ref for c in db.scalars(select(Certificate))}
    return [{**c, "registered": c.get("key_ref") in registered}
            for c in signer_certs(request.app.state.settings)]


@router.post("/certificates", status_code=201)
def register_cert(body: CertIn, request: Request, ctx: Ctx = Depends(require_admin), db=Depends(get_db)):
    if not KEY_REF_RE.match(body.key_ref):
        raise HTTPException(400, "key_ref không hợp lệ")
    if db.scalar(select(Certificate).where(Certificate.key_ref == body.key_ref)):
        raise HTTPException(409, "Chứng thư đã đăng ký")
    info = next((c for c in signer_certs(request.app.state.settings) if c.get("key_ref") == body.key_ref), None)
    if info is None or "error" in info:
        raise HTTPException(404, "Signer không có/không đọc được chứng thư này")
    c = Certificate(key_ref=body.key_ref, subject=info["subject"][:255], serial=info["serial"],
                    not_after=info["not_after"])
    db.add(c)
    db.commit()
    audit(db, ctx.ip, "cert_register", user=ctx.user, cert_key_ref=c.key_ref)
    return _cert_out(c)


@router.get("/certificates")
def list_certs(ctx: Ctx = Depends(require_admin), db=Depends(get_db)):
    return [_cert_out(c) for c in db.scalars(select(Certificate).order_by(Certificate.id))]


@router.post("/certificates/{cid}/deactivate")
def deactivate_cert(cid: int, ctx: Ctx = Depends(require_admin), db=Depends(get_db)):
    c = db.get(Certificate, cid)
    if c is None:
        raise HTTPException(404, "Không tìm thấy chứng thư")
    c.active = False
    db.commit()
    audit(db, ctx.ip, "cert_deactivate", user=ctx.user, cert_key_ref=c.key_ref)
    return _cert_out(c)


@router.put("/users/{uid}/certificates")
def assign_certs(uid: int, body: AssignIn, ctx: Ctx = Depends(require_admin), db=Depends(get_db)):
    u = _get_user(db, uid)
    ids = set(body.cert_ids)
    found = {c.id for c in db.scalars(select(Certificate).where(Certificate.id.in_(ids)))} if ids else set()
    if found != ids:
        raise HTTPException(400, "Có chứng thư không tồn tại")
    db.execute(delete(UserCertificate).where(UserCertificate.user_id == u.id))
    for cid in ids:
        db.add(UserCertificate(user_id=u.id, cert_id=cid))
    db.commit()
    audit(db, ctx.ip, "cert_assign", user=ctx.user, detail=f"target={u.username} certs={sorted(ids)}")
    return {"user_id": u.id, "cert_ids": sorted(ids)}


@router.get("/expiry-warnings")
def expiry_warnings(days: int = 30, request: Request = None, ctx: Ctx = Depends(require_admin)):
    """Cảnh báo chứng thư người ký, TSA, Intermediate CA và CRL sắp hết hạn (mặc định <= 30 ngày)."""
    items = signer_status(request.app.state.settings)
    warnings = [it for it in items if isinstance(it.get("days_left"), (int, float)) and it["days_left"] <= days]
    for w in warnings:
        log.warning("PKI expiry warning: kind=%s name=%s days_left=%s", w.get("kind"), w.get("name"), w.get("days_left"))
    return {"items": items, "warnings": warnings}


@router.get("/audit")
def read_audit(limit: int = 100, offset: int = 0, action: str = "", document_id: str = "",
               ctx: Ctx = Depends(require_admin), db=Depends(get_db)):
    """Chỉ đọc. Không có endpoint nào sửa/xóa audit_logs."""
    q = select(AuditLog).order_by(desc(AuditLog.id)).limit(max(1, min(limit, 500))).offset(max(0, offset))
    if action:
        q = q.where(AuditLog.action == action)
    if document_id:
        q = q.where(AuditLog.document_id == document_id)
    return [{"id": a.id, "ts": a.ts.isoformat(), "user_id": a.user_id, "username": a.username, "action": a.action,
             "status": a.status, "ip": a.ip, "file_id": a.file_id, "file_sha256": a.file_sha256,
             "output_sha256": a.output_sha256, "cert_key_ref": a.cert_key_ref, "detail": a.detail,
             "document_id": a.document_id, "metadata_json": a.metadata_json}
            for a in db.scalars(q)]
