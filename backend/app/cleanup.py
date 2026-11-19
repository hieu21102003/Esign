"""Xóa file hết hạn (mặc định 7 ngày) + phiên hết hạn. audit_logs KHÔNG bao giờ bị xóa."""
from __future__ import annotations

import logging
import threading
from datetime import timedelta
from typing import Optional

from sqlalchemy import delete, or_, select

from .audit import audit
from .models import Document, DocumentVersion, SessionRow, StepAssignee, StoredFile, WorkflowStep, utcnow
from .storage import remove_from_disk

log = logging.getLogger("esign.cleanup")


def _collect_doc_file_ids(db, doc_ids: set[str]) -> set[str]:
    if not doc_ids:
        return set()
    out: set[str] = set()
    for orig_id, cur_id in db.execute(
        select(Document.original_file_id, Document.current_file_id).where(Document.id.in_(doc_ids))
    ):
        if orig_id:
            out.add(orig_id)
        if cur_id:
            out.add(cur_id)
    for fid in db.scalars(select(DocumentVersion.file_id).where(DocumentVersion.document_id.in_(doc_ids))):
        if fid:
            out.add(fid)
    for fid in db.scalars(
        select(StepAssignee.signed_file_id)
        .join(WorkflowStep, StepAssignee.step_id == WorkflowStep.id)
        .where(WorkflowStep.document_id.in_(doc_ids), StepAssignee.signed_file_id.isnot(None))
    ):
        out.add(fid)
    return out


def purge_expired(settings, session_factory) -> int:
    """Xóa vật lý file quá hạn và đánh dấu deleted_at. Trả về số file đã xóa."""
    db = session_factory()
    try:
        now = utcnow()
        rows = list(db.scalars(select(StoredFile).where(
            StoredFile.deleted_at.is_(None),
            StoredFile.is_workflow.is_(False),
            StoredFile.expires_at <= now,
        )))
        wf_days = getattr(settings, "workflow_retention_days", 0)
        if wf_days > 0:
            cutoff = now - timedelta(days=wf_days)
            protected_doc_ids = set(db.scalars(select(Document.id).where(
                or_(Document.status == "in_progress", Document.updated_at > cutoff)
            )))
            expired_doc_ids = set(db.scalars(select(Document.id).where(
                Document.status != "in_progress",
                Document.updated_at <= cutoff,
            )))
            purge_ids = _collect_doc_file_ids(db, expired_doc_ids) - _collect_doc_file_ids(db, protected_doc_ids)
            if purge_ids:
                rows.extend(db.scalars(select(StoredFile).where(
                    StoredFile.id.in_(purge_ids),
                    StoredFile.deleted_at.is_(None),
                    StoredFile.is_workflow.is_(True),
                )))
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
