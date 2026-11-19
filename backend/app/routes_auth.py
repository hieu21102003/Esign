"""Đăng nhập, TOTP 2FA bắt buộc, mã khôi phục, phiên cookie httpOnly + CSRF."""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from .audit import audit
from .deps import COOKIE, Ctx, find_user, get_db, rate_limit, require_mfa, require_session, client_ip
from .models import RecoveryCode, SessionRow, User, utcnow
from .pin import now_from_request
from .security import (DUMMY_HASH, check_password_policy, check_signing_pin_policy, hash_password,
                       new_recovery_codes, new_token, normalize_recovery, token_id, totp_check,
                       totp_new_secret, totp_uri, verify_password)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class CodeIn(BaseModel):
    code: str = Field(min_length=1, max_length=16)


class VerifyIn(BaseModel):
    code: Optional[str] = Field(None, max_length=16)
    recovery_code: Optional[str] = Field(None, max_length=32)


class PasswordIn(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


class SigningPinSetIn(BaseModel):
    """Đặt/đổi PIN ký. Không dùng validator Pydantic để tránh lỗi 422 lộ ngược giá trị PIN."""
    new_pin: Optional[str] = None
    totp_code: Optional[str] = Field(None, max_length=16)
    recovery_code: Optional[str] = Field(None, max_length=32)


def _pin_status_out(u: User, now_dt) -> dict:
    pin_locked = bool(u.pin_locked_until and u.pin_locked_until > now_dt)
    return {
        "has_signing_pin": bool(u.signing_pin_hash),
        "pin_set_at": u.pin_set_at.isoformat() if u.pin_set_at else None,
        "pin_locked": pin_locked,
        "pin_locked_until": u.pin_locked_until.isoformat() if pin_locked and u.pin_locked_until else None,
    }


def _issue_session(request: Request, response: Response, db, user: User, mfa: bool) -> SessionRow:
    st = request.app.state.settings
    token = new_token()
    row = SessionRow(id=token_id(token), user_id=user.id, csrf_token=new_token()[:40], mfa_verified=mfa,
                     expires_at=utcnow() + timedelta(minutes=st.session_ttl_minutes), ip=client_ip(request))
    db.add(row)
    db.commit()
    response.set_cookie(COOKIE, token, max_age=st.session_ttl_minutes * 60, httponly=True,
                        secure=st.cookie_secure, samesite="strict", path="/")
    return row


def _rotate(request: Request, response: Response, db, ctx: Ctx, mfa: bool) -> SessionRow:
    """Cấp phiên mới (chống session fixation) sau khi qua 2FA, hủy phiên cũ."""
    db.delete(ctx.sess)
    db.commit()
    return _issue_session(request, response, db, ctx.user, mfa)


def _register_failure(db, st, user: User) -> None:
    user.failed_attempts += 1
    if user.failed_attempts >= st.max_failed_logins:
        user.locked_until = utcnow() + timedelta(minutes=st.lockout_minutes)
        user.failed_attempts = 0
    db.commit()


def _check_locked(request: Request, db, user: User, ip: str) -> None:
    if user.locked_until and user.locked_until > utcnow():
        wait = int((user.locked_until - utcnow()).total_seconds()) + 1
        audit(db, ip, "login_locked", user=user, status="denied")
        raise HTTPException(423, "Tài khoản tạm thời bị khóa do nhập sai nhiều lần",
                            headers={"Retry-After": str(wait)})


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db=Depends(get_db)):
    st = request.app.state.settings
    ip = client_ip(request)
    rate_limit(request, "login-ip", ip, st.login_rate)
    rate_limit(request, "login-user", body.username.strip().lower(), st.login_rate)
    user = find_user(db, body.username)
    if user is None or not user.is_active:
        verify_password(DUMMY_HASH, body.password)  # đồng đều thời gian, không lộ user tồn tại
        audit(db, ip, "login_failed", username=body.username.strip().lower()[:64], status="denied")
        raise HTTPException(401, "Sai tài khoản hoặc mật khẩu")
    _check_locked(request, db, user, ip)
    if not verify_password(user.password_hash, body.password):
        _register_failure(db, st, user)
        audit(db, ip, "login_failed", user=user, status="denied")
        raise HTTPException(401, "Sai tài khoản hoặc mật khẩu")
    sess = _issue_session(request, response, db, user, mfa=False)
    audit(db, ip, "login_password_ok", user=user)
    return {"mfa_required": True, "totp_enrolled": user.totp_enabled, "csrf_token": sess.csrf_token}


@router.get("/me")
def me(request: Request, ctx: Ctx = Depends(require_session)):
    u = ctx.user
    _, now_dt = now_from_request(request)
    return {"id": u.id, "username": u.username, "display_name": u.display_name, "role": u.role,
            "mfa_verified": ctx.sess.mfa_verified, "totp_enrolled": u.totp_enabled,
            "csrf_token": ctx.sess.csrf_token, **_pin_status_out(u, now_dt)}


@router.post("/logout")
def logout(request: Request, response: Response, ctx: Ctx = Depends(require_session), db=Depends(get_db)):
    db.delete(ctx.sess)
    db.commit()
    response.delete_cookie(COOKIE, path="/")
    audit(db, ctx.ip, "logout", user=ctx.user)
    return {"ok": True}


@router.post("/totp/setup")
def totp_setup(request: Request, ctx: Ctx = Depends(require_session), db=Depends(get_db)):
    """Bắt đầu đăng ký TOTP (lần đăng nhập đầu). Secret được mã hóa trước khi lưu."""
    u = ctx.user
    if u.totp_enabled:
        raise HTTPException(409, "TOTP đã được đăng ký")
    st = request.app.state.settings
    secret = totp_new_secret()
    u.totp_secret_enc = request.app.state.box.encrypt(secret)
    db.commit()
    audit(db, ctx.ip, "totp_setup", user=u)
    return {"secret": secret, "otpauth_uri": totp_uri(secret, u.username, st.totp_issuer)}


@router.post("/totp/confirm")
def totp_confirm(body: CodeIn, request: Request, response: Response, ctx: Ctx = Depends(require_session),
                 db=Depends(get_db)):
    u, st = ctx.user, request.app.state.settings
    if u.totp_enabled:
        raise HTTPException(409, "TOTP đã được đăng ký")
    if not u.totp_secret_enc:
        raise HTTPException(400, "Chưa khởi tạo TOTP")
    _check_locked(request, db, u, ctx.ip)
    secret = request.app.state.box.decrypt(u.totp_secret_enc)
    step = totp_check(secret, body.code, request.app.state.clock(), u.totp_last_step)
    if step is None:
        _register_failure(db, st, u)
        audit(db, ctx.ip, "totp_failed", user=u, status="denied")
        raise HTTPException(401, "Mã TOTP không đúng")
    u.totp_enabled, u.totp_last_step, u.failed_attempts, u.locked_until = True, step, 0, None
    box = request.app.state.box
    codes = new_recovery_codes()
    db.query(RecoveryCode).filter(RecoveryCode.user_id == u.id).delete()
    for c in codes:
        db.add(RecoveryCode(user_id=u.id, code_hash=box.recovery_hash(c)))
    db.commit()
    new = _rotate(request, response, db, ctx, mfa=True)
    audit(db, ctx.ip, "totp_enabled", user=u)
    return {"recovery_codes": codes, "csrf_token": new.csrf_token}


@router.post("/totp/verify")
def totp_verify(body: VerifyIn, request: Request, response: Response, ctx: Ctx = Depends(require_session),
                db=Depends(get_db)):
    """Xác thực bước 2 bằng mã TOTP hoặc mã khôi phục dùng một lần."""
    u, st = ctx.user, request.app.state.settings
    if not u.totp_enabled:
        raise HTTPException(409, "Chưa đăng ký TOTP")
    if bool(body.code) == bool(body.recovery_code):
        raise HTTPException(400, "Cung cấp đúng một trong: code, recovery_code")
    _check_locked(request, db, u, ctx.ip)
    box = request.app.state.box
    ok, action = False, "totp_verified"
    if body.code:
        step = totp_check(box.decrypt(u.totp_secret_enc), body.code, request.app.state.clock(), u.totp_last_step)
        if step is not None:
            u.totp_last_step, ok = step, True
    else:
        h = box.recovery_hash(body.recovery_code)
        row = db.scalar(select(RecoveryCode).where(RecoveryCode.user_id == u.id, RecoveryCode.code_hash == h,
                                                   RecoveryCode.used_at.is_(None)))
        if row is not None and len(normalize_recovery(body.recovery_code)) == 10:
            row.used_at, ok, action = utcnow(), True, "recovery_code_used"
    if not ok:
        db.rollback()
        _register_failure(db, st, u)
        audit(db, ctx.ip, "totp_failed", user=u, status="denied")
        raise HTTPException(401, "Mã xác thực không đúng")
    u.failed_attempts, u.locked_until = 0, None
    db.commit()
    new = _rotate(request, response, db, ctx, mfa=True)
    audit(db, ctx.ip, action, user=u)
    return {"ok": True, "csrf_token": new.csrf_token}


@router.post("/password")
def change_password(body: PasswordIn, request: Request, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    st = request.app.state.settings
    if not verify_password(ctx.user.password_hash, body.current_password):
        audit(db, ctx.ip, "password_change", user=ctx.user, status="denied")
        raise HTTPException(401, "Mật khẩu hiện tại không đúng")
    err = check_password_policy(body.new_password, st.min_password_length)
    if err:
        raise HTTPException(400, err)
    ctx.user.password_hash = hash_password(body.new_password)
    db.query(SessionRow).filter(SessionRow.user_id == ctx.user.id, SessionRow.id != ctx.sess.id).delete()
    db.commit()
    audit(db, ctx.ip, "password_change", user=ctx.user)
    return {"ok": True}


@router.get("/signing-pin")
def get_signing_pin_status(request: Request, ctx: Ctx = Depends(require_mfa)):
    _, now_dt = now_from_request(request)
    return _pin_status_out(ctx.user, now_dt)


@router.post("/signing-pin")
def set_signing_pin(
    body: SigningPinSetIn, request: Request, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)
):
    """Đặt lần đầu / đổi / đặt lại PIN ký 6 chữ số. Bắt buộc xác nhận bằng TOTP hoặc recovery code."""
    st = request.app.state.settings
    u = db.get(User, ctx.user.id)
    if u is None or not u.is_active:
        raise HTTPException(401, "Phiên không hợp lệ")
    rate_limit(request, "pin-set", str(u.id), st.login_rate)

    if not u.totp_enabled or not u.totp_secret_enc:
        raise HTTPException(409, "Tài khoản chưa kích hoạt TOTP 2FA")

    totp_code = (body.totp_code or "").strip()
    recovery_code = (body.recovery_code or "").strip()
    if bool(totp_code) == bool(recovery_code):
        raise HTTPException(400, "Cung cấp đúng một trong: mã TOTP hoặc mã khôi phục")

    _check_locked(request, db, u, ctx.ip)
    box = request.app.state.box
    ok, used_recovery = False, False
    if totp_code:
        step = totp_check(box.decrypt(u.totp_secret_enc), totp_code, request.app.state.clock(), u.totp_last_step)
        if step is not None:
            u.totp_last_step, ok = step, True
    else:
        h = box.recovery_hash(recovery_code)
        row = db.scalar(select(RecoveryCode).where(
            RecoveryCode.user_id == u.id, RecoveryCode.code_hash == h, RecoveryCode.used_at.is_(None),
        ))
        if row is not None and len(normalize_recovery(recovery_code)) == 10:
            row.used_at, ok, used_recovery = utcnow(), True, True

    if not ok:
        db.rollback()
        _register_failure(db, st, u)
        audit(db, ctx.ip, "pin_change_denied", user=u, status="denied", detail="bad_mfa")
        raise HTTPException(401, "Mã TOTP hoặc mã khôi phục không đúng")

    err = check_signing_pin_policy(body.new_pin, u.password_hash)
    if err:
        db.rollback()
        raise HTTPException(400, err)

    _, now_dt = now_from_request(request)
    had_pin = bool(u.signing_pin_hash)
    u.failed_attempts, u.locked_until = 0, None
    u.signing_pin_hash = hash_password(body.new_pin)
    u.pin_set_at = now_dt
    if not (u.pin_locked_until and u.pin_locked_until > now_dt):
        u.pin_failed_attempts = 0
        u.pin_fail_times_json = "[]"
        u.pin_locked_until = None

    audit(
        db, ctx.ip, "pin_change" if had_pin else "pin_set", user=u,
        detail=f"via={'recovery' if used_recovery else 'totp'}", commit=False,
    )
    db.commit()
    return {"ok": True, **_pin_status_out(u, now_dt)}
