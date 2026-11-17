"""Xóa file hết hạn (mặc định 7 ngày) + phiên hết hạn. audit_logs KHÔNG bao giờ bị xóa."""
from __future__ import annotations

import logging
import threading
from typing import Optional

from sqlalchemy import delete, select

from .audit import audit
from .models import SessionRow, StoredFile, utcnow
from .storage import remove_from_disk

log = logging.getLogger("esign.cleanup")


def purge_expired(settings, session_factory) -> int:
    """Xóa vật lý file quá hạn và đánh dấu deleted_at. Trả về số file đã xóa."""
    db = session_factory()
    try:
        now = utcnow()
        rows = list(db.scalars(select(StoredFile).where(StoredFile.deleted_at.is_(None),
                                                        StoredFile.expires_at <= now)))
        for f in rows:
            remove_from_disk(settings, f.id)
            f.deleted_at = now
        db.execute(delete(SessionRow).where(SessionRow.expires_at <= now))
        db.commit()
        if rows:
            audit(db, "system", "files_expired_deleted", username="system", detail=f"count={len(rows)}")
        return len(rows)
    finally:
        db.close()


class CleanupThread(threading.Thread):
    def __init__(self, settings, session_factory):
        super().__init__(daemon=True, name="esign-cleanup")
        self.settings, self.sf = settings, session_factory
        self._stop_evt = threading.Event()

    def run(self):
        while not self._stop_evt.is_set():
            try:
                purge_expired(self.settings, self.sf)
            except Exception:  # job nền không được làm sập app
                log.exception("cleanup lỗi")
            try:
                from .services import signer_status
                for w in signer_status(self.settings):
                    if isinstance(w.get("days_left"), (int, float)) and w["days_left"] <= 30:
                        log.warning("PKI sắp hết hạn/quá hạn: %s %s (còn %.1f ngày)", w.get("kind"), w.get("name"), w["days_left"])
            except Exception:
                pass
            self._stop_evt.wait(self.settings.cleanup_interval_seconds)

    def stop(self):
        self._stop_evt.set()


if __name__ == "__main__":  # python -m app.cleanup  (chạy tay / cron)
    from .config import Settings
    from .models import make_engine, make_session_factory
    s = Settings.from_env()
    print("Đã xóa", purge_expired(s, make_session_factory(make_engine(s.db_url))), "file hết hạn")
