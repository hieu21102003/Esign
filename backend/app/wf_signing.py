"""Tích hợp bước ký & duyệt W1–W4 vào Workflow Engine W5 qua StageHandler hooks (W6)."""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import func, or_, select

from .audit import audit
from .models import (
    Certificate,
    Document,
    DocumentVersion,
    SignJob,
    Stage,
    StepAssignee,
    StoredFile,
    Task,
    Todo,
    User,
    WorkflowStep,
)
from .wf_hooks import StageContext, StageHandler, register_stage_handler


class SignStageHandler(StageHandler):
    """Handler cho stage type='sign': gắn kết với luồng ký Document/WorkflowStep/StepAssignee hiện có."""

    def on_enter(self, ctx: StageContext, todos: List[Todo]) -> None:
        from .routes_workflow import _create_step

        db, task, stage, now, ip = ctx.db, ctx.task, ctx.stage, ctx.now, ctx.ip
        if not task.document_id:
            raise HTTPException(
                409,
                "Công việc chưa đính kèm tài liệu; cần đính kèm tài liệu trước khi chuyển sang bước ký",
            )
        doc = db.get(Document, task.document_id)
        if doc is None or doc.status in ("rejected", "cancelled"):
            raise HTTPException(409, "Tài liệu gắn với công việc không còn hợp lệ để ký")
        cur_file = db.get(StoredFile, doc.current_file_id)
        if cur_file is None or cur_file.deleted_at is not None:
            raise HTTPException(409, "File của tài liệu gắn với công việc không còn tồn tại")
        cur_file.is_workflow = True

        # Khóa phiên bản hiện có để truy vết (nếu chưa có bản ghi DocumentVersion thì tạo v0)
        max_ver = db.scalar(select(func.max(DocumentVersion.version_no)).where(DocumentVersion.document_id == doc.id))
        if max_ver is None:
            db.add(DocumentVersion(
                document_id=doc.id,
                version_no=0,
                file_id=cur_file.id,
                step_id=None,
                created_by_id=ctx.actor.id if ctx.actor else task.creator_id,
                note="Tài liệu gốc",
                created_at=now,
            ))
            locked_ver = 0
        else:
            locked_ver = int(max_ver)

        max_step = db.scalar(select(func.max(WorkflowStep.step_order)).where(WorkflowStep.document_id == doc.id))
        next_order = (max_step or 0) + 1
        assigned_by_id = ctx.actor.id if ctx.actor else task.creator_id
        assignee_ids = [t.assignee_id for t in todos]

        step = _create_step(db, doc, next_order, stage.assign_mode, assigned_by_id, assignee_ids, now)
        step.task_id = task.id
        doc.current_step_order = step.step_order
        doc.status = "in_progress"
        doc.completed_at = None
        doc.updated_at = now
        db.flush()

        sa_by_uid = {
            sa.user_id: sa.id
            for sa in db.scalars(select(StepAssignee).where(StepAssignee.step_id == step.id))
        }
        for td in todos:
            td.workflow_step_id = step.id
            td.step_assignee_id = sa_by_uid.get(td.assignee_id)
        db.flush()

        audit(
            db, ip, "doc_assign", user=ctx.actor, document_id=doc.id, file_id=doc.current_file_id,
            task_id=task.id, workflow_id=task.workflow_id,
            metadata={
                "step_id": step.id,
                "step_order": step.step_order,
                "mode": stage.assign_mode,
                "assignee_ids": assignee_ids,
                "stage_id": stage.id,
                "locked_version_no": locked_ver,
            },
            commit=False,
        )

    def before_complete(self, ctx: StageContext, todo: Todo, payload: dict) -> None:
        if payload.get("_signed_verified") is not True:
            raise HTTPException(
                409,
                f"Bước ký không thể hoàn thành thủ công; vui lòng ký số tài liệu qua luồng ký "
                f"(POST /api/documents/{ctx.task.document_id}/sign hoặc POST /api/tasks/{ctx.task.id}/sign)",
            )
        sa = ctx.db.get(StepAssignee, todo.step_assignee_id) if todo.step_assignee_id else None
        if sa is None or sa.status != "signed" or sa.sign_job_id is None:
            raise HTTPException(409, "Không tìm thấy chữ ký số hợp lệ cho lượt ký này")


class ApproveStageHandler(StageHandler):
    """Handler cho stage type='approve': hoàn thành bằng decision='approve' hoặc 'reject'."""

    def before_complete(self, ctx: StageContext, todo: Todo, payload: dict) -> None:  # noqa: ARG002
        if payload.get("decision") != "approve":
            raise HTTPException(400, "Bước duyệt yêu cầu decision='approve' hoặc 'reject'")


def register_w6_handlers() -> None:
    """Đăng ký handler cho stage sign và approve lúc khởi chạy ứng dụng."""
    register_stage_handler("sign", SignStageHandler())
    register_stage_handler("approve", ApproveStageHandler())


def complete_linked_document_if_done(db, task: Task, actor: Optional[User], now: datetime, ip: str) -> None:
    """Khi task đi hết các bước và chuyển completed -> chốt trạng thái tài liệu liên kết."""
    if not task.document_id:
        return
    doc = db.get(Document, task.document_id)
    if doc is None or doc.status != "in_progress":
        return
    doc.status = "completed"
    doc.completed_at = now
    doc.updated_at = now
    cur_file = db.get(StoredFile, doc.current_file_id)
    final_ver = db.scalar(select(func.max(DocumentVersion.version_no)).where(DocumentVersion.document_id == doc.id))
    audit(
        db, ip, "doc_complete", user=actor, document_id=doc.id,
        file_id=doc.current_file_id, output_sha256=cur_file.sha256 if cur_file else None,
        task_id=task.id, workflow_id=task.workflow_id,
        metadata={"final_version_no": final_ver if final_ver is not None else 0, "source": "workflow_task"},
        commit=False,
    )


def cancel_open_sign_steps_for_task(
    db, task: Task, actor: Optional[User], now: datetime, ip: str,
    *, reason: str = "", doc_status: str = "cancelled",
) -> None:
    """Hủy toàn bộ WorkflowStep đang active khi task bị fail/cancel/archive."""
    if not task.document_id:
        return
    doc = db.get(Document, task.document_id)
    if doc is None:
        return
    active_steps = list(db.scalars(
        select(WorkflowStep).where(
            WorkflowStep.document_id == doc.id,
            WorkflowStep.status == "active",
            or_(WorkflowStep.task_id == task.id, WorkflowStep.task_id.is_(None)),
        )
    ))
    for stp in active_steps:
        stp.status = "cancelled"
        stp.completed_at = now
        for a in db.scalars(select(StepAssignee).where(StepAssignee.step_id == stp.id, StepAssignee.status == "pending")):
            a.status = "skipped"

    doc_changed = False
    if doc.status == "in_progress" and doc_status == "cancelled":
        doc.status = "cancelled"
        doc.cancelled_at = now
        doc.cancel_reason = (reason or "Công việc đã dừng/hủy")[:500]
        doc.updated_at = now
        doc_changed = True

    if active_steps or doc_changed:
        audit(
            db, ip, "doc_cancel", user=actor, document_id=doc.id, file_id=doc.current_file_id,
            task_id=task.id, workflow_id=task.workflow_id,
            detail=(reason[:500] if reason else None),
            metadata={"reason": reason or None, "source": "workflow_task"},
            commit=False,
        )


def has_sign_stage_started(db, task: Task) -> bool:
    """True nếu task đã vào stage ký hoặc đã phát sinh bước ký trên tài liệu."""
    if task.current_stage_id:
        cur_stage = db.get(Stage, task.current_stage_id)
        if cur_stage is not None and cur_stage.type == "sign":
            return True
    if db.scalar(select(WorkflowStep.id).where(WorkflowStep.task_id == task.id).limit(1)) is not None:
        return True
    if (
        db.scalar(
            select(Todo.id)
            .join(Stage, Stage.id == Todo.stage_id)
            .where(Todo.task_id == task.id, Stage.type == "sign")
            .limit(1)
        )
        is not None
    ):
        return True
    return False


def build_task_document_summary(db, task: Task) -> Optional[dict]:
    """Tổng hợp thông tin tài liệu gắn task, danh sách chữ ký và trạng thái bước ký hiện tại."""
    from .routes_workflow import _user_brief

    if not task.document_id:
        return None
    doc = db.get(Document, task.document_id)
    if doc is None:
        return None
    cur_file = db.get(StoredFile, doc.current_file_id)
    versions = list(db.scalars(
        select(DocumentVersion).where(DocumentVersion.document_id == doc.id).order_by(DocumentVersion.version_no)
    ))
    steps = list(db.scalars(
        select(WorkflowStep).where(WorkflowStep.document_id == doc.id).order_by(WorkflowStep.step_order)
    ))
    step_ids = [s.id for s in steps]
    assignees_by_step: Dict[int, List[StepAssignee]] = {sid: [] for sid in step_ids}
    if step_ids:
        for a in db.scalars(select(StepAssignee).where(StepAssignee.step_id.in_(step_ids)).order_by(StepAssignee.id)):
            assignees_by_step[a.step_id].append(a)

    job_ids = {a.sign_job_id for lst in assignees_by_step.values() for a in lst if a.sign_job_id is not None}
    jobs = {j.id: j for j in db.scalars(select(SignJob).where(SignJob.id.in_(job_ids)))} if job_ids else {}
    cert_ids = {j.cert_id for j in jobs.values()}
    certs = {c.id: c for c in db.scalars(select(Certificate).where(Certificate.id.in_(cert_ids)))} if cert_ids else {}
    uids = {doc.creator_id}
    for lst in assignees_by_step.values():
        uids.update(a.user_id for a in lst)
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(uids)))} if uids else {}

    ver_by_file = {v.file_id: v.version_no for v in versions}
    signatures = []
    for stp in steps:
        signed_items = [a for a in assignees_by_step.get(stp.id, []) if a.status == "signed"]
        signed_items.sort(key=lambda a: (a.signed_at or datetime.min, a.id))
        for a in signed_items:
            job = jobs.get(a.sign_job_id) if a.sign_job_id else None
            cert = certs.get(job.cert_id) if job else None
            signatures.append({
                "step_id": stp.id,
                "step_order": stp.step_order,
                "signer": _user_brief(users.get(a.user_id), a.user_id),
                "signed_at": a.signed_at.isoformat() if a.signed_at else None,
                "remark": a.remark,
                "signed_file_id": a.signed_file_id,
                "sign_job_id": a.sign_job_id,
                "version_no": ver_by_file.get(a.signed_file_id),
                "cert_key_ref": cert.key_ref if cert else None,
            })

    cur_step = next((s for s in steps if s.status == "active"), None)
    if cur_step is None and doc.current_step_order:
        cur_step = next((s for s in steps if s.step_order == doc.current_step_order), None)
    if cur_step is None and steps:
        cur_step = steps[-1]

    cur_step_out = None
    if cur_step is not None:
        cur_step_out = {
            "id": cur_step.id,
            "step_order": cur_step.step_order,
            "mode": cur_step.mode,
            "status": cur_step.status,
            "created_at": cur_step.created_at.isoformat(),
            "completed_at": cur_step.completed_at.isoformat() if cur_step.completed_at else None,
            "assignees": [
                {
                    "id": a.id,
                    "user": _user_brief(users.get(a.user_id), a.user_id),
                    "status": a.status,
                    "signed_at": a.signed_at.isoformat() if a.signed_at else None,
                    "remark": a.remark,
                    "signed_file_id": a.signed_file_id,
                }
                for a in assignees_by_step.get(cur_step.id, [])
            ],
        }

    return {
        "id": doc.id,
        "name": doc.title,
        "title": doc.title,
        "status": doc.status,
        "original_file_id": doc.original_file_id,
        "current_file_id": doc.current_file_id,
        "file_name": cur_file.original_name if cur_file else "",
        "current_version_no": versions[-1].version_no if versions else 0,
        "versions": [
            {
                "version_no": v.version_no,
                "file_id": v.file_id,
                "step_id": v.step_id,
                "note": v.note,
                "created_at": v.created_at.isoformat(),
            }
            for v in versions
        ],
        "signatures": signatures,
        "current_sign_step": cur_step_out,
    }
