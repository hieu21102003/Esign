"""API quản trị: người dùng, chứng thư, gán chứng thư, đọc audit (chỉ đọc)."""
from __future__ import annotations

import logging
import re
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, desc, func, select, update

from .audit import audit
from .deps import Ctx, get_db, require_admin
from .pin import now_from_request
from .pki_helpers import clean_cn, clean_email, next_key_ref
from .models import (
    AuditLog,
    Certificate,
    Document,
    DocumentVersion,
    Notification,
    OnboardingOtp,
    RecoveryCode,
    SessionRow,
    SignaturePreset,
    SignJob,
    StepAssignee,
    StoredFile,
    User,
    UserCertificate,
    WorkflowStep,
    utcnow,
)
from .security import check_password_policy, hash_password
from .services import ca_health, ca_issue, ca_revoke, signer_certs, signer_status

log = logging.getLogger("esign.pki")

router = APIRouter(prefix="/api/admin", tags=["admin"])
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
KEY_REF_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class UserIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)
    display_name: str = Field("", max_length=120)
    email: Optional[str] = Field(None, max_length=255)
    role: Literal["admin", "user"] = "user"
    issue_cert: bool = False
    cert_days: int = Field(365, ge=1, le=1825)


class IssueUserCertIn(BaseModel):
    common_name: Optional[str] = Field(None, max_length=120)
    key_ref: Optional[str] = Field(None, max_length=64)
    days: int = Field(365, ge=1, le=1825)


class EmailIn(BaseModel):
    email: Optional[str] = Field(None, max_length=255)


class CertIn(BaseModel):
    key_ref: str = Field(max_length=64)


class AssignIn(BaseModel):
    cert_ids: List[int] = Field(max_length=200)


def _user_out(u: User, cert_refs: Optional[List[str]] = None, now=None) -> dict:
    cur = now or utcnow()
    pin_locked = bool(u.pin_locked_until and u.pin_locked_until > cur)
    return {"id": u.id, "username": u.username, "display_name": u.display_name, "email": u.email, "role": u.role,
            "job_title": u.job_title or "", "department": u.department or "", "manager_name": u.manager_name or "",
            "onboarding_completed": u.onboarding_completed_at is not None,
            "accepted_terms_version": u.accepted_terms_version,
            "is_active": u.is_active, "totp_enabled": u.totp_enabled,
            "locked": bool(u.locked_until and u.locked_until > cur),
            "has_signing_pin": bool(u.signing_pin_hash),
            "pin_locked": pin_locked,
            "pin_locked_until": u.pin_locked_until.isoformat() if pin_locked and u.pin_locked_until else None,
            "created_at": u.created_at.isoformat(),
            "cert_refs": cert_refs if cert_refs is not None else []}


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
    email = clean_email(body.email)
    if email and db.scalar(select(User.id).where(func.lower(User.email) == email.lower()).limit(1)) is not None:
        raise HTTPException(409, "Email đã được sử dụng bởi tài khoản khác")
    display_name = body.display_name.strip()

    issued_cert = None
    key_ref = None
    if body.issue_cert:
        cn = clean_cn(display_name or username)
        key_ref = next_key_ref(db, username)
        issued_cert = ca_issue(st, key_ref, cn, body.cert_days)

    u = User(username=username, display_name=display_name, email=email,
             password_hash=hash_password(body.password), role=body.role)
    db.add(u)
    db.flush()

    cert_row = None
    if issued_cert is not None and key_ref is not None:
        cert_row = Certificate(
            key_ref=key_ref,
            subject=issued_cert["subject"][:255],
            serial=issued_cert["serial"],
            not_after=issued_cert["not_after"],
        )
        db.add(cert_row)
        db.flush()
        db.add(UserCertificate(user_id=u.id, cert_id=cert_row.id))

    audit(db, ctx.ip, "user_create", user=ctx.user, detail=f"target={username} role={u.role}", commit=False)
    if cert_row is not None:
        audit(db, ctx.ip, "cert_issue", user=ctx.user, cert_key_ref=cert_row.key_ref,
              detail=f"target={username} days={body.cert_days}", commit=False)
        audit(db, ctx.ip, "cert_assign", user=ctx.user,
              detail=f"target={username} certs={[cert_row.id]}", commit=False)
    db.commit()

    out = _user_out(u, [cert_row.key_ref] if cert_row is not None else [])
    if cert_row is not None:
        out["certificate"] = _cert_out(cert_row)
    return out


@router.get("/users")
def list_users(request: Request, ctx: Ctx = Depends(require_admin), db=Depends(get_db)):
    users = list(db.scalars(select(User).order_by(User.id)))
    refs_by_uid: dict[int, List[str]] = {}
    for uid, kref in db.execute(
        select(UserCertificate.user_id, Certificate.key_ref)
        .join(Certificate, Certificate.id == UserCertificate.cert_id)
        .where(Certificate.active.is_(True))
        .order_by(Certificate.id)
    ):
        refs_by_uid.setdefault(uid, []).append(kref)
    _, now_dt = now_from_request(request)
    return [_user_out(u, refs_by_uid.get(u.id, []), now=now_dt) for u in users]


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
    u.pin_locked_until, u.pin_failed_attempts, u.pin_fail_times_json = None, 0, "[]"
    db.commit()
    audit(db, ctx.ip, "user_unlock", user=ctx.user, detail=f"target={u.username}")
    return _user_out(u)


@router.post("/users/{uid}/unlock-pin")
def unlock_user_pin(uid: int, ctx: Ctx = Depends(require_admin), db=Depends(get_db)):
    """Mở khóa chức năng ký của người dùng bị khóa do nhập sai PIN nhiều lần."""
    u = _get_user(db, uid)
    u.pin_locked_until = None
    u.pin_failed_attempts = 0
    u.pin_fail_times_json = "[]"
    db.commit()
    audit(db, ctx.ip, "pin_unlock", user=ctx.user, detail=f"target={u.username}")
    return _user_out(u)


@router.put("/users/{uid}/email")
def update_user_email(uid: int, body: EmailIn, ctx: Ctx = Depends(require_admin), db=Depends(get_db)):
    u = _get_user(db, uid)
    email = clean_email(body.email)
    if email and db.scalar(select(User.id).where(func.lower(User.email) == email.lower(), User.id != u.id).limit(1)) is not None:
        raise HTTPException(409, "Email đã được sử dụng bởi tài khoản khác")
    u.email = email
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


@router.post("/users/{uid}/reset-onboarding")
def reset_onboarding(uid: int, ctx: Ctx = Depends(require_admin), db=Depends(get_db)):
    """Buộc user thực hiện lại quy trình onboarding ở lần đăng nhập / tải trang kế tiếp."""
    u = _get_user(db, uid)
    u.onboarding_completed_at = None
    u.onboarding_step = 1
    u.accepted_terms_version = None
    u.signature_image_file_id = None
    u.onboarding_otp_verified_at = None
    u.onboarding_otp_session_id = None
    db.commit()
    audit(db, ctx.ip, "admin_reset_onboarding", user=ctx.user, detail=f"target={u.username}")
    return _user_out(u)


@router.post("/users/{uid}/reset-pin")
def reset_signing_pin(uid: int, ctx: Ctx = Depends(require_admin), db=Depends(get_db)):
    """Xóa PIN ký hiện tại để người dùng phải đặt PIN mới."""
    u = _get_user(db, uid)
    u.signing_pin_hash = None
    u.pin_set_at = None
    u.pin_failed_attempts = 0
    u.pin_fail_times_json = "[]"
    u.pin_locked_until = None
    db.commit()
    audit(db, ctx.ip, "admin_reset_pin", user=ctx.user, detail=f"target={u.username}")
    return _user_out(u)


@router.delete("/users/{uid}")
def delete_user(uid: int, ctx: Ctx = Depends(require_admin), db=Depends(get_db)):
    """Xóa vĩnh viễn user chưa phát sinh tài liệu/lịch sử ký; chặn nếu đã có lịch sử truy vết."""
    u = _get_user(db, uid)
    if u.id == ctx.user.id:
        raise HTTPException(400, "Không thể tự xóa tài khoản của chính mình")

    has_history = any(
        db.scalar(q) is not None
        for q in (
            select(StoredFile.id).where(StoredFile.owner_id == u.id).limit(1),
            select(SignJob.id).where(SignJob.user_id == u.id).limit(1),
            select(Document.id).where(Document.creator_id == u.id).limit(1),
            select(WorkflowStep.id).where(WorkflowStep.assigned_by_id == u.id).limit(1),
            select(StepAssignee.id).where(StepAssignee.user_id == u.id).limit(1),
            select(DocumentVersion.id).where(DocumentVersion.created_by_id == u.id).limit(1),
        )
    )
    if has_history:
        raise HTTPException(
            409,
            "Không thể xóa vĩnh viễn user đã có tài liệu hoặc lịch sử ký; hãy dùng 'Vô hiệu hóa' để khóa đăng nhập và giữ nguyên lịch sử truy vết",
        )

    uname = u.username
    db.execute(update(User).where(User.manager_user_id == u.id).values(manager_user_id=None))
    db.execute(delete(OnboardingOtp).where(OnboardingOtp.user_id == u.id))
    db.execute(delete(SessionRow).where(SessionRow.user_id == u.id))
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == u.id))
    db.execute(delete(UserCertificate).where(UserCertificate.user_id == u.id))
    db.execute(delete(SignaturePreset).where(SignaturePreset.user_id == u.id))
    db.execute(delete(Notification).where(Notification.user_id == u.id))
    db.delete(u)
    audit(db, ctx.ip, "user_delete", user=ctx.user, detail=f"target={uname}", commit=False)
    db.commit()
    return {"ok": True, "deleted_id": uid, "username": uname}


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


@router.get("/ca/status")
def get_ca_status(request: Request, ctx: Ctx = Depends(require_admin)):
    st = request.app.state.settings
    enabled = bool((getattr(st, "ca_url", "") or "").strip())
    return {"enabled": enabled, "healthy": ca_health(st) if enabled else False}


@router.post("/users/{uid}/issue-certificate", status_code=201)
def issue_user_certificate(
    uid: int, body: IssueUserCertIn, request: Request, ctx: Ctx = Depends(require_admin), db=Depends(get_db)
):
    st = request.app.state.settings
    u = _get_user(db, uid)
    cn = clean_cn(body.common_name or u.display_name or u.username)
    if body.key_ref and body.key_ref.strip():
        key_ref = body.key_ref.strip()
        if not KEY_REF_RE.match(key_ref) or len(key_ref) < 2:
            raise HTTPException(400, "key_ref chỉ gồm chữ, số, _, - (2-64 ký tự)")
        if db.scalar(select(Certificate).where(Certificate.key_ref == key_ref)):
            raise HTTPException(409, "key_ref đã tồn tại")
    else:
        key_ref = next_key_ref(db, u.username)

    info = ca_issue(st, key_ref, cn, body.days)
    c = Certificate(
        key_ref=key_ref,
        subject=info["subject"][:255],
        serial=info["serial"],
        not_after=info["not_after"],
    )
    db.add(c)
    db.flush()
    db.add(UserCertificate(user_id=u.id, cert_id=c.id))
    audit(db, ctx.ip, "cert_issue", user=ctx.user, cert_key_ref=c.key_ref,
          detail=f"target={u.username} days={body.days}", commit=False)
    audit(db, ctx.ip, "cert_assign", user=ctx.user,
          detail=f"target={u.username} certs={[c.id]}", commit=False)
    db.commit()
    return {"user_id": u.id, "certificate": _cert_out(c)}


@router.post("/certificates/{cid}/deactivate")
def deactivate_cert(cid: int, ctx: Ctx = Depends(require_admin), db=Depends(get_db)):
    c = db.get(Certificate, cid)
    if c is None:
        raise HTTPException(404, "Không tìm thấy chứng thư")
    c.active = False
    db.commit()
    audit(db, ctx.ip, "cert_deactivate", user=ctx.user, cert_key_ref=c.key_ref)
    return _cert_out(c)


@router.post("/certificates/{cid}/revoke")
def revoke_cert(cid: int, request: Request, ctx: Ctx = Depends(require_admin), db=Depends(get_db)):
    st = request.app.state.settings
    c = db.get(Certificate, cid)
    if c is None:
        raise HTTPException(404, "Không tìm thấy chứng thư")
    ca_revoke(st, c.key_ref)
    c.active = False
    audit(db, ctx.ip, "cert_revoke", user=ctx.user, cert_key_ref=c.key_ref, commit=False)
    db.commit()
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
