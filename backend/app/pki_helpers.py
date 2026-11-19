"""Tiện ích kiểm tra CN, email và sinh key_ref chứng thư dùng chung."""
from __future__ import annotations

import re
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select

from .models import Certificate

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_BAD_CN_RE = re.compile(r"[/\\+\x00-\x1f\x7f]")


def clean_cn(raw: str) -> str:
    s = (raw or "").strip()
    if not s or len(s) > 120 or _BAD_CN_RE.search(s):
        raise HTTPException(400, "Tên hiển thị trên chứng thư không hợp lệ (không rỗng, không chứa / \\ +)")
    return s


def clean_email(v: Optional[str]) -> Optional[str]:
    if not v or not v.strip():
        return None
    s = v.strip().lower()
    if len(s) > 255 or not EMAIL_RE.match(s):
        raise HTTPException(400, "Email không hợp lệ")
    return s


def next_key_ref(db, base: str) -> str:
    norm = re.sub(r"[^A-Za-z0-9_-]+", "_", (base or "").strip().lower()).strip("_")
    if len(norm) < 2:
        norm = f"u_{norm or 'signer'}"
    norm = norm[:56]
    used = set(db.scalars(select(Certificate.key_ref)))
    if norm not in used:
        return norm
    n = 2
    while f"{norm}-{n}" in used:
        n += 1
    return f"{norm}-{n}"
