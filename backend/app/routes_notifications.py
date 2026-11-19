"""API thông báo trong ứng dụng: xem danh sách, đếm chưa đọc, đánh dấu đã đọc."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, func, select, update

from .deps import Ctx, get_db, require_mfa
from .models import Notification, utcnow

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


def _notif_out(n: Notification) -> dict:
    return {
        "id": n.id,
        "document_id": n.document_id,
        "kind": n.kind,
        "title": n.title,
        "message": n.message,
        "is_read": n.is_read,
        "created_at": n.created_at.isoformat(),
        "read_at": n.read_at.isoformat() if n.read_at else None,
    }


def _unread_count(db, user_id: int) -> int:
    return int(db.scalar(
        select(func.count(Notification.id)).where(
            Notification.user_id == user_id,
            Notification.is_read.is_(False),
        )
    ) or 0)


@router.get("")
def list_notifications(unread_only: bool = False, limit: int = 50, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    lim = max(1, min(limit, 200))
    q = select(Notification).where(Notification.user_id == ctx.user.id)
    if unread_only:
        q = q.where(Notification.is_read.is_(False))
    items = list(db.scalars(q.order_by(desc(Notification.id)).limit(lim)))
    return {
        "unread_count": _unread_count(db, ctx.user.id),
        "items": [_notif_out(n) for n in items],
    }


@router.get("/unread-count")
def get_unread_count(ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    return {"unread_count": _unread_count(db, ctx.user.id)}


@router.post("/{notif_id}/read")
def mark_read(notif_id: int, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    n = db.get(Notification, notif_id)
    if n is None or n.user_id != ctx.user.id:
        raise HTTPException(404, "Không tìm thấy thông báo")
    if not n.is_read:
        n.is_read = True
        n.read_at = utcnow()
        db.commit()
    return {"ok": True, "id": n.id, "unread_count": _unread_count(db, ctx.user.id)}


@router.post("/read-all")
def mark_all_read(ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    now = utcnow()
    res = db.execute(
        update(Notification)
        .where(Notification.user_id == ctx.user.id, Notification.is_read.is_(False))
        .values(is_read=True, read_at=now)
    )
    db.commit()
    return {"ok": True, "marked": int(res.rowcount or 0), "unread_count": 0}
