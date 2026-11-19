"""Kiểm thử Milestone W1: Data model luồng ký, migration an toàn, audit & giữ file workflow."""
from __future__ import annotations

import json
import os
import uuid
from types import SimpleNamespace
from datetime import timedelta

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from conftest import build_env


def _uid() -> str:
    return str(uuid.uuid4())


def test_w1_new_tables_columns_constraints_and_cascade(tmp_path):
    from app.models import (
        AuditLog,
        Document,
        DocumentVersion,
        Notification,
        StepAssignee,
        StoredFile,
        User,
        WorkflowStep,
        init_db,
        make_engine,
        make_session_factory,
        utcnow,
    )

    eng = make_engine("sqlite:///" + str(tmp_path / "w1.db").replace("\\", "/"))
    init_db(eng)
    insp = inspect(eng)
    tables = set(insp.get_table_names())
    assert {"documents", "workflow_steps", "step_assignees", "document_versions", "notifications",
            "terms_acceptances", "onboarding_otps"} <= tables
    user_cols = {c["name"] for c in insp.get_columns("users")}
    assert {
        "email", "signing_pin_hash", "pin_set_at", "pin_failed_attempts", "pin_fail_times_json", "pin_locked_until",
        "job_title", "department", "manager_name", "manager_user_id", "signature_image_file_id",
        "onboarding_step", "onboarding_completed_at", "accepted_terms_version",
        "onboarding_otp_verified_at", "onboarding_otp_session_id",
    } <= user_cols
    assert "is_workflow" in {c["name"] for c in insp.get_columns("files")}
    audit_cols = {c["name"] for c in insp.get_columns("audit_logs")}
    assert {"document_id", "metadata_json"} <= audit_cols

    sf = make_session_factory(eng)
    db = sf()
    try:
        u1 = User(username="alice", display_name="Alice", email="alice@example.local", password_hash="h")
        u2 = User(username="bob", display_name="Bob", password_hash="h")
        db.add_all([u1, u2])
        db.flush()

        now = utcnow()
        f_orig = StoredFile(
            id=_uid(), owner_id=u1.id, original_name="hd.pdf", kind="upload",
            sha256="a" * 64, size=100, pages=1, is_workflow=True, created_at=now, expires_at=now + timedelta(days=7),
        )
        f_signed = StoredFile(
            id=_uid(), owner_id=u2.id, original_name="hd_s.pdf", kind="signed",
            sha256="b" * 64, size=120, pages=1, parent_id=f_orig.id, is_workflow=True,
            created_at=now, expires_at=now + timedelta(days=7),
        )
        db.add_all([f_orig, f_signed])
        db.flush()

        doc = Document(
            id=_uid(), title="Hợp đồng số 1", creator_id=u1.id,
            original_file_id=f_orig.id, current_file_id=f_signed.id,
        )
        db.add(doc)
        db.flush()

        step = WorkflowStep(document_id=doc.id, step_order=1, mode="all", assigned_by_id=u1.id)
        db.add(step)
        db.flush()

        sa = StepAssignee(step_id=step.id, user_id=u2.id, status="pending")
        ver = DocumentVersion(
            document_id=doc.id, version_no=1, file_id=f_orig.id,
            step_id=step.id, created_by_id=u1.id, note="Bản gốc",
        )
        notif = Notification(user_id=u2.id, document_id=doc.id, kind="assigned", title="Có tài liệu chờ ký")
        log_row = AuditLog(action="doc_create", username="alice", document_id=doc.id, metadata_json='{"step":1}')
        db.add_all([sa, ver, notif, log_row])
        db.commit()
        doc_id, step_id, u1_id, u2_id, f_orig_id = doc.id, step.id, u1.id, u2.id, f_orig.id

        # Ràng buộc unique
        db.add(WorkflowStep(document_id=doc_id, step_order=1, mode="any", assigned_by_id=u1_id))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        db.add(StepAssignee(step_id=step_id, user_id=u2_id))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        db.add(DocumentVersion(document_id=doc_id, version_no=1, file_id=f_orig_id, created_by_id=u1_id))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        # Xóa Document phải cascade tới step, assignee, version, notification
        db.delete(db.get(Document, doc_id))
        db.commit()
        assert db.scalar(select(WorkflowStep).where(WorkflowStep.document_id == doc_id)) is None
        assert db.scalar(select(StepAssignee).where(StepAssignee.step_id == step_id)) is None
        assert db.scalar(select(DocumentVersion).where(DocumentVersion.document_id == doc_id)) is None
        assert db.scalar(select(Notification).where(Notification.document_id == doc_id)) is None
    finally:
        db.close()


def test_w1_idempotent_migration_from_legacy_db(tmp_path):
    from app.models import (
        _SQLITE_TRIGGERS,
        AuditLog,
        Document,
        StoredFile,
        User,
        init_db,
        make_engine,
        make_session_factory,
    )

    db_path = tmp_path / "legacy.db"
    db_url = "sqlite:///" + str(db_path).replace("\\", "/")
    eng = make_engine(db_url)

    legacy_ddls = [
        """CREATE TABLE users (
            id INTEGER PRIMARY KEY, username VARCHAR(64) UNIQUE NOT NULL,
            display_name VARCHAR(120) NOT NULL DEFAULT '', password_hash VARCHAR(255) NOT NULL,
            role VARCHAR(16) NOT NULL DEFAULT 'user', is_active BOOLEAN NOT NULL DEFAULT 1,
            totp_secret_enc TEXT, totp_enabled BOOLEAN NOT NULL DEFAULT 0,
            totp_last_step INTEGER NOT NULL DEFAULT 0, failed_attempts INTEGER NOT NULL DEFAULT 0,
            locked_until DATETIME, created_at DATETIME NOT NULL
        )""",
        """CREATE TABLE recovery_codes (
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            code_hash VARCHAR(64) NOT NULL, used_at DATETIME
        )""",
        """CREATE TABLE sessions (
            id VARCHAR(64) PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            csrf_token VARCHAR(64) NOT NULL, mfa_verified BOOLEAN NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL, expires_at DATETIME NOT NULL, ip VARCHAR(64) NOT NULL DEFAULT ''
        )""",
        """CREATE TABLE certificates (
            id INTEGER PRIMARY KEY, key_ref VARCHAR(64) UNIQUE NOT NULL,
            subject VARCHAR(255) NOT NULL DEFAULT '', serial VARCHAR(64) NOT NULL DEFAULT '',
            not_after VARCHAR(40) NOT NULL DEFAULT '', active BOOLEAN NOT NULL DEFAULT 1,
            created_at DATETIME NOT NULL
        )""",
        """CREATE TABLE user_certificates (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            cert_id INTEGER NOT NULL REFERENCES certificates(id) ON DELETE CASCADE,
            PRIMARY KEY (user_id, cert_id)
        )""",
        """CREATE TABLE files (
            id VARCHAR(36) PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id),
            original_name VARCHAR(255) NOT NULL DEFAULT '', kind VARCHAR(16) NOT NULL,
            sha256 VARCHAR(64) NOT NULL, size INTEGER NOT NULL, pages INTEGER NOT NULL DEFAULT 0,
            parent_id VARCHAR(36), created_at DATETIME NOT NULL, expires_at DATETIME NOT NULL,
            deleted_at DATETIME
        )""",
        """CREATE TABLE sign_jobs (
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
            input_file_id VARCHAR(36) NOT NULL, output_file_id VARCHAR(36),
            cert_id INTEGER NOT NULL REFERENCES certificates(id), params_json TEXT NOT NULL DEFAULT '{}',
            status VARCHAR(16) NOT NULL DEFAULT 'running', error VARCHAR(400),
            created_at DATETIME NOT NULL, finished_at DATETIME
        )""",
        """CREATE TABLE signature_presets (
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name VARCHAR(80) NOT NULL, config_json TEXT NOT NULL, created_at DATETIME NOT NULL
        )""",
        """CREATE TABLE audit_logs (
            id INTEGER PRIMARY KEY, ts DATETIME NOT NULL, user_id INTEGER,
            username VARCHAR(64) NOT NULL DEFAULT '', action VARCHAR(48) NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'ok', ip VARCHAR(64) NOT NULL DEFAULT '',
            file_id VARCHAR(36), file_sha256 VARCHAR(64), output_sha256 VARCHAR(64),
            cert_key_ref VARCHAR(64), detail VARCHAR(500)
        )""",
        *_SQLITE_TRIGGERS,
    ]
    fid = _uid()
    with eng.begin() as con:
        for ddl in legacy_ddls:
            con.exec_driver_sql(ddl)
        con.exec_driver_sql(
            "INSERT INTO users (id, username, display_name, password_hash, role, created_at) "
            "VALUES (1, 'legacy_user', 'Legacy User', 'h', 'user', '2026-01-01 00:00:00')"
        )
        con.exec_driver_sql(
            "INSERT INTO files (id, owner_id, original_name, kind, sha256, size, pages, created_at, expires_at) "
            "VALUES (?, 1, 'old.pdf', 'upload', ?, 42, 1, '2026-01-01 00:00:00', '2026-01-08 00:00:00')",
            (fid, "c" * 64),
        )
        con.exec_driver_sql(
            "INSERT INTO audit_logs (id, ts, user_id, username, action) "
            "VALUES (1, '2026-01-01 00:00:00', 1, 'legacy_user', 'login')"
        )

    # Chạy init_db 2 lần để xác nhận idempotent
    init_db(eng)
    init_db(eng)

    insp = inspect(eng)
    assert {"documents", "workflow_steps", "step_assignees", "document_versions", "notifications",
            "terms_acceptances", "onboarding_otps"} <= set(insp.get_table_names())
    migrated_user_cols = {c["name"] for c in insp.get_columns("users")}
    assert {
        "email", "signing_pin_hash", "job_title", "department", "manager_name",
        "onboarding_step", "onboarding_completed_at", "accepted_terms_version",
    } <= migrated_user_cols
    audit_idx = {ix["name"] for ix in insp.get_indexes("audit_logs")}
    assert "ix_audit_logs_document_id" in audit_idx

    sf = make_session_factory(eng)
    db = sf()
    try:
        u = db.get(User, 1)
        assert u is not None and u.username == "legacy_user" and u.email is None and u.signing_pin_hash is None
        assert u.pin_failed_attempts == 0 and u.pin_locked_until is None
        assert u.onboarding_completed_at is None and u.onboarding_step == 1
        f = db.get(StoredFile, fid)
        assert f is not None and f.is_workflow is False
        a = db.get(AuditLog, 1)
        assert a is not None and a.action == "login" and a.document_id is None and a.metadata_json is None

        doc = Document(
            id=_uid(), title="Tài liệu sau migration", creator_id=u.id,
            original_file_id=f.id, current_file_id=f.id,
        )
        db.add(doc)
        db.commit()
        assert db.get(Document, doc.id) is not None

        # ORM & SQL trigger vẫn chặn sửa/xóa audit_logs sau migration
        a.action = "hacked"
        with pytest.raises(PermissionError):
            db.commit()
        db.rollback()
        db.delete(db.get(AuditLog, 1))
        with pytest.raises(PermissionError):
            db.commit()
        db.rollback()
    finally:
        db.close()

    for sql in ("UPDATE audit_logs SET action='x'", "DELETE FROM audit_logs"):
        with pytest.raises(Exception):
            with eng.begin() as con:
                con.execute(text(sql))


def test_w1_audit_helper_document_and_metadata(tmp_path):
    from app.audit import audit
    from app.models import AuditLog

    env = build_env(tmp_path, with_services=False)
    doc_id = _uid()
    sf = env.app.state.session_factory
    db = sf()
    db_other = sf()
    try:
        row = audit(db, "127.0.0.1", "doc_create", username="alice", document_id=doc_id,
                    metadata={"step": 1, "mode": "all"})
        assert row.document_id == doc_id
        assert json.loads(row.metadata_json) == {"step": 1, "mode": "all"}

        # commit=False chỉ stage trong session hiện tại cho đến khi commit()
        staged = audit(db, "127.0.0.1", "doc_assign", username="alice", document_id=doc_id,
                       metadata={"staged": True}, commit=False)
        assert db_other.scalar(select(AuditLog).where(AuditLog.action == "doc_assign")) is None
        db.commit()
        found = db_other.get(AuditLog, staged.id)
        assert found is not None and json.loads(found.metadata_json) == {"staged": True}
    finally:
        db.close()
        db_other.close()

    # Admin API lọc audit theo document_id và trả metadata_json
    rows = env.admin.get(f"/api/admin/audit?document_id={doc_id}").json()
    assert len(rows) == 2
    assert {r["action"] for r in rows} == {"doc_create", "doc_assign"}
    assert all(r["document_id"] == doc_id for r in rows)


def test_w1_cleanup_and_storage_preserve_workflow_files(tmp_path):
    from app.cleanup import purge_expired
    from app.config import Settings
    from app.models import Document, DocumentVersion, StepAssignee, StoredFile, User, WorkflowStep, utcnow
    from app.storage import get_file, store_bytes

    env = build_env(tmp_path, with_services=False)
    st = env.app.state.settings
    sf = env.app.state.session_factory
    db = sf()
    try:
        u = db.scalar(select(User).where(User.username == "admin"))
        f_std = store_bytes(db, st, u, b"%PDF-std", kind="upload", name="std.pdf", pages=1, is_workflow=False)
        f_active = store_bytes(db, st, u, b"%PDF-act", kind="upload", name="act.pdf", pages=1, is_workflow=True)
        f_done_orig = store_bytes(db, st, u, b"%PDF-done0", kind="upload", name="done0.pdf", pages=1, is_workflow=True)
        f_done_signed = store_bytes(
            db, st, u, b"%PDF-done1", kind="signed", name="done1.pdf", pages=1, parent_id=f_done_orig.id, is_workflow=True
        )

        past = utcnow() - timedelta(days=30)
        for f in (f_std, f_active, f_done_orig, f_done_signed):
            f.expires_at = past

        doc_active = Document(
            id=_uid(), title="Đang ký", status="in_progress", creator_id=u.id,
            original_file_id=f_active.id, current_file_id=f_active.id, updated_at=past,
        )
        utc_done = utcnow() - timedelta(days=10)
        doc_done = Document(
            id=_uid(), title="Đã xong", status="completed", creator_id=u.id,
            original_file_id=f_done_orig.id, current_file_id=f_done_signed.id,
            completed_at=utc_done, updated_at=utc_done,
        )
        db.add_all([doc_active, doc_done])
        db.flush()
        step_done = WorkflowStep(
            document_id=doc_done.id, step_order=1, mode="all", status="completed", assigned_by_id=u.id
        )
        db.add(step_done)
        db.flush()
        db.add(StepAssignee(
            step_id=step_done.id, user_id=u.id, status="signed", signed_file_id=f_done_signed.id
        ))
        db.add(DocumentVersion(
            document_id=doc_done.id, version_no=1, file_id=f_done_orig.id, step_id=step_done.id, created_by_id=u.id
        ))
        db.commit()

        # get_file(): chặn file lẻ quá hạn, vẫn trả file workflow dù quá expires_at
        ctx = SimpleNamespace(user=u)
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            get_file(db, ctx, f_std.id)
        assert exc.value.status_code == 404
        assert get_file(db, ctx, f_active.id).id == f_active.id
        assert get_file(db, ctx, f_done_signed.id).id == f_done_signed.id
    finally:
        db.close()

    # workflow_retention_days = 0 -> chỉ xóa file lẻ (count == 1), giữ toàn bộ file workflow
    assert st.workflow_retention_days == 0
    assert purge_expired(st, sf) == 1
    assert not os.path.exists(os.path.join(st.upload_dir, f_std.id))
    assert os.path.exists(os.path.join(st.upload_dir, f_active.id))
    assert os.path.exists(os.path.join(st.upload_dir, f_done_orig.id))
    assert os.path.exists(os.path.join(st.upload_dir, f_done_signed.id))

    # workflow_retention_days = 7 -> xóa các file của luồng đã kết thúc > 7 ngày (f_done_orig & f_done_signed), giữ f_active
    st_prune = Settings(**{**st.__dict__, "workflow_retention_days": 7})
    assert purge_expired(st_prune, sf) == 2
    assert os.path.exists(os.path.join(st.upload_dir, f_active.id))
    assert not os.path.exists(os.path.join(st.upload_dir, f_done_orig.id))
    assert not os.path.exists(os.path.join(st.upload_dir, f_done_signed.id))
    db = sf()
    try:
        assert db.get(StoredFile, f_active.id).deleted_at is None
        assert db.get(StoredFile, f_done_orig.id).deleted_at is not None
        assert db.get(StoredFile, f_done_signed.id).deleted_at is not None
    finally:
        db.close()


def test_w1_postgres_trigger_ddl_present_and_valid(env_light):
    from app.models import _PG_AUDIT_TRIGGERS

    assert len(_PG_AUDIT_TRIGGERS) == 3
    joined = "\n".join(_PG_AUDIT_TRIGGERS)
    assert "audit_logs_append_only()" in _PG_AUDIT_TRIGGERS[0]
    assert "audit_logs_no_update" in _PG_AUDIT_TRIGGERS[1]
    assert "audit_logs_no_delete" in _PG_AUDIT_TRIGGERS[2]
    assert "BEFORE UPDATE ON audit_logs" in joined
    assert "BEFORE DELETE ON audit_logs" in joined

    users = env_light.admin.get("/api/admin/users").json()
    assert users and "email" in users[0] and users[0]["email"] is None
