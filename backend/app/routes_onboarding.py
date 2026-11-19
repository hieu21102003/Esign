"""API Onboarding bắt buộc sau lần đăng nhập đầu tiên (wizard 4 bước)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select, update

from .audit import audit
from .deps import Ctx, get_db, rate_limit, require_mfa_only, user_needs_onboarding
from .models import (
    Certificate,
    OnboardingOtp,
    RecoveryCode,
    StoredFile,
    TermsAcceptance,
    User,
    UserCertificate,
    utcnow,
)
from .notifications import dispatch_emails, send_email_smtp
from .pin import apply_signing_pin, now_from_request
from .pki_helpers import clean_cn, clean_email, next_key_ref
from .routes_auth import _check_locked, _register_failure
from .security import check_signing_pin_policy, normalize_recovery, totp_check
from .services import ca_issue, ca_revoke
from .signature_image import process_signature_image
from .storage import read_bytes, store_bytes
from .terms import load_terms

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def _clean_text(raw: Optional[str], label: str, *, max_len: int = 120) -> str:
    s = _CTRL.sub(" ", (raw or "")).strip()
    if not s:
        raise HTTPException(400, f"{label} không được để trống")
    if len(s) > max_len:
        raise HTTPException(400, f"{label} tối đa {max_len} ký tự")
    return s


def _otp_hmac(settings, user_id: int, code: str) -> str:
    key = hashlib.sha256(b"esign-onb-otp|" + (settings.totp_enc_key or "").encode("utf-8")).digest()
    return hmac.new(key, f"{user_id}:{code}".encode("utf-8"), hashlib.sha256).hexdigest()


def _profile_complete(u: User) -> bool:
    return bool(
        (u.display_name or "").strip()
        and (u.job_title or "").strip()
        and (u.department or "").strip()
        and (u.manager_name or "").strip()
        and (u.email or "").strip()
    )


def _has_sig_image(db, u: User) -> bool:
    if not u.signature_image_file_id:
        return False
    f = db.get(StoredFile, u.signature_image_file_id)
    return f is not None and f.deleted_at is None and f.kind == "image"


def _active_user_cert(db, user_id: int) -> Optional[Certificate]:
    return db.scalar(
        select(Certificate)
        .join(UserCertificate, UserCertificate.cert_id == Certificate.id)
        .where(UserCertificate.user_id == user_id, Certificate.active.is_(True))
        .order_by(Certificate.id)
        .limit(1)
    )


def _status_payload(db, request: Request, ctx: Ctx, u: User) -> dict:
    st = request.app.state.settings
    _, now_dt = now_from_request(request)
    terms = load_terms(st)
    needs_onb, terms_reaccept_only = user_needs_onboarding(u, st)
    has_prof = _profile_complete(u)
    terms_ok = u.accepted_terms_version == terms["version"]
    has_sig = _has_sig_image(db, u)
    has_pin = bool(u.signing_pin_hash)
    cert = _active_user_cert(db, u.id)
    smtp_avail = bool((st.smtp_host or "").strip())
    otp_method = "totp" if u.totp_enabled else ("email_otp" if smtp_avail else "none")
    otp_reuse_valid = bool(
        u.onboarding_otp_verified_at is not None
        and u.onboarding_otp_session_id == ctx.sess.id
        and (now_dt - u.onboarding_otp_verified_at) <= timedelta(minutes=st.onboarding_otp_reuse_minutes)
    )

    if terms_reaccept_only:
        step = 2
    elif not has_prof:
        step = 1
    elif not terms_ok:
        step = 2
    elif not has_sig:
        step = 3
    elif needs_onb:
        step = 4
    else:
        step = 4

    return {
        "onboarding_required": needs_onb,
        "terms_reaccept_only": terms_reaccept_only,
        "current_step": step,
        "saved_step": u.onboarding_step or 1,
        "onboarding_completed_at": u.onboarding_completed_at.isoformat() if u.onboarding_completed_at else None,
        "has_profile": has_prof,
        "profile": {
            "display_name": u.display_name or "",
            "job_title": u.job_title or "",
            "department": u.department or "",
            "manager_name": u.manager_name or "",
            "manager_user_id": u.manager_user_id,
            "email": u.email or "",
        },
        "terms_version": terms["version"],
        "terms_title": terms["title"],
        "terms_sha256": terms["sha256"],
        "accepted_terms_version": u.accepted_terms_version,
        "terms_accepted": terms_ok,
        "otp_method": otp_method,
        "smtp_available": smtp_avail,
        "has_signature_image": has_sig,
        "signature_image_file_id": u.signature_image_file_id if has_sig else None,
        "has_signing_pin": has_pin,
        "has_active_cert": cert is not None,
        "active_cert": (
            {"id": cert.id, "key_ref": cert.key_ref, "serial": cert.serial, "not_after": cert.not_after}
            if cert is not None else None
        ),
        "otp_reuse_valid": otp_reuse_valid,
        "otp_reuse_minutes": st.onboarding_otp_reuse_minutes,
    }


def _verify_otp(
    db,
    request: Request,
    st,
    u: User,
    ip: str,
    otp_code: Optional[str],
    recovery_code: Optional[str],
    *,
    fail_action: str,
    otp_required_header: bool = False,
) -> str:
    hdrs = {"X-Onboarding-Otp-Required": "1"} if otp_required_header else None
    _, now_dt = now_from_request(request)

    if u.totp_enabled:
        if not u.totp_secret_enc:
            raise HTTPException(409, "Tài khoản chưa có khóa TOTP")
        tc = (otp_code or "").strip()
        rc = (recovery_code or "").strip()
        if not tc and not rc:
            raise HTTPException(401 if otp_required_header else 400, "Vui lòng nhập mã TOTP hoặc mã khôi phục", headers=hdrs)
        if tc and rc:
            raise HTTPException(400, "Chỉ cung cấp một trong: mã TOTP hoặc mã khôi phục")
        _check_locked(request, db, u, ip)
        box = request.app.state.box
        ok, method = False, "totp"
        if tc:
            step = totp_check(box.decrypt(u.totp_secret_enc), tc, request.app.state.clock(), u.totp_last_step)
            if step is not None:
                u.totp_last_step, ok = step, True
        else:
            h = box.recovery_hash(rc)
            rec = db.scalar(select(RecoveryCode).where(
                RecoveryCode.user_id == u.id, RecoveryCode.code_hash == h, RecoveryCode.used_at.is_(None),
            ))
            if rec is not None and len(normalize_recovery(rc)) == 10:
                rec.used_at, ok, method = now_dt, True, "recovery"
        if not ok:
            db.rollback()
            _register_failure(db, st, u)
            audit(db, ip, fail_action, user=u, status="denied", detail="bad_totp")
            raise HTTPException(401, "Mã TOTP hoặc mã khôi phục không đúng", headers=hdrs)
        u.failed_attempts, u.locked_until = 0, None
        return method

    if not (st.smtp_host or "").strip():
        raise HTTPException(503, "Chưa cấu hình SMTP hoặc TOTP để xác thực OTP")
    code = re.sub(r"\s+", "", otp_code or "")
    if not code:
        raise HTTPException(401 if otp_required_header else 400, "Vui lòng nhập mã OTP gửi qua email", headers=hdrs)

    row = db.scalar(
        select(OnboardingOtp)
        .where(OnboardingOtp.user_id == u.id, OnboardingOtp.purpose == "terms_accept", OnboardingOtp.used_at.is_(None))
        .order_by(desc(OnboardingOtp.id))
        .limit(1)
    )
    if row is None:
        raise HTTPException(401, "Chưa có mã OTP email còn hiệu lực, vui lòng bấm gửi mã mới", headers=hdrs)
    if row.expires_at <= now_dt:
        row.used_at = now_dt
        db.commit()
        raise HTTPException(401, "Mã OTP email đã hết hạn (5 phút), vui lòng bấm gửi mã mới", headers=hdrs)
    if row.attempts >= st.onboarding_otp_max_attempts:
        row.used_at = now_dt
        db.commit()
        raise HTTPException(423, "Mã OTP đã vượt quá số lần thử sai, vui lòng yêu cầu mã mới", headers=hdrs)

    sent_h = _otp_hmac(st, u.id, code if re.fullmatch(r"\d{6}", code) else "000000-invalid")
    if not (re.fullmatch(r"\d{6}", code) and hmac.compare_digest(sent_h, row.code_hash)):
        row.attempts += 1
        locked_now = row.attempts >= st.onboarding_otp_max_attempts
        if locked_now:
            row.used_at = now_dt
        audit(db, ip, fail_action, user=u, status="denied", detail="bad_email_otp", commit=False)
        db.commit()
        if locked_now:
            raise HTTPException(423, "Nhập sai mã OTP quá số lần cho phép, vui lòng yêu cầu mã mới", headers=hdrs)
        rem = st.onboarding_otp_max_attempts - row.attempts
        raise HTTPException(401, f"Mã OTP email không đúng (còn {rem} lần thử)", headers=hdrs)

    row.used_at = now_dt
    return "email_otp"


class ProfileIn(BaseModel):
    display_name: str = Field(max_length=120)
    job_title: str = Field(max_length=120)
    department: str = Field(max_length=120)
    manager_name: str = Field(max_length=120)
    manager_user_id: Optional[int] = None
    email: str = Field(max_length=255)


class TermsAcceptIn(BaseModel):
    terms_version: str = Field(max_length=64)
    scrolled_to_bottom: bool = False
    agreed: bool = False
    otp_code: Optional[str] = Field(None, max_length=16)
    recovery_code: Optional[str] = Field(None, max_length=32)


class CompleteOnboardingIn(BaseModel):
    new_pin: Optional[str] = None
    otp_code: Optional[str] = Field(None, max_length=16)
    recovery_code: Optional[str] = Field(None, max_length=32)


@router.get("/status")
def get_onboarding_status(request: Request, ctx: Ctx = Depends(require_mfa_only), db=Depends(get_db)):
    u = db.get(User, ctx.user.id)
    if u is None:
        raise HTTPException(401, "Phiên không hợp lệ")
    return _status_payload(db, request, ctx, u)


@router.get("/managers")
def list_manager_suggestions(ctx: Ctx = Depends(require_mfa_only), db=Depends(get_db)):
    rows = db.scalars(
        select(User)
        .where(User.is_active.is_(True), User.id != ctx.user.id)
        .order_by(User.display_name, User.username)
    )
    return [
        {
            "id": r.id,
            "username": r.username,
            "display_name": r.display_name or "",
            "job_title": r.job_title or "",
            "department": r.department or "",
        }
        for r in rows
    ]


@router.post("/profile")
def save_onboarding_profile(
    body: ProfileIn, request: Request, ctx: Ctx = Depends(require_mfa_only), db=Depends(get_db)
):
    u = db.get(User, ctx.user.id)
    if u is None or not u.is_active:
        raise HTTPException(401, "Phiên không hợp lệ")

    display_name = clean_cn(_clean_text(body.display_name, "Họ tên"))
    job_title = _clean_text(body.job_title, "Chức danh")
    department = _clean_text(body.department, "Phòng ban")
    manager_name = _clean_text(body.manager_name, "Quản lý trực tiếp")
    email = clean_email(body.email)
    if not email:
        raise HTTPException(400, "Email không được để trống")

    dup = db.scalar(
        select(User.id).where(func.lower(User.email) == email.lower(), User.id != u.id).limit(1)
    )
    if dup is not None:
        raise HTTPException(409, "Email này đã được sử dụng bởi tài khoản khác")

    mgr_id: Optional[int] = None
    if body.manager_user_id is not None:
        if body.manager_user_id == u.id:
            raise HTTPException(400, "Không thể chọn chính mình làm quản lý trực tiếp")
        mgr = db.get(User, body.manager_user_id)
        if mgr is None or not mgr.is_active:
            raise HTTPException(400, "Quản lý trực tiếp được chọn không hợp lệ")
        mgr_id = mgr.id

    u.display_name = display_name
    u.job_title = job_title
    u.department = department
    u.manager_name = manager_name
    u.manager_user_id = mgr_id
    u.email = email
    if u.onboarding_completed_at is None:
        u.onboarding_step = max(u.onboarding_step or 1, 2)

    audit(
        db, ctx.ip, "onboarding_step1_profile", user=u,
        detail=f"dept={department} title={job_title}", commit=False,
    )
    db.commit()
    return _status_payload(db, request, ctx, u)


@router.get("/terms")
def get_onboarding_terms(request: Request, ctx: Ctx = Depends(require_mfa_only)):  # noqa: ARG001
    return load_terms(request.app.state.settings)


@router.post("/terms/send-otp")
def send_onboarding_email_otp(request: Request, ctx: Ctx = Depends(require_mfa_only), db=Depends(get_db)):
    st = request.app.state.settings
    u = db.get(User, ctx.user.id)
    if u is None or not u.is_active:
        raise HTTPException(401, "Phiên không hợp lệ")
    if u.totp_enabled:
        raise HTTPException(400, "Tài khoản đang bật TOTP, vui lòng dùng mã từ ứng dụng xác thực")
    if not (st.smtp_host or "").strip():
        raise HTTPException(503, "Chưa cấu hình SMTP để gửi OTP qua email")
    if not u.email:
        raise HTTPException(400, "Vui lòng lưu email ở Bước 1 trước khi gửi OTP")

    rate_limit(request, "onb-send-otp", str(u.id), st.login_rate)
    _, now_dt = now_from_request(request)
    db.execute(
        update(OnboardingOtp)
        .where(OnboardingOtp.user_id == u.id, OnboardingOtp.purpose == "terms_accept", OnboardingOtp.used_at.is_(None))
        .values(used_at=now_dt)
    )
    code = f"{secrets.randbelow(1_000_000):06d}"
    db.add(OnboardingOtp(
        user_id=u.id,
        purpose="terms_accept",
        code_hash=_otp_hmac(st, u.id, code),
        expires_at=now_dt + timedelta(minutes=st.onboarding_otp_ttl_minutes),
        attempts=0,
        created_at=now_dt,
    ))
    sent = send_email_smtp(
        st,
        u.email,
        "[Ký số] Mã OTP xác nhận điều khoản chữ ký số nội bộ",
        f"Xin chào {u.display_name or u.username},\n\n"
        f"Mã OTP xác nhận của bạn là: {code}\n"
        f"Mã có hiệu lực trong {st.onboarding_otp_ttl_minutes} phút và chỉ dùng một lần.\n",
    )
    if not sent:
        db.rollback()
        raise HTTPException(502, "Không gửi được email OTP, vui lòng kiểm tra lại cấu hình SMTP")
    audit(db, ctx.ip, "onboarding_email_otp_sent", user=u, commit=False)
    db.commit()
    return {"ok": True, "expires_in_minutes": st.onboarding_otp_ttl_minutes}


@router.post("/terms/accept")
def accept_onboarding_terms(
    body: TermsAcceptIn, request: Request, ctx: Ctx = Depends(require_mfa_only), db=Depends(get_db)
):
    st = request.app.state.settings
    u = db.get(User, ctx.user.id)
    if u is None or not u.is_active:
        raise HTTPException(401, "Phiên không hợp lệ")
    rate_limit(request, "onb-terms", str(u.id), st.login_rate)

    if u.onboarding_completed_at is None and not _profile_complete(u):
        raise HTTPException(400, "Vui lòng hoàn tất khai báo thông tin cá nhân (Bước 1) trước")

    terms = load_terms(st)
    if body.terms_version != terms["version"]:
        raise HTTPException(409, "Phiên bản điều khoản đã thay đổi, vui lòng tải lại và đọc bản mới nhất")
    if not body.scrolled_to_bottom or not body.agreed:
        raise HTTPException(400, "Vui lòng cuộn đọc hết điều khoản và đánh dấu đồng ý")

    otp_method = _verify_otp(
        db, request, st, u, ctx.ip, body.otp_code, body.recovery_code, fail_action="onboarding_terms_denied"
    )
    _, now_dt = now_from_request(request)
    ua = (request.headers.get("user-agent") or "")[:500]
    db.add(TermsAcceptance(
        user_id=u.id,
        username=u.username,
        terms_version=terms["version"],
        terms_sha256=terms["sha256"],
        accepted_at=now_dt,
        ip=ctx.ip,
        user_agent=ua,
        otp_method=otp_method,
    ))
    u.accepted_terms_version = terms["version"]
    if u.onboarding_completed_at is None:
        u.onboarding_otp_verified_at = now_dt
        u.onboarding_otp_session_id = ctx.sess.id
        u.onboarding_step = max(u.onboarding_step or 1, 3)
    else:
        u.onboarding_otp_verified_at = None
        u.onboarding_otp_session_id = None

    audit(
        db, ctx.ip, "onboarding_terms_accepted", user=u,
        detail=f"version={terms['version']} method={otp_method}",
        metadata={"terms_version": terms["version"], "terms_sha256": terms["sha256"], "otp_method": otp_method},
        commit=False,
    )
    db.commit()
    return _status_payload(db, request, ctx, u)


@router.post("/signature-image/preview")
def preview_signature_image(
    request: Request,
    file: UploadFile = File(...),
    remove_bg: bool = Form(True),
    sensitivity: int = Form(55),
    rotate: int = Form(0),
    ctx: Ctx = Depends(require_mfa_only),
):
    st = request.app.state.settings
    rate_limit(request, "upload", str(ctx.user.id), st.upload_rate)
    data = file.file.read(st.max_image_bytes + 1)
    png_bytes, width, height = process_signature_image(
        data, st.max_image_bytes, remove_bg=remove_bg, sensitivity=sensitivity, rotate=rotate,
    )
    return {
        "preview_data_url": "data:image/png;base64," + base64.b64encode(png_bytes).decode(),
        "width": width,
        "height": height,
        "size": len(png_bytes),
    }


@router.post("/signature-image/save")
def save_signature_image(
    request: Request,
    file: UploadFile = File(...),
    remove_bg: bool = Form(True),
    sensitivity: int = Form(55),
    rotate: int = Form(0),
    ctx: Ctx = Depends(require_mfa_only),
    db=Depends(get_db),
):
    st = request.app.state.settings
    rate_limit(request, "upload", str(ctx.user.id), st.upload_rate)
    u = db.get(User, ctx.user.id)
    if u is None or not u.is_active:
        raise HTTPException(401, "Phiên không hợp lệ")
    if not _profile_complete(u) or u.accepted_terms_version != load_terms(st)["version"]:
        raise HTTPException(400, "Vui lòng hoàn tất Bước 1 và Bước 2 trước")

    data = file.file.read(st.max_image_bytes + 1)
    png_bytes, width, height = process_signature_image(
        data, st.max_image_bytes, remove_bg=remove_bg, sensitivity=sensitivity, rotate=rotate,
    )
    stored = store_bytes(db, st, u, png_bytes, kind="image", name="signature.png", commit=False)
    u.signature_image_file_id = stored.id
    if u.onboarding_completed_at is None:
        u.onboarding_step = max(u.onboarding_step or 1, 4)

    audit(
        db, ctx.ip, "onboarding_step3_signature", user=u,
        file_id=stored.id, file_sha256=stored.sha256,
        detail=f"{width}x{height} remove_bg={remove_bg} rotate={rotate}", commit=False,
    )
    db.commit()
    return {
        "file_id": stored.id,
        "width": width,
        "height": height,
        "size": stored.size,
        "status": _status_payload(db, request, ctx, u),
    }


@router.get("/signature-image")
def get_my_onboarding_signature_image(
    request: Request, ctx: Ctx = Depends(require_mfa_only), db=Depends(get_db)
):
    u = db.get(User, ctx.user.id)
    if u is None or not u.signature_image_file_id:
        raise HTTPException(404, "Chưa có ảnh chữ ký")
    f = db.get(StoredFile, u.signature_image_file_id)
    if f is None or f.deleted_at is not None or f.kind != "image":
        raise HTTPException(404, "Chưa có ảnh chữ ký")
    data = read_bytes(request.app.state.settings, f)
    return Response(data, media_type="image/png", headers={"X-Content-Type-Options": "nosniff"})


@router.post("/complete")
def complete_onboarding(
    body: CompleteOnboardingIn, request: Request, ctx: Ctx = Depends(require_mfa_only), db=Depends(get_db)
):
    st = request.app.state.settings
    u = db.get(User, ctx.user.id)
    if u is None or not u.is_active:
        raise HTTPException(401, "Phiên không hợp lệ")
    rate_limit(request, "onb-complete", str(u.id), st.login_rate)

    terms = load_terms(st)
    if not _profile_complete(u):
        raise HTTPException(400, "Chưa hoàn tất thông tin cá nhân (Bước 1)")
    if u.accepted_terms_version != terms["version"]:
        raise HTTPException(400, "Chưa xác nhận điều khoản phiên bản hiện tại (Bước 2)")
    if not _has_sig_image(db, u):
        raise HTTPException(400, "Chưa chọn và lưu ảnh chữ ký (Bước 3)")

    _, now_dt = now_from_request(request)
    need_pin = not bool(u.signing_pin_hash)
    otp_via = "existing_pin"

    if need_pin:
        reuse_valid = (
            u.onboarding_otp_verified_at is not None
            and u.onboarding_otp_session_id == ctx.sess.id
            and (now_dt - u.onboarding_otp_verified_at) <= timedelta(minutes=st.onboarding_otp_reuse_minutes)
        )
        has_explicit_otp = bool((body.otp_code or "").strip() or (body.recovery_code or "").strip())
        if reuse_valid and not has_explicit_otp:
            otp_via = "onboarding_otp_reuse"
        else:
            otp_via = _verify_otp(
                db, request, st, u, ctx.ip, body.otp_code, body.recovery_code,
                fail_action="pin_change_denied", otp_required_header=True,
            )

        err = check_signing_pin_policy(body.new_pin, u.password_hash)
        if err:
            db.rollback()
            raise HTTPException(400, err)

    existing_cert = _active_user_cert(db, u.id)
    issued_cert_info = None
    issued_key_ref: Optional[str] = None
    if existing_cert is None:
        cn = clean_cn(u.display_name or u.username)
        issued_key_ref = next_key_ref(db, u.username)
        issued_cert_info = ca_issue(st, issued_key_ref, cn, 365)

    new_cert: Optional[Certificate] = None
    try:
        if need_pin:
            apply_signing_pin(u, body.new_pin, now_dt)
            audit(db, ctx.ip, "pin_set", user=u, detail=f"via={otp_via}", commit=False)

        if issued_cert_info is not None and issued_key_ref is not None:
            new_cert = Certificate(
                key_ref=issued_key_ref,
                subject=issued_cert_info["subject"][:255],
                serial=issued_cert_info["serial"],
                not_after=issued_cert_info["not_after"],
            )
            db.add(new_cert)
            db.flush()
            db.add(UserCertificate(user_id=u.id, cert_id=new_cert.id))
            audit(db, ctx.ip, "cert_issue", user=u, cert_key_ref=new_cert.key_ref,
                  detail=f"target={u.username} source=onboarding days=365", commit=False)
            audit(db, ctx.ip, "cert_assign", user=u,
                  detail=f"target={u.username} certs={[new_cert.id]} source=onboarding", commit=False)

        u.onboarding_completed_at = now_dt
        u.onboarding_step = 4
        u.onboarding_otp_verified_at = None
        u.onboarding_otp_session_id = None
        final_cert = new_cert or existing_cert
        audit(
            db, ctx.ip, "onboarding_completed", user=u,
            cert_key_ref=final_cert.key_ref if final_cert else None,
            detail=f"pin_created={need_pin} cert_issued={new_cert is not None}", commit=False,
        )
        db.commit()
    except Exception:
        db.rollback()
        if issued_key_ref is not None:
            try:
                ca_revoke(st, issued_key_ref)
            except Exception:
                pass
        raise

    if u.email and final_cert is not None:
        msg_body = (
            f"Xin chào {u.display_name or u.username},\n\n"
            f"Tài khoản chữ ký số nội bộ của bạn đã được kích hoạt thành công.\n"
            f"- Họ tên: {u.display_name}\n"
            f"- Chức danh / Phòng ban: {u.job_title} / {u.department}\n"
            f"- Thời điểm hoàn tất: {now_dt.isoformat()} UTC\n"
            f"- Mã chứng thư: {final_cert.key_ref}\n"
            f"- Serial: {final_cert.serial}\n"
            f"- Hiệu lực đến: {final_cert.not_after}\n"
        )
        dispatch_emails(st, [(u.email, "[Ký số] Kích hoạt chữ ký số nội bộ thành công", msg_body)])

    return {
        "ok": True,
        "pin_created": need_pin,
        "cert_issued": new_cert is not None,
        "certificate": (
            {"id": final_cert.id, "key_ref": final_cert.key_ref, "serial": final_cert.serial, "not_after": final_cert.not_after}
            if final_cert is not None else None
        ),
        "status": _status_payload(db, request, ctx, u),
    }
