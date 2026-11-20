"""Mô hình dữ liệu (SQLAlchemy 2). Không có cột nào chứa khóa riêng/passphrase."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    inspect,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DOC_STATUSES = ("in_progress", "completed", "rejected", "cancelled")
STEP_MODES = ("all", "any")
STEP_STATUSES = ("active", "completed", "rejected", "cancelled")
ASSIGNEE_STATUSES = ("pending", "signed", "rejected", "skipped")
NOTIF_KINDS = (
    "assigned", "completed", "rejected", "cancelled",
    "task_assigned", "task_completed", "task_failed", "task_created", "task_edited", "follower_added",
)

# --- W5: Workflow engine (Workflow -> Stage -> Task -> Todo) ---
WF_VISIBILITIES = ("followers_only", "all_visible")
WF_STATUSES = ("active", "closed")
WF_ROLES = ("admin", "supervisor", "creator", "viewer")
STAGE_TYPES = ("normal", "sign", "approve")
ASSIGN_MODES = ("any", "all")
FIELD_TYPES = ("text", "textarea", "date", "file", "number", "select", "user")
TASK_STATUSES = ("processing", "completed", "failed")
TODO_STATUSES = ("doing", "done", "failed", "cancelled")

# text[] trên PostgreSQL, JSON trên SQLite (test cục bộ)
_LabelsType = JSON().with_variant(ARRAY(Text), "postgresql")


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
    signing_pin_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    pin_set_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    pin_failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    pin_fail_times_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pin_locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    job_title: Mapped[str] = mapped_column(String(120), default="")
    department: Mapped[str] = mapped_column(String(120), default="")
    manager_name: Mapped[str] = mapped_column(String(120), default="")
    manager_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    signature_image_file_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("files.id", ondelete="SET NULL", use_alter=True, name="fk_users_signature_image_file"),
        nullable=True,
    )
    onboarding_step: Mapped[int] = mapped_column(Integer, default=1)
    onboarding_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    accepted_terms_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    onboarding_otp_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    onboarding_otp_session_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
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
    # W6: liên kết ngược với công việc W5 nếu bước ký thuộc workflow engine
    task_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True)
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
    task_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # assigned | completed | rejected | cancelled (luồng ký) ; task_* | follower_added (workflow engine W5)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(String(500), default="")
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Workflow(Base):
    """Định nghĩa quy trình (W5): các stage tuần tự, form động, vai trò và lý do thất bại."""
    __tablename__ = "workflows"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(100), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    guide_html: Mapped[str] = mapped_column(Text, default="")
    visibility: Mapped[str] = mapped_column(String(16), default="followers_only")  # followers_only | all_visible
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | closed
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class WorkflowRole(Base):
    __tablename__ = "workflow_roles"
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    role: Mapped[str] = mapped_column(String(16), primary_key=True)  # admin | supervisor | creator | viewer


class WorkflowFailureReason(Base):
    __tablename__ = "workflow_failure_reasons"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(200))
    order: Mapped[int] = mapped_column("order", Integer, default=0)


class Stage(Base):
    __tablename__ = "stages"
    __table_args__ = (UniqueConstraint("workflow_id", "order", name="uq_stages_workflow_order"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"), index=True)
    order: Mapped[int] = mapped_column("order", Integer)
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(16), default="normal")  # normal | sign | approve
    sla_hours: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    guide_html: Mapped[str] = mapped_column(Text, default="")
    assign_mode: Mapped[str] = mapped_column(String(8), default="all")  # any | all
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class StageMember(Base):
    __tablename__ = "stage_members"
    stage_id: Mapped[int] = mapped_column(ForeignKey("stages.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)


class FormField(Base):
    __tablename__ = "form_fields"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(16))  # text|textarea|date|file|number|select|user
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    placeholder: Mapped[str] = mapped_column(String(200), default="")
    options: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    order: Mapped[int] = mapped_column("order", Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class WorkflowTemplate(Base):
    __tablename__ = "templates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(100), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    definition: Mapped[dict] = mapped_column(JSON)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    creator_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Task(Base):
    """Một công việc (job) chạy qua các stage của workflow."""
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_workflow_status", "workflow_id", "status"),
        Index("ix_tasks_current_stage", "current_stage_id"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id"), index=True)
    current_stage_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stages.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(255))
    labels: Mapped[Optional[list]] = mapped_column(_LabelsType, nullable=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    description_html: Mapped[str] = mapped_column(Text, default="")
    custom_values: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # Hạn xử lý của stage hiện tại (= thời điểm vào stage + sla_hours); suy ra "quá hạn" khi < now và đang processing
    deadline: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="processing")  # processing | completed | failed
    failure_reason_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("workflow_failure_reasons.id"), nullable=True
    )
    # Chỗ cắm cho W6 (bước ký): liên kết tới luồng ký/tài liệu hiện có
    document_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class TaskFollower(Base):
    __tablename__ = "task_followers"
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)


class Todo(Base):
    __tablename__ = "todos"
    __table_args__ = (
        Index("ix_todos_assignee_status", "assignee_id", "status"),
        Index("ix_todos_deadline", "deadline"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    stage_id: Mapped[int] = mapped_column(ForeignKey("stages.id"), index=True)
    assignee_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    deadline: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="doing")  # doing | done | failed | cancelled
    # W6: liên kết todo <-> bước ký của tài liệu để truy ngược giữa 2 luồng
    workflow_step_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("workflow_steps.id", ondelete="SET NULL"), nullable=True, index=True
    )
    step_assignee_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("step_assignees.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class TermsAcceptance(Base):
    """Bằng chứng đồng ý điều khoản cam kết sử dụng chữ ký số nội bộ."""
    __tablename__ = "terms_acceptances"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    username: Mapped[str] = mapped_column(String(64), default="")
    terms_version: Mapped[str] = mapped_column(String(64), index=True)
    terms_sha256: Mapped[str] = mapped_column(String(64))
    accepted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(500), default="")
    otp_method: Mapped[str] = mapped_column(String(24), default="totp")


class OnboardingOtp(Base):
    """Mã OTP dùng 1 lần gửi qua email khi xác nhận điều khoản (nếu chưa có TOTP)."""
    __tablename__ = "onboarding_otps"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(32), default="terms_accept")
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


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
    # W5: truy vết theo task/workflow (không FK để nhật ký bất biến độc lập với dữ liệu nghiệp vụ)
    task_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    workflow_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)


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
    if "users" in tables:
        if "email" not in cols["users"]:
            stmts.append("ALTER TABLE users ADD COLUMN email VARCHAR(255)")
        if "signing_pin_hash" not in cols["users"]:
            stmts.append("ALTER TABLE users ADD COLUMN signing_pin_hash VARCHAR(255)")
        if "pin_set_at" not in cols["users"]:
            stmts.append("ALTER TABLE users ADD COLUMN pin_set_at TIMESTAMP")
        if "pin_failed_attempts" not in cols["users"]:
            stmts.append("ALTER TABLE users ADD COLUMN pin_failed_attempts INTEGER NOT NULL DEFAULT 0")
        if "pin_fail_times_json" not in cols["users"]:
            stmts.append("ALTER TABLE users ADD COLUMN pin_fail_times_json TEXT")
        if "pin_locked_until" not in cols["users"]:
            stmts.append("ALTER TABLE users ADD COLUMN pin_locked_until TIMESTAMP")
        if "job_title" not in cols["users"]:
            stmts.append("ALTER TABLE users ADD COLUMN job_title VARCHAR(120) NOT NULL DEFAULT ''")
        if "department" not in cols["users"]:
            stmts.append("ALTER TABLE users ADD COLUMN department VARCHAR(120) NOT NULL DEFAULT ''")
        if "manager_name" not in cols["users"]:
            stmts.append("ALTER TABLE users ADD COLUMN manager_name VARCHAR(120) NOT NULL DEFAULT ''")
        if "manager_user_id" not in cols["users"]:
            stmts.append("ALTER TABLE users ADD COLUMN manager_user_id INTEGER")
        if "signature_image_file_id" not in cols["users"]:
            stmts.append("ALTER TABLE users ADD COLUMN signature_image_file_id VARCHAR(36)")
        if "onboarding_step" not in cols["users"]:
            stmts.append("ALTER TABLE users ADD COLUMN onboarding_step INTEGER NOT NULL DEFAULT 1")
        if "onboarding_completed_at" not in cols["users"]:
            stmts.append("ALTER TABLE users ADD COLUMN onboarding_completed_at TIMESTAMP")
        if "accepted_terms_version" not in cols["users"]:
            stmts.append("ALTER TABLE users ADD COLUMN accepted_terms_version VARCHAR(64)")
        if "onboarding_otp_verified_at" not in cols["users"]:
            stmts.append("ALTER TABLE users ADD COLUMN onboarding_otp_verified_at TIMESTAMP")
        if "onboarding_otp_session_id" not in cols["users"]:
            stmts.append("ALTER TABLE users ADD COLUMN onboarding_otp_session_id VARCHAR(64)")
    if "files" in tables and "is_workflow" not in cols["files"]:
        stmts.append("ALTER TABLE files ADD COLUMN is_workflow BOOLEAN NOT NULL DEFAULT FALSE")
    if "audit_logs" in tables:
        if "document_id" not in cols["audit_logs"]:
            stmts.append("ALTER TABLE audit_logs ADD COLUMN document_id VARCHAR(36)")
        stmts.append("CREATE INDEX IF NOT EXISTS ix_audit_logs_document_id ON audit_logs (document_id)")
        if "metadata_json" not in cols["audit_logs"]:
            stmts.append("ALTER TABLE audit_logs ADD COLUMN metadata_json TEXT")
        if "task_id" not in cols["audit_logs"]:
            stmts.append("ALTER TABLE audit_logs ADD COLUMN task_id INTEGER")
        if "workflow_id" not in cols["audit_logs"]:
            stmts.append("ALTER TABLE audit_logs ADD COLUMN workflow_id INTEGER")
        stmts.append("CREATE INDEX IF NOT EXISTS ix_audit_logs_task_id ON audit_logs (task_id)")
        stmts.append("CREATE INDEX IF NOT EXISTS ix_audit_logs_workflow_id ON audit_logs (workflow_id)")
    # Bảng workflow mới (tasks...) đã được create_all tạo trước khi chạy migrate_db
    if "notifications" in tables and "task_id" not in cols["notifications"]:
        stmts.append(
            "ALTER TABLE notifications ADD COLUMN task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE"
        )
        stmts.append("CREATE INDEX IF NOT EXISTS ix_notifications_task_id ON notifications (task_id)")
    if "workflow_steps" in tables and "task_id" not in cols["workflow_steps"]:
        stmts.append("ALTER TABLE workflow_steps ADD COLUMN task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL")
        stmts.append("CREATE INDEX IF NOT EXISTS ix_workflow_steps_task_id ON workflow_steps (task_id)")
    if "todos" in tables:
        if "workflow_step_id" not in cols["todos"]:
            stmts.append(
                "ALTER TABLE todos ADD COLUMN workflow_step_id INTEGER REFERENCES workflow_steps(id) ON DELETE SET NULL"
            )
            stmts.append("CREATE INDEX IF NOT EXISTS ix_todos_workflow_step_id ON todos (workflow_step_id)")
        if "step_assignee_id" not in cols["todos"]:
            stmts.append(
                "ALTER TABLE todos ADD COLUMN step_assignee_id INTEGER REFERENCES step_assignees(id) ON DELETE SET NULL"
            )
            stmts.append("CREATE INDEX IF NOT EXISTS ix_todos_step_assignee_id ON todos (step_assignee_id)")
    if "stages" in tables and "is_active" not in cols["stages"]:
        stmts.append("ALTER TABLE stages ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE")
    if "form_fields" in tables and "is_active" not in cols["form_fields"]:
        stmts.append("ALTER TABLE form_fields ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE")
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
