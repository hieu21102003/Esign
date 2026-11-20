"""API workflow engine (W5): quy trình, công việc (task), việc cần làm (todo), bảng Kanban, tìm kiếm, xuất CSV."""
from __future__ import annotations

import csv
import io
import re
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import Text, and_, case, cast, delete, false, func, or_, select
from starlette.concurrency import run_in_threadpool

from .audit import audit, audit_event_out
from .deps import Ctx, get_db, rate_limit, require_admin, require_mfa
from .html_sanitize import MAX_HTML_LEN, sanitize_html
from .models import (
    AuditLog,
    Document,
    DocumentVersion,
    FormField,
    Stage,
    StageMember,
    StoredFile,
    Task,
    TaskFollower,
    Todo,
    User,
    Workflow,
    WorkflowFailureReason,
    WorkflowRole,
    WorkflowStep,
    WorkflowTemplate,
    utcnow,
)
from .notifications import dispatch_emails
from .pin import now_from_request
from .routes_files import ingest_uploaded_file
from .routes_workflow import (
    DocumentSignIn,
    RejectIn,
    _pdf_response,
    _resolve_doc_file,
    _user_brief,
    reject_document,
    sign_document,
)
from .services import signer_verify
from .storage import read_bytes, remove_from_disk
from .wf_builder import (
    add_single_stage,
    clone_workflow,
    compute_workflow_warnings,
    create_workflow_from_template,
    delete_or_deactivate_stage,
    field_ids_with_data,
    patch_fields_with_rules,
    patch_stages_with_rules,
    reorder_active_stages,
    save_workflow_as_template,
    stage_usage_stats,
    update_single_stage,
    validate_stage_specs,
)
from .wf_engine import (
    add_follower,
    can_manage_workflow,
    can_see_workflow,
    cancel_task,
    clean_labels,
    clean_line,
    complete_todo,
    create_task,
    edit_task_notify,
    fail_todo,
    get_visible_task,
    ordered_stages,
    task_is_overdue,
    task_lock,
    todo_is_overdue,
    validate_custom_values,
    visible_task_clause,
    workflow_roles_of,
)
from .wf_templates import VALID_ASSIGN_MODES, VALID_FIELD_TYPES, VALID_STAGE_TYPES
from .wf_signing import (
    build_task_document_summary,
    cancel_open_sign_steps_for_task,
    has_sign_stage_started,
)

router = APIRouter(prefix="/api", tags=["workflow-engine"])

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,98}$")
MAX_PAGE_SIZE = 100
CSV_LIMIT = 5000


# ======================================================================================
# Schemas
# ======================================================================================
class StageIn(BaseModel):
    id: Optional[int] = None
    name: str = Field(min_length=1, max_length=200)
    type: Literal["normal", "sign", "approve"] = "normal"
    sla_hours: Optional[int] = Field(None, ge=1, le=8760)
    guide_html: str = Field("", max_length=MAX_HTML_LEN)
    assign_mode: Literal["any", "all"] = "all"
    member_ids: List[int] = Field(default_factory=list, max_length=200)
    is_active: bool = True
    insert_after_order: Optional[int] = None


class StageSinglePatchIn(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    type: Optional[Literal["normal", "sign", "approve"]] = None
    sla_hours: Optional[int] = Field(None, ge=1, le=8760)
    guide_html: Optional[str] = Field(None, max_length=MAX_HTML_LEN)
    assign_mode: Optional[Literal["any", "all"]] = None
    member_ids: Optional[List[int]] = Field(None, max_length=200)
    is_active: Optional[bool] = None


class FieldIn(BaseModel):
    id: Optional[int] = None
    label: str = Field(min_length=1, max_length=200)
    type: Literal["text", "textarea", "date", "file", "number", "select", "user"]
    required: bool = False
    placeholder: str = Field("", max_length=200)
    options: Optional[List[str]] = Field(None, max_length=100)
    is_active: bool = True


class FieldSinglePatchIn(BaseModel):
    label: Optional[str] = Field(None, min_length=1, max_length=200)
    type: Optional[Literal["text", "textarea", "date", "file", "number", "select", "user"]] = None
    required: Optional[bool] = None
    placeholder: Optional[str] = Field(None, max_length=200)
    options: Optional[List[str]] = Field(None, max_length=100)
    order: Optional[int] = Field(None, ge=1, le=200)
    is_active: Optional[bool] = None


class RoleIn(BaseModel):
    user_id: int
    role: Literal["admin", "supervisor", "creator", "viewer"]


class RolesBulkIn(BaseModel):
    roles: List[RoleIn] = Field(min_length=1, max_length=500)


class ReasonIn(BaseModel):
    id: Optional[int] = None
    label: str = Field(min_length=1, max_length=200)


class ReasonPatchIn(BaseModel):
    label: Optional[str] = Field(None, min_length=1, max_length=200)
    order: Optional[int] = Field(None, ge=1, le=200)


class WorkflowCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: Optional[str] = Field(None, max_length=100)
    category: str = Field("", max_length=100)
    description: str = Field("", max_length=5000)
    guide_html: str = Field("", max_length=MAX_HTML_LEN)
    visibility: Literal["followers_only", "all_visible"] = "followers_only"
    status: Literal["active", "closed"] = "active"
    stages: List[StageIn] = Field(min_length=1, max_length=50)
    form_fields: List[FieldIn] = Field(default_factory=list, max_length=50)
    roles: List[RoleIn] = Field(default_factory=list, max_length=500)
    failure_reasons: List[ReasonIn] = Field(default_factory=list, max_length=50)


class WorkflowFromTemplateIn(BaseModel):
    template_id: int
    name: Optional[str] = Field(None, max_length=200)
    slug: Optional[str] = Field(None, max_length=100)
    category: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = Field(None, max_length=5000)
    visibility: Optional[Literal["followers_only", "all_visible"]] = None
    status: Literal["active", "closed"] = "active"
    admin_ids: List[int] = Field(default_factory=list, max_length=50)
    stage_members: Optional[Dict[str, List[int]]] = None
    member_ids: Optional[List[int]] = Field(None, max_length=200)


class CloneWorkflowIn(BaseModel):
    name: Optional[str] = Field(None, max_length=200)
    slug: Optional[str] = Field(None, max_length=100)


class SaveAsTemplateIn(BaseModel):
    name: Optional[str] = Field(None, max_length=200)
    category: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = Field(None, max_length=5000)


class ReorderIn(BaseModel):
    stage_ids: List[int] = Field(min_length=1, max_length=50)


class StageMembersIn(BaseModel):
    user_ids: List[int] = Field(min_length=1, max_length=200)


class WorkflowPatchIn(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    category: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = Field(None, max_length=5000)
    guide_html: Optional[str] = Field(None, max_length=MAX_HTML_LEN)
    visibility: Optional[Literal["followers_only", "all_visible"]] = None
    status: Optional[Literal["active", "closed"]] = None
    stages: Optional[List[StageIn]] = Field(None, min_length=1, max_length=50)
    form_fields: Optional[List[FieldIn]] = Field(None, max_length=50)
    roles: Optional[List[RoleIn]] = Field(None, max_length=500)
    failure_reasons: Optional[List[ReasonIn]] = Field(None, max_length=50)


class TaskCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    labels: List[str] = Field(default_factory=list, max_length=20)
    description_html: str = Field("", max_length=MAX_HTML_LEN)
    custom_values: Optional[Dict[str, Any]] = None
    follower_ids: List[int] = Field(default_factory=list, max_length=50)
    assignee_ids: Optional[List[int]] = Field(None, max_length=200)


class TaskPatchIn(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    labels: Optional[List[str]] = Field(None, max_length=20)
    description_html: Optional[str] = Field(None, max_length=MAX_HTML_LEN)
    custom_values: Optional[Dict[str, Any]] = None


class FollowerIn(BaseModel):
    user_id: int


class TodoCompleteIn(BaseModel):
    result: Optional[str] = Field(None, max_length=4000)
    next_assignee_ids: Optional[List[int]] = Field(None, max_length=200)
    decision: Optional[Literal["approve", "reject"]] = None
    failure_reason_id: Optional[int] = None
    note: Optional[str] = Field(None, max_length=2000)


class TodoFailIn(BaseModel):
    failure_reason_id: int
    note: Optional[str] = Field(None, max_length=2000)
    pin: Optional[str] = None


class TaskCancelIn(BaseModel):
    reason: str = Field("", max_length=500)
    failure_reason_id: Optional[int] = None


# ======================================================================================
# Helpers
# ======================================================================================
def _now(request: Request) -> datetime:
    return now_from_request(request)[1]


def _slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name.replace("đ", "d").replace("Đ", "D"))
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:100].strip("-")
    return s or "workflow"


def _users_map(db, ids) -> Dict[int, User]:
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    return {u.id: u for u in db.scalars(select(User).where(User.id.in_(ids)))}


def _require_active_users(db, ids: List[int], what: str) -> None:
    ids = set(ids)
    if not ids:
        return
    found = set(db.scalars(select(User.id).where(User.id.in_(ids), User.is_active.is_(True))))
    if found != ids:
        raise HTTPException(400, f"{what} gồm người dùng không tồn tại hoặc đã bị vô hiệu hóa")


def _load_workflow(db, user: User, workflow_id: int) -> Workflow:
    wf = db.get(Workflow, workflow_id)
    if wf is None or not can_see_workflow(db, user, workflow_id):
        raise HTTPException(404, "Không tìm thấy quy trình")
    return wf


def _parse_dt(value: Optional[str], name: str, *, end: bool = False) -> tuple[Optional[datetime], bool]:
    """Trả (datetime, exclusive). Chỉ ngày (YYYY-MM-DD) ở cận trên được hiểu là hết ngày đó (exclusive +1 ngày)."""
    if not value:
        return None, False
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError:
        raise HTTPException(400, f"{name} không hợp lệ (dùng ISO 8601, ví dụ 2026-01-31 hoặc 2026-01-31T10:00:00)") from None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    if end and len(value.strip()) == 10:
        return dt + timedelta(days=1), True
    return dt, False


def _range(col, frm: Optional[str], to: Optional[str], name: str) -> list:
    conds = []
    f, _ = _parse_dt(frm, f"{name}_from")
    t, excl = _parse_dt(to, f"{name}_to", end=True)
    if f is not None:
        conds.append(col >= f)
    if t is not None:
        conds.append(col < t if excl else col <= t)
    return conds


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


# ======================================================================================
# Serializers
# ======================================================================================
def _stage_out(s: Stage, members: Optional[List[dict]] = None, stats: Optional[dict] = None) -> dict:
    out = {
        "id": s.id, "order": s.order, "name": s.name, "type": s.type, "sla_hours": s.sla_hours,
        "assign_mode": s.assign_mode, "guide_html": s.guide_html or "", "is_active": bool(s.is_active),
    }
    if members is not None:
        out["members"] = members
    if stats is not None:
        st = stats.get(s.id, {})
        out["processing_count"] = int(st.get("processing_count", 0))
        out["has_history"] = bool(st.get("history_count", 0) > 0)
    return out


def _template_out(tpl: WorkflowTemplate) -> dict:
    return {
        "id": tpl.id,
        "slug": tpl.slug,
        "name": tpl.name,
        "category": tpl.category,
        "description": tpl.description,
        "definition": tpl.definition,
        "is_builtin": bool(tpl.is_builtin),
        "creator_id": tpl.creator_id,
        "created_at": _iso(tpl.created_at),
    }


def _workflow_summary(db, wf: Workflow, viewer: User) -> dict:
    return {
        "id": wf.id, "name": wf.name, "slug": wf.slug, "category": wf.category, "description": wf.description,
        "visibility": wf.visibility, "status": wf.status, "creator_id": wf.creator_id,
        "created_at": _iso(wf.created_at),
        "stage_count": db.scalar(select(func.count(Stage.id)).where(Stage.workflow_id == wf.id, Stage.is_active.is_(True))) or 0,
        "my_roles": sorted(workflow_roles_of(db, viewer.id, wf.id)),
        "can_manage": can_manage_workflow(db, viewer, wf.id),
    }


def _workflow_detail(db, wf: Workflow, viewer: User) -> dict:
    stages = ordered_stages(db, wf.id, include_inactive=True)
    member_rows = db.execute(
        select(StageMember.stage_id, StageMember.user_id).where(StageMember.stage_id.in_([s.id for s in stages]))
    ).all() if stages else []
    users = _users_map(db, {uid for _, uid in member_rows})
    members_by_stage: Dict[int, List[dict]] = {}
    for sid, uid in sorted(member_rows, key=lambda r: r[1]):
        members_by_stage.setdefault(sid, []).append(_user_brief(users.get(uid), uid))
    fields = list(db.scalars(select(FormField).where(FormField.workflow_id == wf.id).order_by(FormField.order, FormField.id)))
    used_fields = field_ids_with_data(db, wf.id)
    stats = stage_usage_stats(db, wf.id)
    reasons = list(db.scalars(
        select(WorkflowFailureReason).where(WorkflowFailureReason.workflow_id == wf.id)
        .order_by(WorkflowFailureReason.order, WorkflowFailureReason.id)
    ))
    my_roles = workflow_roles_of(db, viewer.id, wf.id)
    out = _workflow_summary(db, wf, viewer)
    out.update({
        "guide_html": wf.guide_html or "",
        "stages": [_stage_out(s, members_by_stage.get(s.id, []), stats) for s in stages],
        "form_fields": [{
            "id": f.id, "label": f.label, "type": f.type, "required": f.required,
            "placeholder": f.placeholder, "options": f.options, "order": f.order,
            "is_active": bool(f.is_active), "has_data": f.id in used_fields,
        } for f in fields],
        "failure_reasons": [{"id": r.id, "label": r.label, "order": r.order} for r in reasons],
        "can_manage": can_manage_workflow(db, viewer, wf.id),
        "can_create_task": bool(my_roles & {"admin", "creator"}) and wf.status == "active",
        "processing_count": sum(st.get("processing_count", 0) for st in stats.values()),
        "has_history": any(st.get("history_count", 0) > 0 for st in stats.values()),
        "warnings": compute_workflow_warnings(db, wf.id),
    })
    # Danh sách vai trò chỉ lộ cho admin/supervisor/quản trị hệ thống
    if my_roles & {"admin", "supervisor"} or viewer.role == "admin":
        roles = list(db.scalars(select(WorkflowRole).where(WorkflowRole.workflow_id == wf.id)))
        rusers = _users_map(db, {r.user_id for r in roles})
        out["roles"] = [
            {"user": _user_brief(rusers.get(r.user_id), r.user_id), "role": r.role}
            for r in sorted(roles, key=lambda r: (r.user_id, r.role))
        ]
    return out


def _task_items(db, tasks: List[Task], now: datetime) -> List[dict]:
    """Tóm tắt task cho danh sách/board (nạp theo lô để tránh N+1)."""
    if not tasks:
        return []
    tids = [t.id for t in tasks]
    wfs = {w.id: w for w in db.scalars(select(Workflow).where(Workflow.id.in_({t.workflow_id for t in tasks})))}
    stage_ids = {t.current_stage_id for t in tasks if t.current_stage_id}
    stages = {s.id: s for s in db.scalars(select(Stage).where(Stage.id.in_(stage_ids)))} if stage_ids else {}
    doing = db.execute(
        select(Todo.task_id, Todo.assignee_id).where(Todo.task_id.in_(tids), Todo.status == "doing")
    ).all()
    users = _users_map(db, {t.creator_id for t in tasks} | {a for _, a in doing})
    assignees: Dict[int, List[dict]] = {}
    for tid, uid in doing:
        u = users.get(uid)
        assignees.setdefault(tid, []).append({"id": uid, "display_name": (u.display_name or u.username) if u else ""})
    out = []
    for t in tasks:
        stg = stages.get(t.current_stage_id)
        cu = users.get(t.creator_id)
        out.append({
            "id": t.id, "workflow_id": t.workflow_id,
            "workflow_name": wfs[t.workflow_id].name if t.workflow_id in wfs else "",
            "title": t.title, "labels": list(t.labels or []), "status": t.status,
            "is_overdue": task_is_overdue(t, now), "deadline": _iso(t.deadline),
            "current_stage": {"id": stg.id, "order": stg.order, "name": stg.name} if stg else None,
            "creator": {"id": t.creator_id, "display_name": (cu.display_name or cu.username) if cu else ""},
            "assignees": assignees.get(t.id, []),
            "document_id": t.document_id,
            "created_at": _iso(t.created_at), "updated_at": _iso(t.updated_at),
            "completed_at": _iso(t.completed_at), "archived_at": _iso(t.archived_at),
        })
    return out


def _todo_out(todo: Todo, task: Task, stage: Optional[Stage], users: Dict[int, User], now: datetime) -> dict:
    return {
        "id": todo.id, "task_id": todo.task_id, "stage_id": todo.stage_id,
        "stage_name": stage.name if stage else "", "stage_order": stage.order if stage else None,
        "assignee": _user_brief(users.get(todo.assignee_id), todo.assignee_id),
        "result": todo.result, "status": todo.status, "deadline": _iso(todo.deadline),
        "is_overdue": todo_is_overdue(todo, task, now),
        "workflow_step_id": todo.workflow_step_id,
        "step_assignee_id": todo.step_assignee_id,
        "created_at": _iso(todo.created_at), "completed_at": _iso(todo.completed_at),
    }


def _task_detail(db, task: Task, viewer: User, now: datetime) -> dict:
    wf = db.get(Workflow, task.workflow_id)
    all_stages = ordered_stages(db, task.workflow_id, include_inactive=True)
    cur = next((s for s in all_stages if s.id == task.current_stage_id), None)
    todos = list(db.scalars(select(Todo).where(Todo.task_id == task.id).order_by(Todo.id)))
    follower_ids = list(db.scalars(select(TaskFollower.user_id).where(TaskFollower.task_id == task.id)))
    users = _users_map(db, {task.creator_id} | {t.assignee_id for t in todos} | set(follower_ids))
    stage_by_id = {s.id: s for s in all_stages}
    visited_ids = {t.stage_id for t in todos} | ({task.current_stage_id} if task.current_stage_id else set())
    stages = [s for s in all_stages if s.is_active or s.id in visited_ids]

    def state(s: Stage) -> str:
        if task.status == "completed":
            return "done"
        if cur is not None and s.id == cur.id:
            return "failed" if task.status == "failed" else "current"
        if not s.is_active:
            if any(t.stage_id == s.id and t.status == "failed" for t in todos):
                return "failed"
            return "done"
        if cur is None:
            return "pending"
        if not cur.is_active:
            return "done" if s.id in visited_ids else "pending"
        if s.order < cur.order:
            return "done"
        return "pending"

    values = task.custom_values or {}
    all_fields = list(db.scalars(select(FormField).where(FormField.workflow_id == task.workflow_id)
                                 .order_by(FormField.order, FormField.id)))
    fields = [f for f in all_fields if f.is_active or str(f.id) in values]
    reason = db.get(WorkflowFailureReason, task.failure_reason_id) if task.failure_reason_id else None
    roles = workflow_roles_of(db, viewer.id, task.workflow_id)
    privileged = task.creator_id == viewer.id or "admin" in roles
    open_task = task.archived_at is None
    act_cond = AuditLog.task_id == task.id
    if task.document_id:
        act_cond = or_(act_cond, AuditLog.document_id == task.document_id)
    activity = [audit_event_out(a) for a in db.scalars(
        select(AuditLog).where(act_cond).order_by(AuditLog.id).limit(500)
    )]
    return {
        "id": task.id,
        "workflow": {"id": wf.id, "name": wf.name, "slug": wf.slug, "visibility": wf.visibility, "status": wf.status},
        "title": task.title, "labels": list(task.labels or []), "description_html": task.description_html or "",
        "custom_values": values,
        "form": [{
            "field_id": f.id, "label": f.label, "type": f.type,
            "value": values.get(str(f.id)), "is_active": bool(f.is_active),
        } for f in fields],
        "status": task.status, "is_overdue": task_is_overdue(task, now), "deadline": _iso(task.deadline),
        "current_stage": _stage_out(cur) if cur else None,
        "stages": [{**_stage_out(s), "state": state(s)} for s in stages],
        "failure_reason": {"id": reason.id, "label": reason.label} if reason else None,
        "creator": _user_brief(users.get(task.creator_id), task.creator_id),
        "followers": [_user_brief(users.get(u), u) for u in sorted(follower_ids)],
        "todos": [_todo_out(t, task, stage_by_id.get(t.stage_id), users, now) for t in todos],
        "document_id": task.document_id,
        "document": build_task_document_summary(db, task),
        "created_at": _iso(task.created_at), "updated_at": _iso(task.updated_at),
        "completed_at": _iso(task.completed_at), "archived_at": _iso(task.archived_at),
        "permissions": {
            "can_edit": privileged and open_task,
            "can_add_followers": privileged and open_task,
            "can_archive": privileged and open_task and task.status != "processing",
            "can_restore": privileged and task.archived_at is not None,
            "my_todo_ids": [t.id for t in todos if t.assignee_id == viewer.id and t.status == "doing"],
        },
        "activity": activity,
    }


# ======================================================================================
# Workflows: tạo / xem / sửa
# ======================================================================================
def _create_stages(db, wf: Workflow, stages_in: List[StageIn], *, allow_empty_members: bool = False) -> None:
    validate_stage_specs(stages_in, allow_empty_members=allow_empty_members)
    for idx, s in enumerate(stages_in, start=1):
        _require_active_users(db, s.member_ids, f"Thành viên bước '{s.name}'")
        row = Stage(
            workflow_id=wf.id, order=idx, name=clean_line(s.name, "Tên bước", max_len=200), type=s.type,
            sla_hours=s.sla_hours or None, guide_html=sanitize_html(s.guide_html),
            assign_mode=s.assign_mode, is_active=True,
        )
        db.add(row)
        db.flush()
        for uid in sorted(set(s.member_ids)):
            db.add(StageMember(stage_id=row.id, user_id=uid))
    db.flush()


def _create_fields(db, wf: Workflow, fields_in: List[FieldIn]) -> None:
    for idx, f in enumerate(fields_in, start=1):
        if f.type == "select":
            opts = [clean_line(o, "Lựa chọn", max_len=200) for o in (f.options or [])]
            if not opts:
                raise HTTPException(400, f"Trường '{f.label}' (select) cần ít nhất 1 lựa chọn")
        else:
            opts = None
        db.add(FormField(
            workflow_id=wf.id, label=clean_line(f.label, "Nhãn trường", max_len=200), type=f.type,
            required=f.required, placeholder=clean_line(f.placeholder, "Placeholder", max_len=200, required=False),
            options=opts, order=idx, is_active=True,
        ))
    db.flush()


def _set_roles(db, wf: Workflow, roles_in: List[RoleIn], *, ensure_admin_id: Optional[int] = None) -> dict:
    old_pairs = sorted((r.user_id, r.role) for r in db.scalars(select(WorkflowRole).where(WorkflowRole.workflow_id == wf.id)))
    pairs = {(r.user_id, r.role) for r in roles_in}
    if ensure_admin_id is not None and not any(role == "admin" for _, role in pairs):
        pairs.add((ensure_admin_id, "admin"))
    if not any(role == "admin" for _, role in pairs):
        raise HTTPException(400, "Quy trình phải có ít nhất 1 admin")
    uids = {u for u, _ in pairs}
    found = set(db.scalars(select(User.id).where(User.id.in_(uids), User.is_active.is_(True))))
    if found != uids:
        raise HTTPException(400, "Danh sách vai trò gồm người dùng không tồn tại hoặc đã bị vô hiệu hóa")
    db.execute(delete(WorkflowRole).where(WorkflowRole.workflow_id == wf.id))
    db.flush()
    for uid, role in sorted(pairs):
        db.add(WorkflowRole(workflow_id=wf.id, user_id=uid, role=role))
    db.flush()
    new_pairs = sorted(pairs)
    return {"old": old_pairs, "new": new_pairs} if old_pairs != new_pairs else {}


def _create_reasons(db, wf: Workflow, reasons_in: List[ReasonIn]) -> None:
    for idx, r in enumerate(reasons_in, start=1):
        db.add(WorkflowFailureReason(
            workflow_id=wf.id, label=clean_line(r.label, "Lý do thất bại", max_len=200), order=idx,
        ))
    db.flush()


@router.get("/templates")
def list_templates(ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    rows = list(db.scalars(select(WorkflowTemplate).order_by(WorkflowTemplate.category, WorkflowTemplate.name, WorkflowTemplate.id)))
    return {"items": [_template_out(t) for t in rows]}


@router.get("/templates/{template_id}")
def get_template(template_id: int, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    tpl = db.get(WorkflowTemplate, template_id)
    if tpl is None:
        raise HTTPException(404, "Không tìm thấy mẫu quy trình")
    return _template_out(tpl)


@router.post("/workflows/from-template", status_code=201)
def post_workflow_from_template(
    body: WorkflowFromTemplateIn, request: Request, ctx: Ctx = Depends(require_admin), db=Depends(get_db),
):
    tpl = db.get(WorkflowTemplate, body.template_id)
    if tpl is None:
        raise HTTPException(404, "Không tìm thấy mẫu quy trình")
    wf = create_workflow_from_template(
        db, ctx.user, ctx.ip, tpl,
        name=body.name, slug=body.slug, category=body.category, description=body.description,
        visibility=body.visibility, status=body.status,
        admin_ids=body.admin_ids, stage_members=body.stage_members, default_member_ids=body.member_ids,
        slugify_fn=_slugify,
    )
    db.commit()
    return _workflow_detail(db, wf, ctx.user)


@router.post("/workflows", status_code=201)
def create_workflow(body: WorkflowCreateIn, request: Request, ctx: Ctx = Depends(require_admin), db=Depends(get_db)):
    """Tạo quy trình (chỉ quản trị hệ thống). Người tạo tự động là admin của quy trình."""
    slug = (body.slug or "").strip().lower() or _slugify(body.name)
    if not SLUG_RE.match(slug):
        raise HTTPException(400, "slug chỉ gồm a-z 0-9 và dấu gạch ngang (tối đa 100 ký tự)")
    if db.scalar(select(Workflow.id).where(Workflow.slug == slug)) is not None:
        raise HTTPException(409, "slug đã tồn tại")
    wf = Workflow(
        name=clean_line(body.name, "Tên quy trình", max_len=200), slug=slug,
        category=clean_line(body.category, "Phân loại", max_len=100, required=False),
        description=body.description.strip(), guide_html=sanitize_html(body.guide_html),
        visibility=body.visibility, status=body.status, creator_id=ctx.user.id, created_at=utcnow(),
    )
    db.add(wf)
    db.flush()
    _create_stages(db, wf, body.stages, allow_empty_members=(body.status == "closed"))
    _create_fields(db, wf, body.form_fields)
    _set_roles(db, wf, body.roles, ensure_admin_id=ctx.user.id)
    _create_reasons(db, wf, body.failure_reasons)
    audit(db, ctx.ip, "workflow_created", user=ctx.user, workflow_id=wf.id, detail=wf.name, commit=False,
          metadata={"slug": wf.slug, "stages": len(body.stages), "visibility": wf.visibility, "status": wf.status})
    db.commit()
    return _workflow_detail(db, wf, ctx.user)


@router.get("/workflows")
def list_workflows(status: Optional[str] = None, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    """Danh sách quy trình mà người dùng có quyền xem."""
    stmt = select(Workflow)
    if status in ("active", "closed"):
        stmt = stmt.where(Workflow.status == status)
    rows = list(db.scalars(stmt.order_by(Workflow.created_at.desc(), Workflow.id.desc())))
    visible = [wf for wf in rows if can_see_workflow(db, ctx.user, wf.id)]
    return {"items": [_workflow_summary(db, wf, ctx.user) for wf in visible]}


@router.get("/workflows/{workflow_id}")
def get_workflow(workflow_id: int, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    """Chi tiết quy trình (lọc vai trò theo quyền ở server)."""
    wf = _load_workflow(db, ctx.user, workflow_id)
    return _workflow_detail(db, wf, ctx.user)


def _require_wf_admin(db, user: User, workflow_id: int) -> Workflow:
    wf = _load_workflow(db, user, workflow_id)
    if not can_manage_workflow(db, user, wf.id):
        raise HTTPException(403, "Chỉ admin của quy trình mới được sửa cấu trúc")
    return wf


def _patch_reasons(db, wf: Workflow, reasons_in: List[ReasonIn]) -> dict:
    existing = {r.id: r for r in db.scalars(select(WorkflowFailureReason).where(WorkflowFailureReason.workflow_id == wf.id))}
    keep: set[int] = set()
    diff: dict = {"added": [], "updated": [], "deleted": []}
    for idx, r in enumerate(reasons_in, start=1):
        lbl = clean_line(r.label, "Lý do thất bại", max_len=200)
        if r.id is None:
            row = WorkflowFailureReason(workflow_id=wf.id, label=lbl, order=idx)
            db.add(row)
            db.flush()
            keep.add(row.id)
            diff["added"].append({"id": row.id, "label": lbl})
            continue
        row = existing.get(r.id)
        if row is None:
            raise HTTPException(400, f"Lý do id={r.id} không thuộc quy trình này")
        keep.add(r.id)
        if lbl != row.label or row.order != idx:
            diff["updated"].append({"id": row.id, "label": [row.label, lbl], "order": [row.order, idx]})
        row.label = lbl
        row.order = idx
    for rid in set(existing) - keep:
        if db.scalar(select(Task.id).where(Task.failure_reason_id == rid).limit(1)) is not None:
            raise HTTPException(409, f"Lý do '{existing[rid].label}' đã được dùng bởi công việc thất bại, không thể xóa")
        diff["deleted"].append({"id": rid, "label": existing[rid].label})
        db.delete(existing[rid])
    db.flush()
    return diff


@router.patch("/workflows/{workflow_id}")
def patch_workflow(workflow_id: int, body: WorkflowPatchIn, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    """Sửa quy trình và cấu trúc (enforce ràng buộc W8 khi có task). Chỉ admin; supervisor chỉ đọc."""
    wf = _require_wf_admin(db, ctx.user, workflow_id)
    changed: List[str] = []
    diff: Dict[str, Any] = {}
    if body.name is not None:
        new_name = clean_line(body.name, "Tên quy trình", max_len=200)
        if new_name != wf.name:
            diff["name"] = [wf.name, new_name]
        wf.name = new_name
        changed.append("name")
    if body.category is not None:
        new_cat = clean_line(body.category, "Phân loại", max_len=100, required=False)
        if new_cat != wf.category:
            diff["category"] = [wf.category, new_cat]
        wf.category = new_cat
        changed.append("category")
    if body.description is not None:
        new_desc = body.description.strip()
        if new_desc != wf.description:
            diff["description"] = True
        wf.description = new_desc
        changed.append("description")
    if body.guide_html is not None:
        new_guide = sanitize_html(body.guide_html)
        if new_guide != wf.guide_html:
            diff["guide_html"] = True
        wf.guide_html = new_guide
        changed.append("guide_html")
    if body.visibility is not None and body.visibility != wf.visibility:
        diff["visibility"] = [wf.visibility, body.visibility]
        wf.visibility = body.visibility
        changed.append("visibility")
    if body.status is not None and body.status != wf.status:
        diff["status"] = [wf.status, body.status]
        wf.status = body.status
        changed.append("status")
    if body.stages is not None:
        diff["stages"] = patch_stages_with_rules(db, wf, body.stages)
        changed.append("stages")
    if body.form_fields is not None:
        diff["form_fields"] = patch_fields_with_rules(db, wf, body.form_fields)
        changed.append("form_fields")
    if body.failure_reasons is not None:
        diff["failure_reasons"] = _patch_reasons(db, wf, body.failure_reasons)
        changed.append("failure_reasons")
    if body.roles is not None:
        rd = _set_roles(db, wf, body.roles)
        if rd:
            diff["roles"] = rd
        changed.append("roles")
    if not changed:
        raise HTTPException(422, "Không có thay đổi nào được gửi lên")
    audit(db, ctx.ip, "workflow_updated", user=ctx.user, workflow_id=wf.id, detail=wf.name, commit=False,
          metadata={"changed": changed, "diff": diff})
    db.commit()
    return _workflow_detail(db, wf, ctx.user)


@router.post("/workflows/{workflow_id}/close")
def close_workflow(workflow_id: int, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    wf = _require_wf_admin(db, ctx.user, workflow_id)
    if wf.status == "closed":
        raise HTTPException(409, "Quy trình đã ở trạng thái đóng")
    wf.status = "closed"
    audit(db, ctx.ip, "workflow_closed", user=ctx.user, workflow_id=wf.id, detail=wf.name, commit=False,
          metadata={"diff": {"status": ["active", "closed"]}})
    db.commit()
    return _workflow_detail(db, wf, ctx.user)


@router.post("/workflows/{workflow_id}/reopen")
def reopen_workflow(workflow_id: int, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    wf = _require_wf_admin(db, ctx.user, workflow_id)
    if wf.status == "active":
        raise HTTPException(409, "Quy trình đang ở trạng thái hoạt động")
    wf.status = "active"
    audit(db, ctx.ip, "workflow_reopened", user=ctx.user, workflow_id=wf.id, detail=wf.name, commit=False,
          metadata={"diff": {"status": ["closed", "active"]}})
    db.commit()
    return _workflow_detail(db, wf, ctx.user)


@router.post("/workflows/{workflow_id}/clone", status_code=201)
def post_clone_workflow(
    workflow_id: int, body: Optional[CloneWorkflowIn] = None, ctx: Ctx = Depends(require_mfa), db=Depends(get_db),
):
    src = _require_wf_admin(db, ctx.user, workflow_id)
    dst = clone_workflow(
        db, src, ctx.user, ctx.ip,
        name=body.name if body else None, slug=body.slug if body else None, slugify_fn=_slugify,
    )
    db.commit()
    return _workflow_detail(db, dst, ctx.user)


@router.post("/workflows/{workflow_id}/save-as-template", status_code=201)
def post_save_as_template(
    workflow_id: int, body: Optional[SaveAsTemplateIn] = None, ctx: Ctx = Depends(require_mfa), db=Depends(get_db),
):
    wf = _require_wf_admin(db, ctx.user, workflow_id)
    tpl = save_workflow_as_template(
        db, wf, ctx.user, ctx.ip,
        name=body.name if body else None,
        category=body.category if body else None,
        description=body.description if body else None,
        slugify_fn=_slugify,
    )
    db.commit()
    return _template_out(tpl)


@router.post("/workflows/{workflow_id}/stages", status_code=201)
def post_add_stage(workflow_id: int, body: StageIn, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    wf = _require_wf_admin(db, ctx.user, workflow_id)
    _row, diff = add_single_stage(db, wf, body, insert_after_order=body.insert_after_order)
    audit(db, ctx.ip, "workflow_updated", user=ctx.user, workflow_id=wf.id, detail=wf.name, commit=False,
          metadata={"changed": ["stages"], "diff": diff})
    db.commit()
    return _workflow_detail(db, wf, ctx.user)


@router.put("/workflows/{workflow_id}/stages/reorder")
def put_reorder_stages(workflow_id: int, body: ReorderIn, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    wf = _require_wf_admin(db, ctx.user, workflow_id)
    diff = reorder_active_stages(db, wf, body.stage_ids)
    audit(db, ctx.ip, "workflow_updated", user=ctx.user, workflow_id=wf.id, detail=wf.name, commit=False,
          metadata={"changed": ["stages"], "diff": diff})
    db.commit()
    return _workflow_detail(db, wf, ctx.user)


@router.patch("/workflows/{workflow_id}/stages/{stage_id}")
def patch_single_stage(
    workflow_id: int, stage_id: int, body: StageSinglePatchIn, ctx: Ctx = Depends(require_mfa), db=Depends(get_db),
):
    wf = _require_wf_admin(db, ctx.user, workflow_id)
    _row, diff = update_single_stage(db, wf, stage_id, body)
    audit(db, ctx.ip, "workflow_updated", user=ctx.user, workflow_id=wf.id, detail=wf.name, commit=False,
          metadata={"changed": ["stages"], "diff": diff})
    db.commit()
    return _workflow_detail(db, wf, ctx.user)


@router.delete("/workflows/{workflow_id}/stages/{stage_id}")
def delete_single_stage(workflow_id: int, stage_id: int, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    wf = _require_wf_admin(db, ctx.user, workflow_id)
    res = delete_or_deactivate_stage(db, wf, stage_id)
    audit(db, ctx.ip, "workflow_updated", user=ctx.user, workflow_id=wf.id, detail=wf.name, commit=False,
          metadata={"changed": ["stages"], "diff": res})
    db.commit()
    return _workflow_detail(db, wf, ctx.user)


@router.post("/workflows/{workflow_id}/stages/{stage_id}/members")
def post_stage_members(
    workflow_id: int, stage_id: int, body: StageMembersIn, ctx: Ctx = Depends(require_mfa), db=Depends(get_db),
):
    wf = _require_wf_admin(db, ctx.user, workflow_id)
    st = db.get(Stage, stage_id)
    if st is None or st.workflow_id != wf.id:
        raise HTTPException(404, "Không tìm thấy giai đoạn")
    _require_active_users(db, body.user_ids, "Thành viên giai đoạn")
    existing = set(db.scalars(select(StageMember.user_id).where(StageMember.stage_id == st.id)))
    added = []
    for uid in sorted(set(body.user_ids)):
        if uid not in existing:
            db.add(StageMember(stage_id=st.id, user_id=uid))
            added.append(uid)
    db.flush()
    audit(db, ctx.ip, "workflow_updated", user=ctx.user, workflow_id=wf.id, detail=wf.name, commit=False,
          metadata={"changed": ["stage_members"], "diff": {"stage_id": st.id, "added": added}})
    db.commit()
    return _workflow_detail(db, wf, ctx.user)


@router.delete("/workflows/{workflow_id}/stages/{stage_id}/members/{user_id}")
def delete_stage_member(
    workflow_id: int, stage_id: int, user_id: int, ctx: Ctx = Depends(require_mfa), db=Depends(get_db),
):
    wf = _require_wf_admin(db, ctx.user, workflow_id)
    st = db.get(Stage, stage_id)
    if st is None or st.workflow_id != wf.id:
        raise HTTPException(404, "Không tìm thấy giai đoạn")
    member_row = db.get(StageMember, (st.id, user_id))
    if member_row is None:
        raise HTTPException(404, "Người dùng không thuộc giai đoạn này")
    has_doing = db.scalar(
        select(Todo.id).where(Todo.stage_id == st.id, Todo.assignee_id == user_id, Todo.status == "doing").limit(1)
    ) is not None
    if has_doing:
        raise HTTPException(409, f"Thành viên đang có việc cần làm chưa xử lý ở bước '{st.name}', hãy xử lý hoặc chuyển trước")
    all_members = list(db.scalars(select(StageMember.user_id).where(StageMember.stage_id == st.id)))
    if (wf.status == "active" or st.type == "sign") and len(all_members) <= 1:
        raise HTTPException(400, f"Bước '{st.name}' cần giữ lại ít nhất 1 thành viên")
    db.delete(member_row)
    db.flush()
    audit(db, ctx.ip, "workflow_updated", user=ctx.user, workflow_id=wf.id, detail=wf.name, commit=False,
          metadata={"changed": ["stage_members"], "diff": {"stage_id": st.id, "removed": user_id}})
    db.commit()
    return _workflow_detail(db, wf, ctx.user)


@router.post("/workflows/{workflow_id}/form-fields", status_code=201)
def post_add_form_field(workflow_id: int, body: FieldIn, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    wf = _require_wf_admin(db, ctx.user, workflow_id)
    if body.type == "select":
        opts = [clean_line(o, "Lựa chọn", max_len=200) for o in (body.options or [])]
        if not opts:
            raise HTTPException(400, f"Trường '{body.label}' (select) cần ít nhất 1 lựa chọn")
    else:
        opts = None
    max_ord = db.scalar(select(func.max(FormField.order)).where(FormField.workflow_id == wf.id)) or 0
    row = FormField(
        workflow_id=wf.id,
        label=clean_line(body.label, "Nhãn trường", max_len=200),
        type=body.type,
        required=body.required,
        placeholder=clean_line(body.placeholder, "Placeholder", max_len=200, required=False),
        options=opts,
        order=int(max_ord) + 1,
        is_active=True,
    )
    db.add(row)
    db.flush()
    audit(db, ctx.ip, "workflow_updated", user=ctx.user, workflow_id=wf.id, detail=wf.name, commit=False,
          metadata={"changed": ["form_fields"], "diff": {"added": {"id": row.id, "label": row.label, "type": row.type}}})
    db.commit()
    return _workflow_detail(db, wf, ctx.user)


@router.patch("/workflows/{workflow_id}/form-fields/{field_id}")
def patch_single_form_field(
    workflow_id: int, field_id: int, body: FieldSinglePatchIn, ctx: Ctx = Depends(require_mfa), db=Depends(get_db),
):
    wf = _require_wf_admin(db, ctx.user, workflow_id)
    row = db.get(FormField, field_id)
    if row is None or row.workflow_id != wf.id:
        raise HTTPException(404, "Không tìm thấy trường dữ liệu")
    used = field_ids_with_data(db, wf.id)
    if body.type is not None and body.type != row.type:
        if row.id in used:
            raise HTTPException(
                409,
                f"Trường '{row.label}' đã có dữ liệu trên công việc cũ: không thể đổi kiểu; hãy vô hiệu hóa (is_active=false)",
            )
        row.type = body.type
    if body.label is not None:
        row.label = clean_line(body.label, "Nhãn trường", max_len=200)
    if body.required is not None:
        row.required = body.required
    if body.placeholder is not None:
        row.placeholder = clean_line(body.placeholder, "Placeholder", max_len=200, required=False)
    if body.options is not None or row.type == "select":
        if row.type == "select":
            raw_opts = body.options if body.options is not None else (row.options or [])
            opts = [clean_line(o, "Lựa chọn", max_len=200) for o in raw_opts]
            if not opts:
                raise HTTPException(400, f"Trường '{row.label}' (select) cần ít nhất 1 lựa chọn")
            row.options = opts
        elif body.options is not None:
            row.options = None
    if body.order is not None:
        row.order = body.order
    if body.is_active is not None:
        row.is_active = body.is_active
    db.flush()
    audit(db, ctx.ip, "workflow_updated", user=ctx.user, workflow_id=wf.id, detail=wf.name, commit=False,
          metadata={"changed": ["form_fields"], "diff": {"updated": row.id}})
    db.commit()
    return _workflow_detail(db, wf, ctx.user)


@router.delete("/workflows/{workflow_id}/form-fields/{field_id}")
def delete_single_form_field(workflow_id: int, field_id: int, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    wf = _require_wf_admin(db, ctx.user, workflow_id)
    row = db.get(FormField, field_id)
    if row is None or row.workflow_id != wf.id:
        raise HTTPException(404, "Không tìm thấy trường dữ liệu")
    used = field_ids_with_data(db, wf.id)
    if row.id in used:
        raise HTTPException(
            409,
            f"Trường '{row.label}' đã có dữ liệu trên công việc cũ nên không thể xóa; hãy chuyển sang vô hiệu hóa (is_active=false)",
        )
    lbl = row.label
    db.delete(row)
    db.flush()
    audit(db, ctx.ip, "workflow_updated", user=ctx.user, workflow_id=wf.id, detail=wf.name, commit=False,
          metadata={"changed": ["form_fields"], "diff": {"deleted": field_id, "label": lbl}})
    db.commit()
    return _workflow_detail(db, wf, ctx.user)


@router.put("/workflows/{workflow_id}/roles")
def put_workflow_roles(workflow_id: int, body: RolesBulkIn, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    wf = _require_wf_admin(db, ctx.user, workflow_id)
    rd = _set_roles(db, wf, body.roles)
    audit(db, ctx.ip, "workflow_updated", user=ctx.user, workflow_id=wf.id, detail=wf.name, commit=False,
          metadata={"changed": ["roles"], "diff": rd})
    db.commit()
    return _workflow_detail(db, wf, ctx.user)


@router.post("/workflows/{workflow_id}/roles", status_code=201)
def post_workflow_role(workflow_id: int, body: RoleIn, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    wf = _require_wf_admin(db, ctx.user, workflow_id)
    _require_active_users(db, [body.user_id], "Người dùng")
    if db.get(WorkflowRole, (wf.id, body.user_id, body.role)) is None:
        db.add(WorkflowRole(workflow_id=wf.id, user_id=body.user_id, role=body.role))
        db.flush()
    audit(db, ctx.ip, "workflow_updated", user=ctx.user, workflow_id=wf.id, detail=wf.name, commit=False,
          metadata={"changed": ["roles"], "diff": {"added": {"user_id": body.user_id, "role": body.role}}})
    db.commit()
    return _workflow_detail(db, wf, ctx.user)


@router.delete("/workflows/{workflow_id}/roles/{user_id}/{role}")
def delete_workflow_role(workflow_id: int, user_id: int, role: str, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    wf = _require_wf_admin(db, ctx.user, workflow_id)
    row = db.get(WorkflowRole, (wf.id, user_id, role))
    if row is None:
        raise HTTPException(404, "Không tìm thấy vai trò này")
    if role == "admin":
        admin_cnt = db.scalar(
            select(func.count()).select_from(WorkflowRole).where(
                WorkflowRole.workflow_id == wf.id, WorkflowRole.role == "admin"
            )
        ) or 0
        if admin_cnt <= 1:
            raise HTTPException(400, "Quy trình phải còn ít nhất 1 admin")
    db.delete(row)
    db.flush()
    audit(db, ctx.ip, "workflow_updated", user=ctx.user, workflow_id=wf.id, detail=wf.name, commit=False,
          metadata={"changed": ["roles"], "diff": {"removed": {"user_id": user_id, "role": role}}})
    db.commit()
    return _workflow_detail(db, wf, ctx.user)


@router.post("/workflows/{workflow_id}/failure-reasons", status_code=201)
def post_failure_reason(workflow_id: int, body: ReasonCreateIn, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    wf = _require_wf_admin(db, ctx.user, workflow_id)
    max_ord = db.scalar(select(func.max(WorkflowFailureReason.order)).where(WorkflowFailureReason.workflow_id == wf.id)) or 0
    row = WorkflowFailureReason(
        workflow_id=wf.id, label=clean_line(body.label, "Lý do thất bại", max_len=200), order=int(max_ord) + 1,
    )
    db.add(row)
    db.flush()
    audit(db, ctx.ip, "workflow_updated", user=ctx.user, workflow_id=wf.id, detail=wf.name, commit=False,
          metadata={"changed": ["failure_reasons"], "diff": {"added": {"id": row.id, "label": row.label}}})
    db.commit()
    return _workflow_detail(db, wf, ctx.user)


@router.patch("/workflows/{workflow_id}/failure-reasons/{reason_id}")
def patch_failure_reason(
    workflow_id: int, reason_id: int, body: ReasonPatchIn, ctx: Ctx = Depends(require_mfa), db=Depends(get_db),
):
    wf = _require_wf_admin(db, ctx.user, workflow_id)
    row = db.get(WorkflowFailureReason, reason_id)
    if row is None or row.workflow_id != wf.id:
        raise HTTPException(404, "Không tìm thấy lý do thất bại")
    if body.label is not None:
        row.label = clean_line(body.label, "Lý do thất bại", max_len=200)
    if body.order is not None:
        row.order = body.order
    db.flush()
    audit(db, ctx.ip, "workflow_updated", user=ctx.user, workflow_id=wf.id, detail=wf.name, commit=False,
          metadata={"changed": ["failure_reasons"], "diff": {"updated": row.id}})
    db.commit()
    return _workflow_detail(db, wf, ctx.user)


@router.delete("/workflows/{workflow_id}/failure-reasons/{reason_id}")
def delete_failure_reason(workflow_id: int, reason_id: int, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    wf = _require_wf_admin(db, ctx.user, workflow_id)
    row = db.get(WorkflowFailureReason, reason_id)
    if row is None or row.workflow_id != wf.id:
        raise HTTPException(404, "Không tìm thấy lý do thất bại")
    if db.scalar(select(Task.id).where(Task.failure_reason_id == reason_id).limit(1)) is not None:
        raise HTTPException(409, f"Lý do '{row.label}' đã được dùng bởi công việc thất bại, không thể xóa")
    lbl = row.label
    db.delete(row)
    db.flush()
    audit(db, ctx.ip, "workflow_updated", user=ctx.user, workflow_id=wf.id, detail=wf.name, commit=False,
          metadata={"changed": ["failure_reasons"], "diff": {"deleted": reason_id, "label": lbl}})
    db.commit()
    return _workflow_detail(db, wf, ctx.user)


# ======================================================================================
# Tasks
# ======================================================================================
@router.post("/workflows/{workflow_id}/tasks", status_code=201)
def post_task(workflow_id: int, body: TaskCreateIn, request: Request, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    st = request.app.state.settings
    wf = _load_workflow(db, ctx.user, workflow_id)
    if not (workflow_roles_of(db, ctx.user.id, wf.id) & {"admin", "creator"}):
        raise HTTPException(403, "Bạn không có quyền tạo công việc trong quy trình này")
    title = clean_line(body.title, "Tiêu đề", max_len=255)
    labels = clean_labels(body.labels)
    followers = sorted(set(body.follower_ids))
    _require_active_users(db, followers, "Người theo dõi")
    fields = list(db.scalars(select(FormField).where(FormField.workflow_id == wf.id, FormField.is_active.is_(True))))
    values = validate_custom_values(db, fields, body.custom_values, allowed_file_owner_ids={ctx.user.id})
    task, jobs = create_task(
        db, st, wf, ctx.user, title=title, labels=labels, description_html=sanitize_html(body.description_html),
        custom_values=values, follower_ids=followers, now=_now(request), ip=ctx.ip,
        assignee_ids=body.assignee_ids,
    )
    db.commit()
    dispatch_emails(st, jobs)
    return _task_detail(db, task, ctx.user, _now(request))


@router.get("/tasks/{task_id}")
def get_task(task_id: int, request: Request, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    return _task_detail(db, get_visible_task(db, ctx.user, task_id), ctx.user, _now(request))


@router.get("/tasks/{task_id}/timeline")
def task_timeline(task_id: int, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    """Lịch sử hành trình của task — cùng định dạng sự kiện với timeline tài liệu để tab Lịch sử dùng chung."""
    task = get_visible_task(db, ctx.user, task_id)
    cond = AuditLog.task_id == task.id
    if task.document_id:
        cond = or_(cond, AuditLog.document_id == task.document_id)
    rows = db.scalars(select(AuditLog).where(cond).order_by(AuditLog.id).limit(1000))
    return {
        "task_id": task.id, "document_id": task.document_id, "title": task.title, "status": task.status,
        "created_at": _iso(task.created_at), "completed_at": _iso(task.completed_at),
        "events": [audit_event_out(a) for a in rows],
    }


def _require_task_privilege(db, user: User, task: Task) -> None:
    if not (task.creator_id == user.id or "admin" in workflow_roles_of(db, user.id, task.workflow_id)):
        raise HTTPException(403, "Chỉ người tạo hoặc admin của quy trình mới thực hiện được thao tác này")


@router.patch("/tasks/{task_id}")
def patch_task(task_id: int, body: TaskPatchIn, request: Request, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    st = request.app.state.settings
    task = get_visible_task(db, ctx.user, task_id)
    _require_task_privilege(db, ctx.user, task)
    if task.archived_at is not None:
        raise HTTPException(409, "Công việc đã được lưu trữ")
    changed: List[str] = []
    if body.title is not None:
        task.title = clean_line(body.title, "Tiêu đề", max_len=255)
        changed.append("title")
    if body.labels is not None:
        task.labels = clean_labels(body.labels)
        changed.append("labels")
    if body.description_html is not None:
        task.description_html = sanitize_html(body.description_html)
        changed.append("description")
    if body.custom_values is not None:
        all_fields = list(db.scalars(select(FormField).where(FormField.workflow_id == task.workflow_id)))
        existing_keys = set((task.custom_values or {}).keys())
        fields = [f for f in all_fields if f.is_active or str(f.id) in existing_keys]
        task.custom_values = validate_custom_values(
            db, fields, body.custom_values, allowed_file_owner_ids={ctx.user.id, task.creator_id})
        changed.append("custom_values")
    if not changed:
        raise HTTPException(422, "Không có thay đổi nào được gửi lên")
    now = _now(request)
    task.updated_at = now
    audit(db, ctx.ip, "task_edited", user=ctx.user, task_id=task.id, workflow_id=task.workflow_id,
          document_id=task.document_id, commit=False, metadata={"changed": changed})
    jobs = edit_task_notify(db, st, task, ctx.user, changed)
    db.commit()
    dispatch_emails(st, jobs)
    return _task_detail(db, task, ctx.user, now)


@router.post("/tasks/{task_id}/followers", status_code=201)
def post_follower(task_id: int, body: FollowerIn, request: Request, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    st = request.app.state.settings
    task = get_visible_task(db, ctx.user, task_id)
    _require_task_privilege(db, ctx.user, task)
    if task.archived_at is not None:
        raise HTTPException(409, "Công việc đã được lưu trữ")
    target = db.get(User, body.user_id)
    if target is None or not target.is_active:
        raise HTTPException(400, "Người dùng không tồn tại hoặc đã bị vô hiệu hóa")
    if db.get(TaskFollower, (task.id, target.id)) is not None:
        raise HTTPException(409, "Người dùng đã theo dõi công việc này")
    jobs = add_follower(db, st, task, ctx.user, target, ctx.ip)
    db.commit()
    dispatch_emails(st, jobs)
    return _task_detail(db, task, ctx.user, _now(request))


@router.delete("/tasks/{task_id}/followers/{user_id}")
def delete_follower(task_id: int, user_id: int, request: Request, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    task = get_visible_task(db, ctx.user, task_id)
    _require_task_privilege(db, ctx.user, task)
    row = db.get(TaskFollower, (task.id, user_id))
    if row is None:
        raise HTTPException(404, "Người dùng không phải người theo dõi")
    db.delete(row)
    audit(db, ctx.ip, "follower_removed", user=ctx.user, task_id=task.id, workflow_id=task.workflow_id,
          document_id=task.document_id, commit=False, metadata={"follower_id": user_id})
    db.commit()
    # Người bị gỡ có thể không còn thấy task nữa → trả về trạng thái từ góc nhìn người thao tác
    return _task_detail(db, task, ctx.user, _now(request))


def _set_archive(db, request: Request, ctx: Ctx, task_id: int, archive: bool) -> dict:
    now = _now(request)
    with task_lock(task_id):
        task = get_visible_task(db, ctx.user, task_id)
        _require_task_privilege(db, ctx.user, task)
        if archive:
            if task.status == "processing":
                raise HTTPException(409, "Chỉ lưu trữ được công việc đã hoàn thành hoặc thất bại")
            if task.archived_at is not None:
                raise HTTPException(409, "Công việc đã được lưu trữ")
            task.archived_at = now
            cancel_open_sign_steps_for_task(db, task, ctx.user, now, ctx.ip, reason="Công việc được lưu trữ")
        else:
            if task.archived_at is None:
                raise HTTPException(409, "Công việc chưa được lưu trữ")
            task.archived_at = None
        task.updated_at = now
        audit(db, ctx.ip, "task_archived" if archive else "task_restored", user=ctx.user, task_id=task.id,
              workflow_id=task.workflow_id, document_id=task.document_id, commit=False)
        db.commit()
    return _task_detail(db, task, ctx.user, now)


@router.post("/tasks/{task_id}/archive")
def archive_task(task_id: int, request: Request, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    return _set_archive(db, request, ctx, task_id, True)


@router.post("/tasks/{task_id}/restore")
def restore_task(task_id: int, request: Request, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    return _set_archive(db, request, ctx, task_id, False)


def _attach_task_document_sync(
    db, request: Request, ctx: Ctx, task_id: int, *,
    upload_file: Optional[UploadFile] = None,
    file_id: Optional[str] = None,
    document_id: Optional[str] = None,
    title_raw: Optional[str] = None,
) -> dict:
    st = request.app.state.settings
    now = _now(request)
    with task_lock(task_id):
        task = get_visible_task(db, ctx.user, task_id)
        roles = workflow_roles_of(db, ctx.user.id, task.workflow_id)
        is_cur_assignee = (
            task.current_stage_id is not None
            and db.scalar(
                select(Todo.id).where(
                    Todo.task_id == task.id,
                    Todo.stage_id == task.current_stage_id,
                    Todo.assignee_id == ctx.user.id,
                    Todo.status == "doing",
                ).limit(1)
            ) is not None
        )
        if not (task.creator_id == ctx.user.id or "admin" in roles or is_cur_assignee):
            raise HTTPException(403, "Chỉ người tạo, admin quy trình hoặc người xử lý bước hiện tại mới được gắn tài liệu")
        if task.archived_at is not None:
            raise HTTPException(409, "Công việc đã được lưu trữ")
        if task.status != "processing":
            raise HTTPException(409, "Công việc không còn ở trạng thái đang xử lý")
        if has_sign_stage_started(db, task):
            raise HTTPException(409, "Không thể đổi tài liệu sau khi bước ký đã bắt đầu")

        sources = int(upload_file is not None) + int(bool(file_id)) + int(bool(document_id))
        if sources == 0:
            raise HTTPException(422, "Cần gửi file upload, file_id hoặc document_id")
        if sources > 1:
            raise HTTPException(422, "Chỉ chọn một nguồn tài liệu: file, file_id hoặc document_id")

        custom_title = clean_line(title_raw, "Tiêu đề tài liệu", max_len=255, required=False) if title_raw else ""
        created_file: Optional[StoredFile] = None
        source_kind = "upload"

        try:
            if upload_file is not None:
                f, _info, _oh, _ext = ingest_uploaded_file(
                    db, request, ctx, upload_file,
                    is_workflow=True, commit=False, task_id=task.id, workflow_id=task.workflow_id,
                )
                created_file = f
                doc_title = custom_title or (f.original_name[:-4] if f.original_name.lower().endswith(".pdf") else f.original_name) or task.title
                doc = Document(
                    id=str(uuid.uuid4()), title=doc_title[:255], description="",
                    status="in_progress", creator_id=ctx.user.id,
                    original_file_id=f.id, current_file_id=f.id, current_step_order=0,
                    created_at=now, updated_at=now,
                )
                db.add(doc)
                db.flush()
                db.add(DocumentVersion(
                    document_id=doc.id, version_no=0, file_id=f.id,
                    step_id=None, created_by_id=ctx.user.id, note="Tài liệu gốc", created_at=now,
                ))
                audit(
                    db, ctx.ip, "doc_create", user=ctx.user, document_id=doc.id,
                    task_id=task.id, workflow_id=task.workflow_id,
                    file_id=f.id, file_sha256=f.sha256, detail=f"title={doc.title}",
                    metadata={"source": "workflow_task_upload"}, commit=False,
                )
            elif file_id:
                source_kind = "file_id"
                try:
                    fid = str(uuid.UUID(file_id.strip()))
                except ValueError:
                    raise HTTPException(404, "Không tìm thấy file") from None
                f = db.get(StoredFile, fid)
                if f is None or f.deleted_at is not None or f.kind not in ("upload", "signed"):
                    raise HTTPException(404, "Không tìm thấy file phù hợp")
                form_file_ids = set()
                if isinstance(task.custom_values, dict):
                    for fld in db.scalars(select(FormField).where(FormField.workflow_id == task.workflow_id, FormField.type == "file")):
                        val = task.custom_values.get(str(fld.id))
                        if isinstance(val, str):
                            form_file_ids.add(val)
                if f.owner_id != ctx.user.id and ctx.user.role != "admin" and f.id not in form_file_ids:
                    raise HTTPException(404, "Không tìm thấy file")
                if not f.is_workflow and f.expires_at <= now and f.id not in form_file_ids:
                    raise HTTPException(404, "Không tìm thấy file")
                used_doc = db.scalar(
                    select(Document.id).where(
                        or_(Document.original_file_id == f.id, Document.current_file_id == f.id)
                    ).limit(1)
                ) or db.scalar(select(DocumentVersion.id).where(DocumentVersion.file_id == f.id).limit(1))
                if used_doc is not None:
                    raise HTTPException(409, "File này đã thuộc một tài liệu khác")
                f.is_workflow = True
                doc_title = custom_title or (f.original_name[:-4] if f.original_name.lower().endswith(".pdf") else f.original_name) or task.title
                doc = Document(
                    id=str(uuid.uuid4()), title=doc_title[:255], description="",
                    status="in_progress", creator_id=ctx.user.id,
                    original_file_id=f.id, current_file_id=f.id, current_step_order=0,
                    created_at=now, updated_at=now,
                )
                db.add(doc)
                db.flush()
                db.add(DocumentVersion(
                    document_id=doc.id, version_no=0, file_id=f.id,
                    step_id=None, created_by_id=ctx.user.id, note="Tài liệu gốc", created_at=now,
                ))
                audit(
                    db, ctx.ip, "doc_create", user=ctx.user, document_id=doc.id,
                    task_id=task.id, workflow_id=task.workflow_id,
                    file_id=f.id, file_sha256=f.sha256, detail=f"title={doc.title}",
                    metadata={"source": "workflow_task_file_id"}, commit=False,
                )
            else:
                source_kind = "document_id"
                try:
                    did = str(uuid.UUID(document_id.strip()))
                except ValueError:
                    raise HTTPException(404, "Không tìm thấy tài liệu") from None
                doc = db.get(Document, did)
                if doc is None or (doc.creator_id != ctx.user.id and ctx.user.role != "admin"):
                    raise HTTPException(404, "Không tìm thấy tài liệu")
                other_task = db.scalar(select(Task.id).where(Task.document_id == doc.id, Task.id != task.id).limit(1))
                if other_task is not None:
                    raise HTTPException(409, "Tài liệu này đã gắn với công việc khác")
                if doc.status != "in_progress":
                    raise HTTPException(409, "Chỉ gắn được tài liệu đang ở trạng thái xử lý")
                has_steps = db.scalar(select(WorkflowStep.id).where(WorkflowStep.document_id == doc.id).limit(1))
                if has_steps is not None:
                    raise HTTPException(409, "Không thể gắn tài liệu đã phát sinh bước ký độc lập")
                ver_fids = set(db.scalars(select(DocumentVersion.file_id).where(DocumentVersion.document_id == doc.id)))
                ver_fids.update([doc.original_file_id, doc.current_file_id])
                for sf in db.scalars(select(StoredFile).where(StoredFile.id.in_(ver_fids))):
                    sf.is_workflow = True
                f = db.get(StoredFile, doc.current_file_id)

            task.document_id = doc.id
            task.updated_at = now
            audit(
                db, ctx.ip, "task_document_attached", user=ctx.user,
                task_id=task.id, workflow_id=task.workflow_id, document_id=doc.id,
                file_id=doc.current_file_id, file_sha256=f.sha256 if f else None,
                detail=doc.title, metadata={"source": source_kind}, commit=False,
            )
            db.commit()
        except Exception:
            db.rollback()
            if created_file is not None:
                remove_from_disk(st, created_file.id)
            raise
    return _task_detail(db, task, ctx.user, now)


@router.post("/tasks/{task_id}/document")
async def attach_task_document(task_id: int, request: Request, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    """Gắn tài liệu vào task: hỗ trợ multipart upload (PDF/Office) hoặc JSON {file_id | document_id}."""
    ct = (request.headers.get("content-type") or "").lower()
    if "multipart/form-data" in ct:
        form = await request.form()
        try:
            raw_up = form.get("file")
            up = raw_up if (raw_up is not None and hasattr(raw_up, "file") and hasattr(raw_up, "filename")) else None
            file_id = form.get("file_id") if isinstance(form.get("file_id"), str) else None
            doc_id = form.get("document_id") if isinstance(form.get("document_id"), str) else None
            title = form.get("title") if isinstance(form.get("title"), str) else None
            return await run_in_threadpool(
                _attach_task_document_sync, db, request, ctx, task_id,
                upload_file=up, file_id=file_id, document_id=doc_id, title_raw=title,
            )
        finally:
            await form.close()
    elif "application/json" in ct:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "JSON không hợp lệ") from None
        if not isinstance(body, dict):
            raise HTTPException(422, "Body phải là đối tượng JSON")
        return await run_in_threadpool(
            _attach_task_document_sync, db, request, ctx, task_id,
            file_id=body.get("file_id") if isinstance(body.get("file_id"), str) else None,
            document_id=body.get("document_id") if isinstance(body.get("document_id"), str) else None,
            title_raw=body.get("title") if isinstance(body.get("title"), str) else None,
        )
    raise HTTPException(415, "Cần gửi multipart/form-data hoặc application/json")


@router.get("/tasks/{task_id}/document/file")
def download_task_document_file(
    task_id: int, request: Request, version_no: Optional[int] = None,
    ctx: Ctx = Depends(require_mfa), db=Depends(get_db),
):
    task = get_visible_task(db, ctx.user, task_id)
    if not task.document_id:
        raise HTTPException(404, "Công việc chưa đính kèm tài liệu")
    doc = db.get(Document, task.document_id)
    if doc is None:
        raise HTTPException(404, "Không tìm thấy tài liệu")
    f = _resolve_doc_file(db, doc, version_no=version_no)
    data = read_bytes(request.app.state.settings, f)
    audit(
        db, ctx.ip, "download", user=ctx.user, document_id=doc.id,
        task_id=task.id, workflow_id=task.workflow_id,
        file_id=f.id, file_sha256=f.sha256,
        metadata={"version_no": version_no} if version_no is not None else None,
    )
    return _pdf_response(f, data)


@router.post("/tasks/{task_id}/document/verify")
def verify_task_document(
    task_id: int, request: Request, version_no: Optional[int] = None,
    ctx: Ctx = Depends(require_mfa), db=Depends(get_db),
):
    st = request.app.state.settings
    rate_limit(request, "verify", str(ctx.user.id), st.verify_rate)
    task = get_visible_task(db, ctx.user, task_id)
    if not task.document_id:
        raise HTTPException(404, "Công việc chưa đính kèm tài liệu")
    doc = db.get(Document, task.document_id)
    if doc is None:
        raise HTTPException(404, "Không tìm thấy tài liệu")
    f = _resolve_doc_file(db, doc, version_no=version_no)
    sigs = signer_verify(st, read_bytes(st, f))
    audit(
        db, ctx.ip, "verify", user=ctx.user, document_id=doc.id, file_id=f.id, file_sha256=f.sha256,
        task_id=task.id, workflow_id=task.workflow_id,
        metadata={"version_no": version_no} if version_no is not None else None,
        detail=f"signatures={len(sigs)} all_ok={bool(sigs) and all(s.get('ok') for s in sigs)}",
    )
    return {
        "task_id": task.id,
        "document_id": doc.id,
        "file_id": f.id,
        "signatures": sigs,
        "all_ok": bool(sigs) and all(s.get("ok") for s in sigs),
    }


@router.post("/tasks/{task_id}/sign")
def sign_task_document(
    task_id: int, body: DocumentSignIn, request: Request, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)
):
    task = get_visible_task(db, ctx.user, task_id)
    if not task.document_id:
        raise HTTPException(409, "Công việc chưa đính kèm tài liệu")
    return sign_document(task.document_id, body, request, ctx, db)


@router.post("/tasks/{task_id}/reject")
def reject_task_document(
    task_id: int, body: RejectIn, request: Request, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)
):
    task = get_visible_task(db, ctx.user, task_id)
    if not task.document_id:
        raise HTTPException(409, "Công việc chưa đính kèm tài liệu")
    return reject_document(task.document_id, body, request, ctx, db)


@router.post("/tasks/{task_id}/cancel")
def cancel_task_route(
    task_id: int, request: Request, body: Optional[TaskCancelIn] = None,
    ctx: Ctx = Depends(require_mfa), db=Depends(get_db),
):
    st = request.app.state.settings
    reason = clean_line(body.reason, "Lý do hủy", max_len=500, required=False) if (body and body.reason) else ""
    fr_id = body.failure_reason_id if body else None
    now = _now(request)
    with task_lock(task_id):
        task = get_visible_task(db, ctx.user, task_id)
        _require_task_privilege(db, ctx.user, task)
        jobs = cancel_task(db, st, task, ctx.user, reason, now, ctx.ip, failure_reason_id=fr_id)
        db.commit()
    dispatch_emails(st, jobs)
    return _task_detail(db, task, ctx.user, now)


# ======================================================================================
# Todos
# ======================================================================================
def _lock_todo_and_task(db, todo_id: int) -> tuple[Todo, Task]:
    """Đọc lại todo/task bên trong khóa (populate_existing + FOR UPDATE) để tránh đọc dữ liệu cũ."""
    todo = db.scalar(select(Todo).where(Todo.id == todo_id).with_for_update().execution_options(populate_existing=True))
    if todo is None:
        raise HTTPException(404, "Không tìm thấy việc cần làm")
    task = db.scalar(select(Task).where(Task.id == todo.task_id).with_for_update().execution_options(populate_existing=True))
    return todo, task


def _todo_task_id(db, todo_id: int) -> int:
    tid = db.scalar(select(Todo.task_id).where(Todo.id == todo_id))
    if tid is None:
        raise HTTPException(404, "Không tìm thấy việc cần làm")
    return tid


@router.post("/todos/{todo_id}/complete")
def post_todo_complete(todo_id: int, body: TodoCompleteIn, request: Request, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    st = request.app.state.settings
    result = clean_line(body.result, "Kết quả", max_len=4000, required=False) if body.result else None
    note = clean_line(body.note, "Ghi chú", max_len=2000, required=False) if body.note else None
    with task_lock(_todo_task_id(db, todo_id)):
        todo, task = _lock_todo_and_task(db, todo_id)
        now = _now(request)
        jobs = complete_todo(
            db, st, todo, task, ctx.user, result, now, ctx.ip,
            next_assignee_ids=body.next_assignee_ids,
            decision=body.decision,
            failure_reason_id=body.failure_reason_id,
            note=note,
        )
        db.commit()
    dispatch_emails(st, jobs)
    return _task_detail(db, task, ctx.user, now)


@router.post("/todos/{todo_id}/fail")
def post_todo_fail(todo_id: int, body: TodoFailIn, request: Request, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    st = request.app.state.settings
    note = clean_line(body.note, "Ghi chú", max_len=2000, required=False) if body.note else None
    pre = db.execute(
        select(Todo.task_id, Todo.assignee_id, Todo.status, Stage.type, Task.document_id, WorkflowStep.id)
        .join(Stage, Stage.id == Todo.stage_id)
        .join(Task, Task.id == Todo.task_id)
        .outerjoin(WorkflowStep, WorkflowStep.id == Todo.workflow_step_id)
        .where(Todo.id == todo_id)
    ).first()
    if pre is None:
        raise HTTPException(404, "Không tìm thấy việc cần làm")
    pre_task_id, pre_uid, pre_status, pre_stage_type, pre_doc_id, pre_step_id = pre
    if (
        pre_stage_type == "sign"
        and pre_doc_id
        and pre_step_id
        and pre_uid == ctx.user.id
        and pre_status == "doing"
    ):
        reject_document(
            pre_doc_id,
            RejectIn(reason=note or "Từ chối ký", pin=body.pin, failure_reason_id=body.failure_reason_id),
            request, ctx, db,
        )
        task = db.get(Task, pre_task_id)
        return _task_detail(db, task, ctx.user, _now(request))
    with task_lock(pre_task_id):
        todo, task = _lock_todo_and_task(db, todo_id)
        now = _now(request)
        jobs = fail_todo(db, st, todo, task, ctx.user, body.failure_reason_id, note, now, ctx.ip)
        db.commit()
    dispatch_emails(st, jobs)
    return _task_detail(db, task, ctx.user, now)


def _same_department_users(user: User):
    return select(User.id).where(User.department == user.department)


def _page_params(page: int, page_size: int) -> tuple[int, int]:
    return max(1, page), max(1, min(page_size, MAX_PAGE_SIZE))


@router.get("/todos")
def list_todos(
    request: Request,
    role: str = "assigned",
    status: str = "",
    page: int = 1,
    page_size: int = 20,
    ctx: Ctx = Depends(require_mfa),
    db=Depends(get_db),
):
    now = _now(request)
    uid = ctx.user.id
    page, page_size = _page_params(page, page_size)
    conds = [visible_task_clause(uid)]
    role_val = role or "assigned"
    if role_val == "assigned":
        conds.append(Todo.assignee_id == uid)
    elif role_val == "created":
        conds.append(Task.creator_id == uid)
    elif role_val == "following":
        conds.append(Task.id.in_(select(TaskFollower.task_id).where(TaskFollower.user_id == uid)))
    elif role_val == "team":
        conds.append(Todo.assignee_id.in_(_same_department_users(ctx.user)) if ctx.user.department else false())
    else:
        raise HTTPException(400, "role không hợp lệ (assigned|created|following|team)")
    if status == "overdue":
        conds += [Todo.status == "doing", Task.status == "processing", Todo.deadline.is_not(None), Todo.deadline < now]
    elif status in ("doing", "done", "failed", "cancelled"):
        conds.append(Todo.status == status)
    elif status:
        raise HTTPException(400, "status không hợp lệ (doing|done|failed|cancelled|overdue)")
    base = select(Todo).join(Task, Task.id == Todo.task_id).where(and_(*conds))
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = list(db.scalars(
        base.order_by(Todo.deadline.is_(None), Todo.deadline.asc(), Todo.id.desc())
        .limit(page_size).offset((page - 1) * page_size)
    ))
    tasks = {t.id: t for t in db.scalars(select(Task).where(Task.id.in_({r.task_id for r in rows})))} if rows else {}
    stages = {s.id: s for s in db.scalars(select(Stage).where(Stage.id.in_({r.stage_id for r in rows})))} if rows else {}
    users = _users_map(db, {r.assignee_id for r in rows})
    wfs = {w.id: w.name for w in db.scalars(select(Workflow).where(Workflow.id.in_({t.workflow_id for t in tasks.values()})))} if tasks else {}
    items = []
    for r in rows:
        t = tasks[r.task_id]
        item = _todo_out(r, t, stages.get(r.stage_id), users, now)
        item["task"] = {
            "id": t.id, "title": t.title, "status": t.status, "workflow_id": t.workflow_id,
            "workflow_name": wfs.get(t.workflow_id, ""), "is_overdue": task_is_overdue(t, now),
        }
        items.append(item)
    return {"items": items, "total": total, "page": page, "page_size": page_size,
            "pages": max(1, -(-total // page_size))}


# ======================================================================================
# Jobs (tìm kiếm/lọc) + export + board
# ======================================================================================
_SORTS = {
    "created_at": Task.created_at, "updated_at": Task.updated_at, "deadline": Task.deadline,
    "completed_at": Task.completed_at, "title": Task.title,
}


def _jobs_conditions(
    db, user: User, now: datetime, *, role: Optional[str], status: str, workflow_id: Optional[int],
    stage_id: Optional[int], q: str, created_from, created_to, deadline_from, deadline_to,
    completed_from, completed_to, archived: bool,
) -> list:
    uid = user.id
    conds = [visible_task_clause(uid)]  # visibility luôn áp dụng ở SQL, trước mọi bộ lọc khác
    conds.append(Task.archived_at.is_not(None) if archived else Task.archived_at.is_(None))
    if role == "assigned":
        doing_me = Task.id.in_(select(Todo.task_id).where(Todo.assignee_id == uid, Todo.status == "doing"))
        any_me = Task.id.in_(select(Todo.task_id).where(Todo.assignee_id == uid, Todo.status != "cancelled"))
        conds.append(or_(and_(Task.status == "processing", doing_me), and_(Task.status != "processing", any_me)))
    elif role == "created":
        conds.append(Task.creator_id == uid)
    elif role == "following":
        conds.append(Task.id.in_(select(TaskFollower.task_id).where(TaskFollower.user_id == uid)))
    elif role == "team":
        if user.department:
            dept = _same_department_users(user)
            conds.append(or_(Task.creator_id.in_(dept),
                             Task.id.in_(select(Todo.task_id).where(Todo.assignee_id.in_(dept)))))
        else:
            conds.append(false())
    elif role:
        raise HTTPException(400, "role không hợp lệ (assigned|created|following|team)")
    if status == "overdue":
        conds += [Task.status == "processing", Task.deadline.is_not(None), Task.deadline < now]
    elif status in ("processing", "completed", "failed"):
        conds.append(Task.status == status)
    elif status:
        raise HTTPException(400, "status không hợp lệ (processing|completed|failed|overdue)")
    if workflow_id is not None:
        conds.append(Task.workflow_id == workflow_id)
    if stage_id is not None:
        conds.append(Task.current_stage_id == stage_id)
    if q.strip():
        like = f"%{q.strip().lower()}%"
        conds.append(or_(
            func.lower(Task.title).like(like),
            func.lower(cast(Task.labels, Text)).like(like),
            func.lower(Task.description_html).like(like),
        ))
    conds += _range(Task.created_at, created_from, created_to, "created")
    conds += _range(Task.deadline, deadline_from, deadline_to, "deadline")
    conds += _range(Task.completed_at, completed_from, completed_to, "completed")
    return conds


def _order_by(sort: str):
    desc = sort.startswith("-")
    key = sort.lstrip("-") or "created_at"
    col = _SORTS.get(key)
    if col is None:
        raise HTTPException(400, "sort không hợp lệ (created_at|updated_at|deadline|completed_at|title, thêm '-' để giảm dần)")
    ordering = [col.desc() if desc else col.asc()]
    if key in ("deadline", "completed_at"):
        ordering.insert(0, col.is_(None))  # NULL xuống cuối
    return ordering + [Task.id.desc()]


@router.get("/jobs")
def list_jobs(
    request: Request,
    role: Optional[str] = None,
    status: str = "",
    workflow_id: Optional[int] = None,
    stage_id: Optional[int] = None,
    q: str = "",
    created_from: Optional[str] = None,
    created_to: Optional[str] = None,
    deadline_from: Optional[str] = None,
    deadline_to: Optional[str] = None,
    completed_from: Optional[str] = None,
    completed_to: Optional[str] = None,
    archived: bool = False,
    sort: str = "-created_at",
    page: int = 1,
    page_size: int = 20,
    ctx: Ctx = Depends(require_mfa),
    db=Depends(get_db),
):
    now = _now(request)
    page, page_size = _page_params(page, page_size)
    conds = _jobs_conditions(
        db, ctx.user, now, role=role, status=status, workflow_id=workflow_id, stage_id=stage_id, q=q,
        created_from=created_from, created_to=created_to, deadline_from=deadline_from, deadline_to=deadline_to,
        completed_from=completed_from, completed_to=completed_to, archived=archived,
    )
    total = db.scalar(select(func.count(Task.id)).where(and_(*conds))) or 0
    tasks = list(db.scalars(
        select(Task).where(and_(*conds)).order_by(*_order_by(sort)).limit(page_size).offset((page - 1) * page_size)
    ))
    return {"items": _task_items(db, tasks, now), "total": total, "page": page, "page_size": page_size,
            "pages": max(1, -(-total // page_size))}


def _csv_cell(v: Any) -> str:
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s  # chống CSV/formula injection


@router.get("/jobs/export")
def export_jobs(
    request: Request,
    role: Optional[str] = None,
    status: str = "",
    workflow_id: Optional[int] = None,
    stage_id: Optional[int] = None,
    q: str = "",
    created_from: Optional[str] = None,
    created_to: Optional[str] = None,
    deadline_from: Optional[str] = None,
    deadline_to: Optional[str] = None,
    completed_from: Optional[str] = None,
    completed_to: Optional[str] = None,
    archived: bool = False,
    sort: str = "-created_at",
    ctx: Ctx = Depends(require_mfa),
    db=Depends(get_db),
):
    """Xuất CSV với đúng bộ lọc + visibility như /jobs (tối đa 5000 dòng)."""
    now = _now(request)
    conds = _jobs_conditions(
        db, ctx.user, now, role=role, status=status, workflow_id=workflow_id, stage_id=stage_id, q=q,
        created_from=created_from, created_to=created_to, deadline_from=deadline_from, deadline_to=deadline_to,
        completed_from=completed_from, completed_to=completed_to, archived=archived,
    )
    tasks = list(db.scalars(select(Task).where(and_(*conds)).order_by(*_order_by(sort)).limit(CSV_LIMIT)))
    items = _task_items(db, tasks, now)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "quy_trinh", "tieu_de", "nhan", "trang_thai", "qua_han", "buoc_hien_tai", "nguoi_tao",
                "han_xu_ly", "ngay_tao", "ngay_hoan_thanh"])
    for it in items:
        w.writerow([_csv_cell(x) for x in (
            it["id"], it["workflow_name"], it["title"], ", ".join(it["labels"]), it["status"],
            "x" if it["is_overdue"] else "", it["current_stage"]["name"] if it["current_stage"] else "",
            it["creator"]["display_name"], it["deadline"], it["created_at"], it["completed_at"],
        )])
    return Response(
        content="\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="jobs.csv"'},
    )


@router.get("/workflows/{workflow_id}/board")
def workflow_board(
    workflow_id: int, request: Request, per_column: int = 50, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)
):
    now = _now(request)
    wf = _load_workflow(db, ctx.user, workflow_id)
    per_column = max(1, min(per_column, 200))
    base = and_(Task.workflow_id == wf.id, Task.archived_at.is_(None), visible_task_clause(ctx.user.id))
    stages = ordered_stages(db, wf.id)
    overdue_expr = case((and_(Task.deadline.is_not(None), Task.deadline < now), 1), else_=0)
    agg = {
        sid: (cnt or 0, int(od or 0))
        for sid, cnt, od in db.execute(
            select(Task.current_stage_id, func.count(Task.id), func.sum(overdue_expr))
            .where(base, Task.status == "processing").group_by(Task.current_stage_id)
        )
    }
    columns = []
    for s in stages:
        cnt, od = agg.get(s.id, (0, 0))
        tasks = list(db.scalars(
            select(Task).where(base, Task.status == "processing", Task.current_stage_id == s.id)
            .order_by(Task.deadline.is_(None), Task.deadline.asc(), Task.id.desc()).limit(per_column)
        ))
        columns.append({"stage": _stage_out(s), "count": cnt, "overdue_count": od, "tasks": _task_items(db, tasks, now)})
    system = {}
    for key in ("completed", "failed"):
        cnt = db.scalar(select(func.count(Task.id)).where(base, Task.status == key)) or 0
        tasks = list(db.scalars(
            select(Task).where(base, Task.status == key).order_by(Task.updated_at.desc(), Task.id.desc()).limit(per_column)
        ))
        system[key] = {"count": cnt, "tasks": _task_items(db, tasks, now)}
    return {
        "workflow": {"id": wf.id, "name": wf.name, "slug": wf.slug, "visibility": wf.visibility, "status": wf.status},
        "stages": columns, "completed": system["completed"], "failed": system["failed"],
    }
