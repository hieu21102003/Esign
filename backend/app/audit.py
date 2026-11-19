"""Ghi audit log (chỉ thêm). Không bao giờ nhận/ghi mật khẩu, passphrase hay nội dung file."""
from __future__ import annotations

import json
from typing import Any, Mapping, Optional

from .models import AuditLog


def audit(db, ip: str, action: str, *, user=None, username: str = "", status: str = "ok",
          file_id: Optional[str] = None, file_sha256: Optional[str] = None,
          output_sha256: Optional[str] = None, cert_key_ref: Optional[str] = None,
          detail: Optional[str] = None, document_id: Optional[str] = None,
          metadata: Optional[Mapping[str, Any]] = None, metadata_json: Optional[str] = None,
          commit: bool = True) -> AuditLog:
    meta_str = (
        json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
        if metadata is not None else metadata_json
    )
    row = AuditLog(
        user_id=getattr(user, "id", None),
        username=(getattr(user, "username", None) or username or "")[:64],
        action=action[:48], status=status[:16], ip=(ip or "")[:64],
        file_id=file_id, file_sha256=file_sha256, output_sha256=output_sha256,
        cert_key_ref=cert_key_ref,
        detail=(detail[:500] if detail else None),
        document_id=document_id,
        metadata_json=meta_str,
    )
    db.add(row)
    if commit:
        db.commit()
    return row
