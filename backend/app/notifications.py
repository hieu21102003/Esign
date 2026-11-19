"""Thông báo trong ứng dụng & gửi email SMTP tùy chọn (tự động bỏ qua khi chưa cấu hình SMTP)."""
from __future__ import annotations

import logging
import smtplib
import threading
from email.message import EmailMessage
from typing import Iterable, Optional

from sqlalchemy import select

from .models import Notification, User, utcnow

log = logging.getLogger("esign.notifications")


def send_email_smtp(settings, to_email: str, subject: str, body: str) -> bool:
    """Gửi một email qua SMTP. Trả False (không ném lỗi) nếu chưa cấu hình hoặc gửi thất bại."""
    host = (getattr(settings, "smtp_host", "") or "").strip()
    if not host or not to_email:
        return False
    port = int(getattr(settings, "smtp_port", 587) or 587)
    user = (getattr(settings, "smtp_user", "") or "").strip()
    pw = getattr(settings, "smtp_pass", "") or ""
    sender = (getattr(settings, "smtp_from", "") or user or "no-reply@esign.local").strip()
    use_tls = bool(getattr(settings, "smtp_tls", True))

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body, charset="utf-8")

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=10) as srv:
                if user:
                    srv.login(user, pw)
                srv.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=10) as srv:
                srv.ehlo()
                if use_tls:
                    srv.starttls()
                    srv.ehlo()
                if user:
                    srv.login(user, pw)
                srv.send_message(msg)
        return True
    except Exception as exc:  # không làm gián đoạn luồng ký và không ghi mật khẩu vào log
        log.warning("Gửi email SMTP thất bại tới %s: %s", to_email, type(exc).__name__)
        return False


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
    email_jobs: list[tuple[str, str, str]] = []
    for uid in ids:
        u = users.get(uid)
        if u is None:
            continue
        db.add(Notification(
            user_id=u.id,
            document_id=document_id,
            kind=kind,
            title=clean_title,
            message=clean_msg,
            is_read=False,
            created_at=now,
        ))
        if u.email:
            email_jobs.append((u.email, clean_title, clean_msg))
    if commit:
        db.commit()
        dispatch_emails(settings, email_jobs)
    return email_jobs
