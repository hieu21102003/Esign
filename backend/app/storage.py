"""Lưu/đọc file trên đĩa với tên ngẫu nhiên (UUID) + kiểm tra quyền sở hữu."""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import timedelta
from typing import Optional

from fastapi import HTTPException

from .models import StoredFile, utcnow


def _path(settings, file_id: str) -> str:
    uuid.UUID(file_id)  # ném ValueError nếu không phải UUID -> chặn path traversal
    return os.path.join(settings.upload_dir, file_id)


def store_bytes(db, settings, owner, data: bytes, *, kind: str, name: str, pages: int = 0,
                parent_id: Optional[str] = None, is_workflow: bool = False,
                commit: bool = True) -> StoredFile:
    fid = str(uuid.uuid4())
    os.makedirs(settings.upload_dir, mode=0o700, exist_ok=True)
    final = _path(settings, fid)
    tmp = final + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.chmod(tmp, 0o600)
    os.replace(tmp, final)
    now = utcnow()
    row = StoredFile(id=fid, owner_id=owner.id, original_name=name[:255], kind=kind,
                     sha256=hashlib.sha256(data).hexdigest(), size=len(data), pages=pages,
                     parent_id=parent_id, is_workflow=is_workflow,
                     created_at=now, expires_at=now + timedelta(days=settings.retention_days))
    db.add(row)
    if commit:
        db.commit()
    else:
        db.flush()
    return row


def read_bytes(settings, f: StoredFile) -> bytes:
    try:
        with open(_path(settings, f.id), "rb") as fh:
            return fh.read()
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "File không tồn tại hoặc đã bị xóa")


def remove_from_disk(settings, file_id: str) -> None:
    try:
        os.remove(_path(settings, file_id))
    except (FileNotFoundError, ValueError):
        pass


def get_file(db, ctx, file_id: str, *, owner_only: bool = False, kinds=None) -> StoredFile:
    """Trả file nếu người dùng là chủ (hoặc admin khi owner_only=False). Ngược lại 404 (không lộ sự tồn tại)."""
    try:
        uuid.UUID(file_id)
    except ValueError:
        raise HTTPException(404, "Không tìm thấy file")
    f = db.get(StoredFile, file_id)
    if f is None or f.deleted_at is not None or (not f.is_workflow and f.expires_at <= utcnow()):
        raise HTTPException(404, "Không tìm thấy file")
    is_owner = f.owner_id == ctx.user.id
    if not is_owner and not (ctx.user.role == "admin" and not owner_only):
        raise HTTPException(404, "Không tìm thấy file")
    if kinds and f.kind not in kinds:
        raise HTTPException(400, "Loại file không phù hợp")
    return f
