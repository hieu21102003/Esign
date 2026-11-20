"""Thông báo trong ứng dụng & gửi email SMTP tùy chọn (tự động bỏ qua khi chưa cấu hình SMTP)."""
from __future__ import annotations

import logging
import re
import smtplib
import socket
import ssl
import threading
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Iterable, Optional
from urllib.parse import urlsplit

from sqlalchemy import select

from .models import Notification, User, utcnow

log = logging.getLogger("esign.notifications")

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


@dataclass(frozen=True)
class SmtpSendResult:
    ok: bool
    error_code: str = ""
    error_message: str = ""


def build_smtp_ssl_context() -> ssl.SSLContext:
    """Tạo SSLContext cho SMTP với xác minh chuỗi chứng chỉ và hostname đầy đủ."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def normalize_app_base_url(raw: str) -> str:
    """Chuẩn hóa APP_BASE_URL từ cấu hình; chỉ chấp nhận URL http/https hợp lệ."""
    s = (raw or "").strip()
    if not s or any(ch.isspace() or ord(ch) < 32 for ch in s):
        return ""
    parts = urlsplit(s)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return ""
    if not parts.netloc or parts.username is not None or parts.password is not None:
        return ""
    if parts.query or parts.fragment:
        return ""
    path = parts.path.rstrip("/")
    return f"{scheme}://{parts.netloc}{path}"


def document_link(settings, document_id: Optional[str]) -> str:
    """Tạo link mở thẳng tài liệu từ APP_BASE_URL cấu hình (không dùng Host header)."""
    if not document_id or not _UUID_RE.match(document_id):
        return ""
    base = normalize_app_base_url(getattr(settings, "app_base_url", "") or "")
    if not base:
        return ""
    return f"{base}/documents/{document_id}"


def smtp_status_info(settings) -> dict:
    """Trả thông tin trạng thái cấu hình SMTP (không bao giờ trả mật khẩu)."""
    host = (getattr(settings, "smtp_host", "") or "").strip()
    port = int(getattr(settings, "smtp_port", 587) or 587)
    user = (getattr(settings, "smtp_user", "") or "").strip()
    pw = getattr(settings, "smtp_pass", "") or ""
    sender = (getattr(settings, "smtp_from", "") or user or ("no-reply@esign.local" if host else "")).strip()
    use_tls = bool(getattr(settings, "smtp_tls", True))
    if not host:
        tls_mode = "none"
    elif port == 465:
        tls_mode = "SSL/TLS (cổng 465)"
    elif use_tls:
        tls_mode = "STARTTLS"
    else:
        tls_mode = "Không mã hóa (plain)"
    return {
        "enabled": bool(host),
        "host": host,
        "port": port,
        "tls_mode": tls_mode,
        "smtp_tls": use_tls,
        "from_email": sender,
        "password_set": bool(pw),
        "app_base_url_configured": bool(normalize_app_base_url(getattr(settings, "app_base_url", "") or "")),
    }


def _classify_smtp_exception(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return (
            "timeout",
            "Hết thời gian chờ kết nối máy chủ SMTP. Kiểm tra lại host, cổng và tường lửa outbound (cổng 587/465).",
        )
    if isinstance(exc, ssl.SSLCertVerificationError):
        return (
            "tls_error",
            "Lỗi xác minh chứng chỉ TLS: chuỗi chứng chỉ không tin cậy hoặc tên host không khớp chứng chỉ "
            "(gợi ý: tên host phải trùng tên trong chứng chỉ TLS của máy chủ SMTP).",
        )
    if isinstance(exc, ssl.SSLError):
        return (
            "tls_error",
            "Lỗi kết nối TLS với máy chủ SMTP: bắt tay TLS thất bại hoặc sai cổng/kiểu mã hóa "
            "(gợi ý: tên host phải trùng tên trong chứng chỉ và dùng cổng 587 STARTTLS hoặc 465 SSL).",
        )
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return (
            "tls_error",
            "Máy chủ SMTP không hỗ trợ STARTTLS ở cổng này "
            "(gợi ý: kiểm tra cổng 587/STARTTLS hoặc 465/SSL và tên host phải trùng tên trong chứng chỉ).",
        )
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return (
            "auth_error",
            "Xác thực SMTP thất bại: sai tài khoản hoặc mật khẩu SMTP (hoặc hộp thư chưa bật quyền gửi SMTP).",
        )
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return (
            "sender_refused",
            "Máy chủ SMTP từ chối địa chỉ người gửi (SMTP_FROM). Kiểm tra địa chỉ gửi phải khớp tài khoản SMTP được cấp phép.",
        )
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return (
            "recipient_refused",
            "Máy chủ SMTP từ chối địa chỉ người nhận. Kiểm tra lại địa chỉ email nhận hoặc quyền gửi của tài khoản.",
        )
    if isinstance(exc, smtplib.SMTPDataError):
        return (
            "rejected",
            "Máy chủ SMTP từ chối gửi thư (kiểm tra lại địa chỉ người gửi/người nhận hoặc hạn mức hộp thư).",
        )
    if isinstance(
        exc,
        (
            smtplib.SMTPConnectError,
            smtplib.SMTPServerDisconnected,
            socket.gaierror,
            ConnectionRefusedError,
            ConnectionResetError,
            OSError,
        ),
    ):
        return (
            "connection_error",
            "Không kết nối được tới máy chủ SMTP. Kiểm tra lại tên host, cổng kết nối và tường lửa outbound.",
        )
    if isinstance(exc, smtplib.SMTPException):
        return (
            "smtp_error",
            "Lỗi giao thức SMTP khi gửi email. Vui lòng kiểm tra lại cấu hình máy chủ gửi thư.",
        )
    return (
        "smtp_error",
        "Gửi email thất bại do lỗi không xác định ở dịch vụ SMTP.",
    )


def send_email_smtp_detailed(settings, to_email: str, subject: str, body: str) -> SmtpSendResult:
    """Gửi một email qua SMTP với xác minh chứng chỉ TLS đầy đủ và phân loại lỗi an toàn."""
    host = (getattr(settings, "smtp_host", "") or "").strip()
    if not host:
        return SmtpSendResult(False, "not_configured", "Chưa cấu hình máy chủ SMTP (SMTP_HOST đang trống).")
    to_clean = (to_email or "").strip()
    if not to_clean or "\r" in to_email or "\n" in to_email:
        return SmtpSendResult(False, "invalid_recipient", "Địa chỉ email người nhận không hợp lệ.")

    port = int(getattr(settings, "smtp_port", 587) or 587)
    user = (getattr(settings, "smtp_user", "") or "").strip()
    pw = getattr(settings, "smtp_pass", "") or ""
    sender = (getattr(settings, "smtp_from", "") or user or "no-reply@esign.local").strip().replace("\r", "").replace("\n", "")
    use_tls = bool(getattr(settings, "smtp_tls", True))
    clean_subj = " ".join((subject or "").replace("\r", "\n").splitlines()).strip()[:200]

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to_clean
    msg["Subject"] = clean_subj
    msg.set_content(body or "", charset="utf-8")

    ssl_ctx = build_smtp_ssl_context()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=10, context=ssl_ctx) as srv:
                if user:
                    srv.login(user, pw)
                srv.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=10) as srv:
                srv.ehlo()
                if use_tls:
                    srv.starttls(context=ssl_ctx)
                    srv.ehlo()
                if user:
                    srv.login(user, pw)
                srv.send_message(msg)
        return SmtpSendResult(True)
    except Exception as exc:  # không làm gián đoạn luồng ký và không ghi mật khẩu hay phản hồi thô vào log
        code, message = _classify_smtp_exception(exc)
        log.warning("Gửi email SMTP thất bại tới %s: code=%s exc=%s", to_clean, code, type(exc).__name__)
        return SmtpSendResult(False, code, message)


def send_email_smtp(settings, to_email: str, subject: str, body: str) -> bool:
    """Gửi một email qua SMTP. Trả False (không ném lỗi) nếu chưa cấu hình hoặc gửi thất bại."""
    return send_email_smtp_detailed(settings, to_email, subject, body).ok


def dispatch_emails(settings, items: list[tuple[str, str, str]]) -> None:
    """Gửi danh sách email ở luồng nền để không chặn response API."""
    host = (getattr(settings, "smtp_host", "") or "").strip()
    if not host or not items:
        return

    def _run():
        for to_email, subject, body in items:
            send_email_smtp(settings, to_email, subject, body)

    threading.Thread(target=_run, daemon=True, name="esign-smtp").start()


def notify_users(
    db,
    settings,
    user_ids: Iterable[int],
    *,
    document_id: Optional[str],
    kind: str,
    title: str,
    message: str,
    email_subject: Optional[str] = None,
    email_body: Optional[str] = None,
    task_id: Optional[int] = None,
    commit: bool = False,
) -> list[tuple[str, str, str]]:
    """Tạo notification trong DB cho danh sách user đang hoạt động; trả về danh sách email cần gửi."""
    ids = list(dict.fromkeys(int(uid) for uid in user_ids if uid is not None))
    if not ids:
        return []
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(ids), User.is_active.is_(True)))}
    now = utcnow()
    clean_title = title[:200]
    clean_msg = message[:500]
    clean_subj = " ".join((email_subject or clean_title).replace("\r", "\n").splitlines()).strip()[:200]
    base_body = email_body if email_body is not None else clean_msg
    link = document_link(settings, document_id)
    mail_text = (
        f"{base_body}\n\nMở tài liệu: {link}\n(Vui lòng đăng nhập và xác thực 2FA để xem và thao tác với tài liệu.)"
        if link
        else base_body
    )
    email_jobs: list[tuple[str, str, str]] = []
    for uid in ids:
        u = users.get(uid)
        if u is None:
            continue
        db.add(Notification(
            user_id=u.id,
            document_id=document_id,
            task_id=task_id,
            kind=kind,
            title=clean_title,
            message=clean_msg,
            is_read=False,
            created_at=now,
        ))
        if u.email:
            email_jobs.append((u.email, clean_subj, mail_text))
    if commit:
        db.commit()
        dispatch_emails(settings, email_jobs)
    return email_jobs
