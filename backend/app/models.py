"""Mô hình dữ liệu (SQLAlchemy 2). Không có cột nào chứa khóa riêng/passphrase."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, event, inspect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DOC_STATUSES = ("in_progress", "completed", "rejected", "cancelled")
STEP_MODES = ("all", "any")
STEP_STATUSES = ("active", "completed", "rejected", "cancelled")
ASSIGNEE_STATUSES = ("pending", "signed", "rejected", "skipped")
NOTIF_KINDS = ("assigned", "completed", "rejected", "cancelled")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="user")  # admin | user
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    totp_secret_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Fernet
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    totp_last_step: Mapped[int] = mapped_column(Integer, default=0)  # chống replay mã TOTP
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RecoveryCode(Base):
    __tablename__ = "recovery_codes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class SessionRow(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256(token); token chỉ nằm ở cookie
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    mfa_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    ip: Mapped[str] = mapped_column(String(64), default="")


class Certificate(Base):
    """Chỉ lưu tham chiếu (key_ref) tới .p12 nằm ở service signer. KHÔNG lưu khóa."""
    __tablename__ = "certificates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key_ref: Mapped[str] = mapped_column(String(64), unique=True)
    subject: Mapped[str] = mapped_column(String(255), default="")
    serial: Mapped[str] = mapped_column(String(64), default="")
    not_after: Mapped[str] = mapped_column(String(40), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class UserCertificate(Base):
    __tablename__ = "user_certificates"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    cert_id: Mapped[int] = mapped_column(ForeignKey("certificates.id", ondelete="CASCADE"), primary_key=True)


class StoredFile(Base):
    __tablename__ = "files"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # UUID = tên file trên đĩa
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    original_name: Mapped[str] = mapped_column(String(255), default="")
    kind: Mapped[str] = mapped_column(String(16))  # upload | signed | image
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    pages: Mapped[int] = mapped_column(Integer, default=0)
    parent_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    is_workflow: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class SignJob(Base):
    __tablename__ = "sign_jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    input_file_id: Mapped[str] = mapped_column(String(36))
    output_file_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    cert_id: Mapped[int] = mapped_column(ForeignKey("certificates.id"))
    params_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(16), default="running")  # running | done | failed
    error: Mapped[Optional[str]] = mapped_column(String(400), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class SignaturePreset(Base):
    __tablename__ = "signature_presets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    config_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Document(Base):
    """Tài liệu trong luồng ký (signing workflow)."""
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="in_progress", index=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    original_file_id: Mapped[str] = mapped_column(ForeignKey("files.id"))
    current_file_id: Mapped[str] = mapped_column(ForeignKey("files.id"))
    current_step_order: Mapped[int] = mapped_column(Integer, default=1)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    rejected_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    cancel_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class WorkflowStep(Base):
    """Một bước trong luồng ký (1 hoặc nhiều người ký song song)."""
    __tablename__ = "workflow_steps"
    __table_args__ = (UniqueConstraint("document_id", "step_order", name="uq_workflow_steps_doc_order"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    step_order: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(8), default="all")  # all | any
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    assigned_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class StepAssignee(Base):
    """Người được assign trong một bước ký."""
    __tablename__ = "step_assignees"
    __table_args__ = (UniqueConstraint("step_id", "user_id", name="uq_step_assignees_step_user"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    step_id: Mapped[int] = mapped_column(ForeignKey("workflow_steps.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    signed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    remark: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    signed_file_id: Mapped[Optional[str]] = mapped_column(ForeignKey("files.id"), nullable=True)
    sign_job_id: Mapped[Optional[int]] = mapped_column(ForeignKey("sign_jobs.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DocumentVersion(Base):
    """Lịch sử từng phiên bản PDF của tài liệu sau mỗi lần ký để truy vết."""
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version_no", name="uq_document_versions_doc_ver"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id"))
    step_id: Mapped[Optional[int]] = mapped_column(ForeignKey("workflow_steps.id", ondelete="SET NULL"), nullable=True)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Notification(Base):
    """Thông báo trong ứng dụng cho người dùng."""
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(24), index=True)  # assigned | completed | rejected | cancelled
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(String(500), default="")
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class AuditLog(Base):
    """Chỉ ghi thêm. Không có API sửa/xóa; chặn thêm bằng ORM event + trigger DB."""
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    username: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(48), index=True)
    status: Mapped[str] = mapped_column(String(16), default="ok")
    ip: Mapped[str] = mapped_column(String(64), default="")
    file_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    file_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    output_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    cert_key_ref: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    detail: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    document_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


@event.listens_for(AuditLog, "before_update")
def _audit_no_update(mapper, connection, target):  # noqa: ARG001
    raise PermissionError("audit_logs chỉ cho phép ghi thêm")


@event.listens_for(AuditLog, "before_delete")
def _audit_no_delete(mapper, connection, target):  # noqa: ARG001
    raise PermissionError("audit_logs chỉ cho phép ghi thêm")


_SQLITE_TRIGGERS = (
    "CREATE TRIGGER IF NOT EXISTS audit_logs_no_update BEFORE UPDATE ON audit_logs "
    "BEGIN SELECT RAISE(ABORT, 'audit_logs is append-only'); END;",
    "CREATE TRIGGER IF NOT EXISTS audit_logs_no_delete BEFORE DELETE ON audit_logs "
    "BEGIN SELECT RAISE(ABORT, 'audit_logs is append-only'); END;",
)

_PG_AUDIT_TRIGGERS = (
    "CREATE OR REPLACE FUNCTION audit_logs_append_only() RETURNS trigger AS $$ "
    "BEGIN RAISE EXCEPTION 'audit_logs is append-only'; END; $$ LANGUAGE plpgsql;",
    "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'audit_logs_no_update') THEN "
    "CREATE TRIGGER audit_logs_no_update BEFORE UPDATE ON audit_logs FOR EACH ROW EXECUTE FUNCTION audit_logs_append_only(); "
    "END IF; END $$;",
    "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'audit_logs_no_delete') THEN "
    "CREATE TRIGGER audit_logs_no_delete BEFORE DELETE ON audit_logs FOR EACH ROW EXECUTE FUNCTION audit_logs_append_only(); "
    "END IF; END $$;",
)


def make_engine(url: str):
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 15}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_con, _rec):  # noqa: ANN001
            cur = dbapi_con.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    return engine


def migrate_db(engine) -> None:
    """Nâng cấp schema an toàn (idempotent) cho DB cũ, giữ nguyên dữ liệu."""
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    cols = {t: {c["name"] for c in insp.get_columns(t)} for t in tables}
    stmts = []
    if "users" in tables and "email" not in cols["users"]:
        stmts.append("ALTER TABLE users ADD COLUMN email VARCHAR(255)")
    if "files" in tables and "is_workflow" not in cols["files"]:
        stmts.append("ALTER TABLE files ADD COLUMN is_workflow BOOLEAN NOT NULL DEFAULT FALSE")
    if "audit_logs" in tables:
        if "document_id" not in cols["audit_logs"]:
            stmts.append("ALTER TABLE audit_logs ADD COLUMN document_id VARCHAR(36)")
        stmts.append("CREATE INDEX IF NOT EXISTS ix_audit_logs_document_id ON audit_logs (document_id)")
        if "metadata_json" not in cols["audit_logs"]:
            stmts.append("ALTER TABLE audit_logs ADD COLUMN metadata_json TEXT")
    with engine.begin() as con:
        for ddl in stmts:
            con.exec_driver_sql(ddl)
        if engine.dialect.name == "sqlite":
            for ddl in _SQLITE_TRIGGERS:
                con.exec_driver_sql(ddl)
        elif engine.dialect.name == "postgresql":
            for ddl in _PG_AUDIT_TRIGGERS:
                con.exec_driver_sql(ddl)


def init_db(engine) -> None:
    Base.metadata.create_all(engine)
    migrate_db(engine)


def make_session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)
