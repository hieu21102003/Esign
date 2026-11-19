"""Xác thực PIN ký 6 chữ số: băm Argon2id, so sánh hằng thời gian, chống dò theo cửa sổ trượt."""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, Request
from sqlalchemy import select

from .audit import audit
from .deps import Ctx
from .models import User
from .security import DUMMY_HASH, verify_password

_PIN_RE = re.compile(r"^[0-9]{6}$")
_PIN_LOCKS_GUARD = threading.Lock()
_PIN_LOCKS: dict[int, threading.Lock] = {}


def _pin_lock(user_id: int) -> threading.Lock:
    with _PIN_LOCKS_GUARD:
        lk = _PIN_LOCKS.get(user_id)
        if lk is None:
            lk = threading.Lock()
            _PIN_LOCKS[user_id] = lk
        return lk


def now_from_request(request: Request) -> tuple[float, datetime]:
    """Lấy timestamp & UTC datetime (naive) thống nhất với `app.state.clock` dùng trong test."""
    clock = getattr(request.app.state, "clock", None)
    ts = float(clock()) if clock is not None else datetime.now(timezone.utc).timestamp()
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
    return ts, dt


def verify_signing_pin(
    db,
    request: Request,
    ctx: Ctx,
    raw_pin: Optional[str],
    *,
    action: str,
    document_id: Optional[str] = None,
    file_id: Optional[str] = None,
) -> None:
    """Xác thực PIN ký của user hiện tại trước mọi hành động ký / từ chối / hủy."""
    st = request.app.state.settings
    now_ts, now_dt = now_from_request(request)

    with _pin_lock(ctx.user.id):
        db.rollback()
        u = db.scalar(select(User).where(User.id == ctx.user.id).with_for_update())
        if u is None or not u.is_active:
            raise HTTPException(401, "Phiên không hợp lệ")

        if not u.signing_pin_hash:
            audit(
                db, ctx.ip, "pin_verify_failed", user=u, status="denied",
                document_id=document_id, file_id=file_id,
                detail=f"action={action} reason=not_set", commit=True,
            )
            raise HTTPException(
                403,
                "Bạn chưa đặt mã PIN ký. Vui lòng vào Cài đặt tài khoản để đặt PIN 6 chữ số trước khi ký.",
                headers={"X-Pin-Error": "pin_not_set"},
            )

        if u.pin_locked_until and u.pin_locked_until > now_dt:
            wait_secs = max(1, int((u.pin_locked_until - now_dt).total_seconds()) + 1)
            wait_mins = max(1, (wait_secs + 59) // 60)
            audit(
                db, ctx.ip, "pin_verify_locked", user=u, status="denied",
                document_id=document_id, file_id=file_id,
                detail=f"action={action} wait={wait_secs}s", commit=True,
            )
            raise HTTPException(
                423,
                f"Chức năng ký đang tạm khóa do nhập sai PIN quá nhiều lần. Vui lòng thử lại sau khoảng {wait_mins} phút hoặc liên hệ quản trị viên.",
                headers={"X-Pin-Error": "pin_locked", "Retry-After": str(wait_secs)},
            )

        if raw_pin is None or raw_pin == "":
            audit(
                db, ctx.ip, "pin_verify_failed", user=u, status="denied",
                document_id=document_id, file_id=file_id,
                detail=f"action={action} reason=missing", commit=True,
            )
            raise HTTPException(
                400,
                "Vui lòng nhập mã PIN ký (6 chữ số)",
                headers={"X-Pin-Error": "pin_required"},
            )

        pin_str = raw_pin if isinstance(raw_pin, str) else ""
        if _PIN_RE.fullmatch(pin_str):
            pin_ok = verify_password(u.signing_pin_hash, pin_str)
        else:
            verify_password(DUMMY_HASH, pin_str)
            pin_ok = False

        if not pin_ok:
            window_secs = max(60, int(st.pin_fail_window_minutes) * 60)
            cutoff = now_ts - window_secs
            try:
                prev_raw = json.loads(u.pin_fail_times_json or "[]")
                if not isinstance(prev_raw, list):
                    prev_raw = []
            except (ValueError, TypeError):
                prev_raw = []

            recent = [
                float(t) for t in prev_raw
                if isinstance(t, (int, float)) and cutoff < float(t) <= now_ts + 5
            ]
            recent.append(now_ts)
            max_attempts = max(1, int(st.pin_max_failed_attempts))

            if len(recent) >= max_attempts:
                lock_mins = max(1, int(st.pin_lockout_minutes))
                u.pin_locked_until = now_dt + timedelta(minutes=lock_mins)
                u.pin_failed_attempts = 0
                u.pin_fail_times_json = "[]"
                audit(
                    db, ctx.ip, "pin_locked", user=u, status="denied",
                    document_id=document_id, file_id=file_id,
                    detail=f"action={action} fails={len(recent)}", commit=False,
                )
                db.commit()
                raise HTTPException(
                    423,
                    f"Mã PIN không đúng. Chức năng ký đã tạm khóa {lock_mins} phút do nhập sai quá nhiều lần.",
                    headers={"X-Pin-Error": "pin_locked", "Retry-After": str(lock_mins * 60)},
                )

            remaining = max_attempts - len(recent)
            u.pin_failed_attempts = len(recent)
            u.pin_fail_times_json = json.dumps(recent[-max_attempts:])
            audit(
                db, ctx.ip, "pin_verify_failed", user=u, status="denied",
                document_id=document_id, file_id=file_id,
                detail=f"action={action} remaining={remaining}", commit=False,
            )
            db.commit()
            raise HTTPException(
                401,
                f"Mã PIN không đúng (còn {remaining} lần thử)",
                headers={"X-Pin-Error": "pin_invalid", "X-Pin-Remaining": str(remaining)},
            )

        u.pin_failed_attempts = 0
        u.pin_fail_times_json = "[]"
        u.pin_locked_until = None
        audit(
            db, ctx.ip, "pin_verify_ok", user=u,
            document_id=document_id, file_id=file_id,
            detail=f"action={action}", commit=False,
        )
        db.commit()
