"""Ghi audit log (chỉ thêm). Không bao giờ nhận/ghi mật khẩu, passphrase hay nội dung file."""
from __future__ import annotations

from typing import Optional

from .models import AuditLog


def audit(db, ip: str, action: str, *, user=None, username: str = "", status: str = "ok",
          file_id: Optional[str] = None, file_sha256: Optional[str] = None,
          output_sha256: Optional[str] = None, cert_key_ref: Optional[str] = None,
          detail: Optional[str] = None) -> None:
    row = AuditLog(
        user_id=getattr(user, "id", None),
        username=(getattr(user, "username", None) or username or "")[:64],
        action=action[:48], status=status[:16], ip=(ip or "")[:64],
        file_id=file_id, file_sha256=file_sha256, output_sha256=output_sha256,
        cert_key_ref=cert_key_ref,
        detail=(detail[:500] if detail else None),
    )
    db.add(row)
    db.commit()
