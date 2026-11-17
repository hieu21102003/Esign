"""Dependency dùng chung: DB session, IP client, rate limit, xác thực phiên, CSRF, phân quyền."""
from __future__ import annotations

import hmac
import ipaddress
import socket
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select

from .models import SessionRow, User, utcnow
from .security import token_id

COOKIE = "esign_sid"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def get_settings(request: Request):
    return request.app.state.settings


def get_db(request: Request):
    db = request.app.state.session_factory()
    try:
        yield db
    finally:
        db.close()


def _is_trusted_proxy(peer_ip: str, trusted: list[str]) -> bool:
    if peer_ip in trusted:
        return True
    try:
        addr = ipaddress.ip_address(peer_ip)
    except ValueError:
        return False
    for entry in trusted:
        try:
            if "/" in entry:
                if addr in ipaddress.ip_network(entry, strict=False):
                    return True
            else:
                try:
                    if addr == ipaddress.ip_address(entry):
                        return True
                except ValueError:
                    _, _, ips = socket.gethostbyname_ex(entry)
                    if peer_ip in ips:
                        return True
        except OSError:
            continue
    return False


def client_ip(request: Request) -> str:
    st = request.app.state.settings
    peer = (request.client.host if request.client else "unknown")[:64]
    if st.trust_proxy and _is_trusted_proxy(peer, st.trusted_proxies):
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            parts = [p.strip() for p in xff.split(",") if p.strip()]
            for candidate in reversed(parts):
                if not _is_trusted_proxy(candidate, st.trusted_proxies):
                    return candidate[:64]
            return parts[-1][:64]
    return peer


def rate_limit(request: Request, name: str, key: str, rate: tuple) -> None:
    limit, window = rate
    wait = request.app.state.limiter.check(f"{name}:{key}", limit, window)
    if wait is not None:
        raise HTTPException(429, "Quá nhiều yêu cầu, vui lòng thử lại sau", headers={"Retry-After": str(wait)})


@dataclass
class Ctx:
    user: User
    sess: SessionRow
    ip: str


def require_session(request: Request, db=Depends(get_db)) -> Ctx:
    """Cần phiên hợp lệ (có thể chưa qua 2FA). Với method thay đổi dữ liệu: bắt buộc CSRF token."""
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Chưa đăng nhập")
    sess = db.get(SessionRow, token_id(token))
    if sess is None or sess.expires_at <= utcnow():
        if sess is not None:
            db.delete(sess)
            db.commit()
        raise HTTPException(401, "Phiên đã hết hạn")
    user = db.get(User, sess.user_id)
    if user is None or not user.is_active:
        raise HTTPException(401, "Tài khoản không hợp lệ")
    if request.method not in SAFE_METHODS:
        sent = request.headers.get("x-csrf-token", "")
        if not sent or not hmac.compare_digest(sent.encode(), sess.csrf_token.encode()):
            raise HTTPException(403, "CSRF token không hợp lệ")
    return Ctx(user=user, sess=sess, ip=client_ip(request))


def require_mfa(ctx: Ctx = Depends(require_session)) -> Ctx:
    """Đã đăng nhập VÀ đã qua 2FA trong phiên này."""
    if not ctx.sess.mfa_verified:
        raise HTTPException(403, "Cần xác thực 2FA")
    return ctx


def require_admin(ctx: Ctx = Depends(require_mfa)) -> Ctx:
    if ctx.user.role != "admin":
        raise HTTPException(403, "Chỉ quản trị viên")
    return ctx


def find_user(db, username: str):
    return db.scalar(select(User).where(User.username == username.strip().lower()))
