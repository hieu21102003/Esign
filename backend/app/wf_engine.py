"""Workflow engine (W5): luật nghiệp vụ Workflow -> Stage -> Task -> Todo.

Mọi hàm ở đây chạy trong giao dịch của request (không tự commit); route chịu trách nhiệm commit rồi gửi email.
Bước ký (W6) cắm vào qua `wf_hooks` (StageHandler) — engine không biết gì về luồng ký.
"""
from __future__ import annotations

import math
import re
import threading
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import and_, or_, select

from .audit import audit
from .models import (
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
)
from .notifications import notify_users
from .wf_hooks import StageContext, get_stage_handler

_CTRL = re.compile(r"[\x00-\x1f\x7f]")

_LOCKS_GUARD = threading.Lock()
_TASK_LOCKS: dict[int, threading.Lock] = {}


def task_lock(task_id: int) -> threading.Lock:
    """Tuần tự hóa mọi thao tác chuyển stage trên cùng một task (chống hai người hoàn thành cùng lúc)."""
    with _LOCKS_GUARD:
        lk = _TASK_LOCKS.get(task_id)
        if lk is None:
            lk = threading.Lock()
            _TASK_LOCKS[task_id] = lk
        return lk


# --------------------------------------------------------------------------------------
# Phân quyền & visibility (lọc ở SERVER bằng mệnh đề SQL dùng chung cho list/board/search/export)
# --------------------------------------------------------------------------------------
def is_system_admin(user: User) -> bool:
    return user.role == "admin"


def workflow_roles_of(db, user_id: int, workflow_id: int) -> set[str]:
    return set(db.scalars(
        select(WorkflowRole.role).where(WorkflowRole.workflow_id == workflow_id, WorkflowRole.user_id == user_id)
    ))


def can_manage_workflow(db, user: User, workflow_id: int) -> bool:
    """Chỉ admin (quản trị hệ thống hoặc role admin của workflow) mới sửa cấu trúc workflow."""
    return is_system_admin(user) or "admin" in workflow_roles_of(db, user.id, workflow_id)


def is_stage_member_of_workflow(db, user_id: int, workflow_id: int) -> bool:
    return db.scalar(
        select(StageMember.user_id)
        .join(Stage, Stage.id == StageMember.stage_id)
        .where(Stage.workflow_id == workflow_id, StageMember.user_id == user_id)
        .limit(1)
    ) is not None


def can_see_workflow(db, user: User, workflow_id: int) -> bool:
    """Xem được cấu trúc workflow: quản trị hệ thống, người có role, thành viên stage, hoặc có task được xem."""
    return (
        is_system_admin(user)
        or bool(workflow_roles_of(db, user.id, workflow_id))
        or is_stage_member_of_workflow(db, user.id, workflow_id)
        or db.scalar(
            select(Task.id).where(Task.workflow_id == workflow_id, visible_task_clause(user.id)).limit(1)
        ) is not None
    )


def visible_task_clause(user_id: int):
    """Mệnh đề SQL: task mà user được phép thấy.

    - Người tham gia: creator, follower, assignee hiện tại (todo đang doing).
    - Role admin/supervisor của workflow thấy mọi task của workflow.
    - Workflow all_visible: mọi người có role (bất kỳ) trong workflow thấy mọi task.
    Quản trị hệ thống KHÔNG tự động thấy task (phải được gán role trong workflow, có audit).
    """
    follower_ids = select(TaskFollower.task_id).where(TaskFollower.user_id == user_id)
    assigned_ids = select(Todo.task_id).where(Todo.assignee_id == user_id, Todo.status == "doing")
    priv_wf = select(WorkflowRole.workflow_id).where(
        WorkflowRole.user_id == user_id, WorkflowRole.role.in_(("admin", "supervisor"))
    )
    open_wf = (
        select(WorkflowRole.workflow_id)
        .join(Workflow, Workflow.id == WorkflowRole.workflow_id)
        .where(WorkflowRole.user_id == user_id, Workflow.visibility == "all_visible")
    )
    return or_(
        Task.creator_id == user_id,
        Task.id.in_(follower_ids),
        Task.id.in_(assigned_ids),
        Task.workflow_id.in_(priv_wf),
        Task.workflow_id.in_(open_wf),
    )


def get_visible_task(db, user: User, task_id: int) -> Task:
    """Trả task nếu user được thấy; ngược lại 404 (không lộ sự tồn tại)."""
    task = db.scalar(select(Task).where(Task.id == task_id, visible_task_clause(user.id)))
    if task is None:
        raise HTTPException(404, "Không tìm thấy công việc")
    return task


# --------------------------------------------------------------------------------------
# Trạng thái suy ra
# --------------------------------------------------------------------------------------
def task_is_overdue(task: Task, now: datetime) -> bool:
    """'Quá hạn' không lưu cứng: deadline < now AND status=processing."""
    return task.status == "processing" and task.deadline is not None and task.deadline < now


def todo_is_overdue(todo: Todo, task: Task, now: datetime) -> bool:
    return (
        task.status == "processing" and todo.status == "doing"
        and todo.deadline is not None and todo.deadline < now
    )


# --------------------------------------------------------------------------------------
# Làm sạch & validate dữ liệu nhập
# --------------------------------------------------------------------------------------
def clean_line(raw: Optional[str], label: str, *, max_len: int, required: bool = True) -> str:
    s = _CTRL.sub(" ", raw or "").strip()
    if required and not s:
        raise HTTPException(422, f"{label} không được để trống")
    if len(s) > max_len:
        raise HTTPException(422, f"{label} tối đa {max_len} ký tự")
    return s


def clean_labels(raw: Optional[List[str]]) -> List[str]:
    out: List[str] = []
    for item in raw or []:
        s = _CTRL.sub(" ", str(item)).strip()
        if not s:
            continue
        if len(s) > 50:
            raise HTTPException(422, "Nhãn tối đa 50 ký tự")
        if s not in out:
            out.append(s)
    if len(out) > 20:
        raise HTTPException(422, "Tối đa 20 nhãn")
    return out


def validate_custom_values(
    db, fields: List[FormField], values: Optional[Dict[str, Any]], *, allowed_file_owner_ids: set[int]
) -> Dict[str, Any]:
    """Kiểm tra custom_values theo form_fields: required, đúng kiểu từng trường, từ chối key lạ.

    Khóa của custom_values là id của form_field (dạng chuỗi). Trả dict đã chuẩn hóa.
    """
    if values is None:
        values = {}
    if not isinstance(values, dict):
        raise HTTPException(422, "custom_values phải là đối tượng JSON")
    by_key = {str(f.id): f for f in fields}
    errors: List[str] = []
    unknown = sorted(k for k in values if str(k) not in by_key)
    if unknown:
        errors.append("Trường không tồn tại trong biểu mẫu: " + ", ".join(str(k)[:40] for k in unknown))

    out: Dict[str, Any] = {}
    for f in sorted(fields, key=lambda x: (x.order, x.id)):
        key = str(f.id)
        raw = values.get(key)
        blank = raw is None or (isinstance(raw, str) and not raw.strip())
        if blank:
            if f.required:
                errors.append(f"Trường '{f.label}' là bắt buộc")
            continue
        try:
            out[key] = _coerce_value(db, f, raw, allowed_file_owner_ids)
        except ValueError as exc:
            errors.append(f"Trường '{f.label}': {exc}")
    if errors:
        raise HTTPException(422, "; ".join(errors))
    return out


def _coerce_value(db, f: FormField, raw: Any, allowed_file_owner_ids: set[int]) -> Any:
    t = f.type
    if t in ("text", "textarea"):
        if not isinstance(raw, str):
            raise ValueError("phải là chuỗi")
        s = raw.strip() if t == "text" else raw.replace("\x00", "").strip()
        s = _CTRL.sub(" ", s) if t == "text" else s
        limit = 1000 if t == "text" else 20000
        if len(s) > limit:
            raise ValueError(f"tối đa {limit} ký tự")
        return s
    if t == "number":
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError("phải là số")
        if isinstance(raw, float) and not math.isfinite(raw):
            raise ValueError("phải là số hữu hạn")
        return raw
    if t == "date":
        if not isinstance(raw, str):
            raise ValueError("phải là ngày dạng YYYY-MM-DD")
        s = raw.strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            raise ValueError("phải là ngày hợp lệ dạng YYYY-MM-DD")
        try:
            return date.fromisoformat(s).isoformat()
        except ValueError:
            raise ValueError("phải là ngày hợp lệ dạng YYYY-MM-DD") from None
    if t == "select":
        opts = [str(o) for o in (f.options or [])]
        if not isinstance(raw, str) or raw not in opts:
            raise ValueError("giá trị không nằm trong danh sách lựa chọn")
        return raw
    if t == "user":
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValueError("phải là id người dùng")
        u = db.get(User, raw)
        if u is None or not u.is_active:
            raise ValueError("người dùng không tồn tại hoặc đã bị vô hiệu hóa")
        return raw
    if t == "file":
        if not isinstance(raw, str):
            raise ValueError("phải là id file đã tải lên")
        try:
            fid = str(uuid.UUID(raw))
        except ValueError:
            raise ValueError("id file không hợp lệ") from None
        sf = db.get(StoredFile, fid)
        if sf is None or sf.deleted_at is not None or sf.owner_id not in allowed_file_owner_ids:
            raise ValueError("file không tồn tại hoặc không thuộc về bạn")
        sf.is_workflow = True  # giữ file, không bị dọn dẹp sau 7 ngày như file ký lẻ
        return fid
    raise ValueError("kiểu trường không được hỗ trợ")


# --------------------------------------------------------------------------------------
# Audit + thông báo
# --------------------------------------------------------------------------------------
def _audit(db, ip: str, action: str, actor: Optional[User], task: Task, *,
           metadata: Optional[dict] = None, detail: Optional[str] = None) -> None:
    audit(
        db, ip, action, user=actor, task_id=task.id, workflow_id=task.workflow_id,
        document_id=task.document_id, detail=detail, metadata=metadata, commit=False,
    )


def _label(u: Optional[User]) -> str:
    return (u.display_name or u.username) if u is not None else "Hệ thống"


def _notify(db, st, ids, task: Task, kind: str, title: str, message: str, subject: str):
    return notify_users(
        db, st, ids, document_id=task.document_id, task_id=task.id, kind=kind, title=title, message=message,
        email_subject=f"[Quy trình] {subject}", commit=False,
    )


def stakeholder_ids(db, task: Task, *, include_doers: bool = True) -> set[int]:
    ids: set[int] = {task.creator_id}
    ids.update(db.scalars(select(TaskFollower.user_id).where(TaskFollower.task_id == task.id)))
    if include_doers:
        ids.update(db.scalars(
            select(Todo.assignee_id).where(Todo.task_id == task.id, Todo.status == "done")
        ))
    return ids


# --------------------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------------------
def ordered_stages(db, workflow_id: int, *, include_inactive: bool = False) -> List[Stage]:
    q = select(Stage).where(Stage.workflow_id == workflow_id)
    if not include_inactive:
        q = q.where(Stage.is_active.is_(True))
    return list(db.scalars(q.order_by(Stage.order)))


def enter_stage(
    db, st, task: Task, stage: Stage, actor: Optional[User], now: datetime, ip: str,
    *, assignee_ids: Optional[List[int]] = None,
) -> list:
    """Task vào stage: sinh todo cho thành viên stage, deadline = now + sla_hours. Trả danh sách email."""
    members = list(db.scalars(
        select(User)
        .join(StageMember, StageMember.user_id == User.id)
        .where(StageMember.stage_id == stage.id, User.is_active.is_(True))
        .order_by(User.id)
    ))
    if not members:
        raise HTTPException(409, f"Bước '{stage.name}' chưa có thành viên hoạt động để nhận việc")
    if assignee_ids is not None:
        dedup = list(dict.fromkeys(int(x) for x in assignee_ids))
        if not dedup:
            raise HTTPException(400, f"Cần chọn ít nhất 1 người nhận việc cho bước '{stage.name}'")
        by_id = {m.id: m for m in members}
        if any(uid not in by_id for uid in dedup):
            raise HTTPException(400, f"Người nhận việc phải thuộc thành viên (stage_members) của bước '{stage.name}'")
        chosen_members = [by_id[uid] for uid in dedup]
    else:
        chosen_members = members
    deadline = now + timedelta(hours=stage.sla_hours) if stage.sla_hours else None
    task.current_stage_id = stage.id
    task.deadline = deadline
    task.updated_at = now
    todos = [
        Todo(task_id=task.id, stage_id=stage.id, assignee_id=m.id, status="doing", deadline=deadline, created_at=now)
        for m in chosen_members
    ]
    db.add_all(todos)
    db.flush()
    _audit(db, ip, "stage_entered", actor, task, metadata={
        "stage_id": stage.id, "stage_order": stage.order, "stage_name": stage.name, "type": stage.type,
        "assign_mode": stage.assign_mode, "deadline": deadline.isoformat() if deadline else None,
    })
    for td in todos:
        _audit(db, ip, "todo_assigned", actor, task, metadata={
            "todo_id": td.id, "stage_id": stage.id, "assignee_id": td.assignee_id,
        })
    get_stage_handler(stage.type).on_enter(StageContext(db, st, task, stage, actor, now, ip), todos)
    deadline_txt = f" (hạn {deadline.strftime('%d/%m/%Y %H:%M')} UTC)." if deadline else "."
    if stage.type == "sign":
        n_title = f"Cần ký tài liệu: {task.title}"[:200]
        n_msg = (
            f"{_label(actor)} giao cho bạn ký tài liệu cho công việc \"{task.title}\" "
            f"ở bước \"{stage.name}\"{deadline_txt}"
        )
        n_subj = f"[Ký số] Cần ký tài liệu: {task.title}"
    else:
        n_title = f"Bạn được giao việc: {task.title}"[:200]
        n_msg = (
            f"{_label(actor)} giao cho bạn xử lý công việc \"{task.title}\" "
            f"ở bước \"{stage.name}\"{deadline_txt}"
        )
        n_subj = f"Bạn được giao việc: {task.title}"
    return _notify(
        db, st, [m.id for m in chosen_members], task, "task_assigned",
        n_title, n_msg, n_subj,
    )


def create_task(db, st, wf: Workflow, actor: User, *, title: str, labels: List[str], description_html: str,
                custom_values: Dict[str, Any], follower_ids: List[int], now: datetime, ip: str,
                assignee_ids: Optional[List[int]] = None) -> tuple[Task, list]:
    if wf.status != "active":
        raise HTTPException(409, "Quy trình đã đóng, không thể tạo công việc mới")
    stages = ordered_stages(db, wf.id)
    if not stages:
        raise HTTPException(409, "Quy trình chưa có bước nào")
    task = Task(
        workflow_id=wf.id, title=title, labels=labels, creator_id=actor.id, description_html=description_html,
        custom_values=custom_values, status="processing", created_at=now, updated_at=now,
    )
    db.add(task)
    db.flush()
    for uid in follower_ids:
        db.add(TaskFollower(task_id=task.id, user_id=uid))
    db.flush()
    _audit(db, ip, "task_created", actor, task, metadata={
        "title": title, "labels": labels, "follower_ids": follower_ids,
    })
    jobs: list = []
    notified: set[int] = {actor.id}
    if follower_ids:
        fo = [u for u in follower_ids if u != actor.id]
        jobs += _notify(db, st, fo, task, "follower_added", f"Bạn theo dõi: {title}"[:200],
                        f"{_label(actor)} đã tạo công việc \"{title}\" và thêm bạn làm người theo dõi.",
                        f"Bạn theo dõi công việc: {title}")
        notified.update(fo)
    managers = set(db.scalars(
        select(WorkflowRole.user_id).where(
            WorkflowRole.workflow_id == wf.id, WorkflowRole.role.in_(("admin", "supervisor"))
        )
    )) - notified
    jobs += _notify(db, st, managers, task, "task_created", f"Công việc mới: {title}"[:200],
                    f"{_label(actor)} vừa tạo công việc \"{title}\" trong quy trình \"{wf.name}\".",
                    f"Công việc mới: {title}")
    jobs += enter_stage(db, st, task, stages[0], actor, now, ip, assignee_ids=assignee_ids)
    return task, jobs


def _advance(ctx: StageContext, wf_stage: Stage, *, next_assignee_ids: Optional[List[int]] = None) -> list:
    """Stage hiện tại đã đóng: sang stage kế tiếp hoặc hoàn thành task nếu là stage cuối."""
    from .wf_signing import complete_linked_document_if_done

    db, task = ctx.db, ctx.task
    stages = ordered_stages(db, task.workflow_id)
    nxt = next((s for s in stages if s.order > wf_stage.order), None)
    if nxt is not None:
        return enter_stage(db, ctx.settings, task, nxt, ctx.actor, ctx.now, ctx.ip, assignee_ids=next_assignee_ids)
    task.status = "completed"
    task.completed_at = ctx.now
    task.updated_at = ctx.now
    complete_linked_document_if_done(db, task, ctx.actor, ctx.now, ctx.ip)
    _audit(db, ctx.ip, "task_completed", ctx.actor, task, metadata={"final_stage_id": wf_stage.id})
    recipients = stakeholder_ids(db, task) - ({ctx.actor.id} if ctx.actor else set())
    return _notify(
        db, ctx.settings, recipients, task, "task_completed", f"Công việc hoàn thành: {task.title}"[:200],
        f"Công việc \"{task.title}\" đã đi hết quy trình (hoàn tất bởi {_label(ctx.actor)}).",
        f"Công việc đã hoàn thành: {task.title}",
    )


def check_todo_actionable(db, user: User, todo: Todo, task: Task) -> Stage:
    """Kiểm tra mọi điều kiện để người dùng xử lý một todo. Trả Stage hiện tại."""
    if todo.assignee_id != user.id:
        # Người không được giao: nếu thấy task thì 403, không thì 404 (không lộ sự tồn tại)
        if db.scalar(select(Task.id).where(Task.id == task.id, visible_task_clause(user.id))) is None:
            raise HTTPException(404, "Không tìm thấy việc cần làm")
        raise HTTPException(403, "Bạn không phải người được giao việc này")
    stage = db.get(Stage, todo.stage_id)
    in_stage = db.scalar(
        select(StageMember.user_id).where(StageMember.stage_id == todo.stage_id, StageMember.user_id == user.id)
    )
    if in_stage is None:
        raise HTTPException(403, "Bạn không còn thuộc thành viên của bước này")
    if task.archived_at is not None:
        raise HTTPException(409, "Công việc đã được lưu trữ")
    if task.status != "processing":
        raise HTTPException(409, "Công việc không còn ở trạng thái đang xử lý")
    if todo.status != "doing":
        raise HTTPException(409, "Việc cần làm này đã được xử lý hoặc đã bị hủy")
    if task.current_stage_id != todo.stage_id:
        raise HTTPException(409, "Việc cần làm không thuộc bước hiện tại của công việc")
    return stage


def complete_todo(
    db, st, todo: Todo, task: Task, actor: User, result: Optional[str],
    now: datetime, ip: str, *, next_assignee_ids: Optional[List[int]] = None,
    decision: Optional[str] = None, failure_reason_id: Optional[int] = None,
    note: Optional[str] = None, _signed_verified: bool = False,
) -> list:
    stage = check_todo_actionable(db, actor, todo, task)
    if stage.type == "approve":
        dec = decision or "approve"
        if dec not in ("approve", "reject"):
            raise HTTPException(400, "decision phải là approve hoặc reject")
        if dec == "reject":
            if failure_reason_id is None:
                raise HTTPException(400, "Từ chối ở bước duyệt bắt buộc chọn failure_reason_id thuộc quy trình")
            return fail_todo(
                db, st, todo, task, actor, failure_reason_id, note or result, now, ip, decision="reject"
            )
    else:
        if decision is not None:
            raise HTTPException(400, "Chỉ bước duyệt (approve) mới hỗ trợ trường decision")
        dec = None

    ctx = StageContext(db, st, task, stage, actor, now, ip)
    handler = get_stage_handler(stage.type)
    payload = {
        "result": result,
        "next_assignee_ids": next_assignee_ids,
        "decision": dec,
        "_signed_verified": _signed_verified,
    }
    handler.before_complete(ctx, todo, payload)

    todo.status = "done"
    todo.result = result
    todo.completed_at = now
    task.updated_at = now
    meta = {
        "todo_id": todo.id, "stage_id": stage.id, "stage_order": stage.order,
        "assign_mode": stage.assign_mode, "has_result": bool(result),
    }
    if dec is not None:
        meta["decision"] = dec
    _audit(db, ip, "todo_completed", actor, task, metadata=meta)
    handler.after_complete(ctx, todo, payload)

    others = list(db.scalars(
        select(Todo).where(Todo.task_id == task.id, Todo.stage_id == stage.id, Todo.status == "doing",
                           Todo.id != todo.id)
    ))
    if stage.assign_mode == "any":
        for o in others:
            o.status = "cancelled"
            _audit(db, ip, "todo_cancelled", actor, task, metadata={
                "todo_id": o.id, "assignee_id": o.assignee_id, "reason": "stage_closed_by_any",
            })
        others = []
    if others:  # mode=all còn người chưa xong: chờ
        return []
    return _advance(ctx, stage, next_assignee_ids=next_assignee_ids)


def fail_todo(
    db, st, todo: Todo, task: Task, actor: User, failure_reason_id: int, note: Optional[str],
    now: datetime, ip: str, *, decision: Optional[str] = None, sync_document: bool = True,
) -> list:
    from .wf_signing import cancel_open_sign_steps_for_task

    stage = check_todo_actionable(db, actor, todo, task)
    reason = db.get(WorkflowFailureReason, failure_reason_id) if isinstance(failure_reason_id, int) else None
    if reason is None or reason.workflow_id != task.workflow_id:
        raise HTTPException(400, "Lý do thất bại không thuộc quy trình này")

    todo.status = "failed"
    todo.result = note
    todo.completed_at = now
    cancelled_assignees: List[int] = []
    for o in db.scalars(select(Todo).where(Todo.task_id == task.id, Todo.status == "doing", Todo.id != todo.id)):
        o.status = "cancelled"
        cancelled_assignees.append(o.assignee_id)
        _audit(db, ip, "todo_cancelled", actor, task, metadata={
            "todo_id": o.id, "assignee_id": o.assignee_id, "reason": "task_failed",
        })
    if sync_document and task.document_id:
        cancel_open_sign_steps_for_task(db, task, actor, now, ip, reason=note or reason.label)

    task.status = "failed"
    task.failure_reason_id = reason.id
    task.updated_at = now
    td_meta = {"todo_id": todo.id, "stage_id": stage.id}
    tk_meta = {
        "failure_reason_id": reason.id, "failure_reason": reason.label, "stage_id": stage.id, "note": note,
    }
    if decision is not None:
        td_meta["decision"] = decision
        tk_meta["decision"] = decision
    _audit(db, ip, "todo_failed", actor, task, metadata=td_meta)
    _audit(db, ip, "task_failed", actor, task, detail=reason.label, metadata=tk_meta)
    recipients = (stakeholder_ids(db, task) | set(cancelled_assignees)) - {actor.id}
    return _notify(
        db, st, recipients, task, "task_failed", f"Công việc thất bại: {task.title}"[:200],
        f"{_label(actor)} đánh dấu công việc \"{task.title}\" thất bại: {reason.label}",
        f"Công việc thất bại: {task.title}",
    )


def cancel_task(
    db, st, task: Task, actor: User, reason: str, now: datetime, ip: str,
    *, failure_reason_id: Optional[int] = None,
) -> list:
    """Hủy công việc đang xử lý: đóng mọi todo đang mở và hủy bước ký/tài liệu gắn kèm."""
    from .wf_signing import cancel_open_sign_steps_for_task

    if task.archived_at is not None:
        raise HTTPException(409, "Công việc đã được lưu trữ")
    if task.status != "processing":
        raise HTTPException(409, "Chỉ hủy được công việc đang xử lý")
    if failure_reason_id is not None:
        fr = db.get(WorkflowFailureReason, failure_reason_id)
        if fr is None or fr.workflow_id != task.workflow_id:
            raise HTTPException(400, "Lý do thất bại không thuộc quy trình này")

    cancelled_assignees: List[int] = []
    for o in db.scalars(select(Todo).where(Todo.task_id == task.id, Todo.status == "doing")):
        o.status = "cancelled"
        cancelled_assignees.append(o.assignee_id)
        _audit(db, ip, "todo_cancelled", actor, task, metadata={
            "todo_id": o.id, "assignee_id": o.assignee_id, "reason": "task_cancelled",
        })

    if task.document_id:
        cancel_open_sign_steps_for_task(db, task, actor, now, ip, reason=reason or "Công việc đã bị hủy")

    task.status = "failed"
    task.failure_reason_id = failure_reason_id
    task.updated_at = now
    _audit(
        db, ip, "task_cancelled", actor, task, detail=reason or None,
        metadata={"reason": reason or None, "failure_reason_id": failure_reason_id},
    )
    recipients = (stakeholder_ids(db, task) | set(cancelled_assignees)) - {actor.id}
    return _notify(
        db, st, recipients, task, "task_failed", f"Công việc đã bị hủy: {task.title}"[:200],
        f"{_label(actor)} đã hủy công việc \"{task.title}\"" + (f": {reason}" if reason else "."),
        f"Công việc đã bị hủy: {task.title}",
    )


def edit_task_notify(db, st, task: Task, actor: User, changed: List[str]) -> list:
    recipients = set(stakeholder_ids(db, task, include_doers=False))
    recipients.update(db.scalars(select(Todo.assignee_id).where(Todo.task_id == task.id, Todo.status == "doing")))
    recipients.discard(actor.id)
    return _notify(
        db, st, recipients, task, "task_edited", f"Công việc được chỉnh sửa: {task.title}"[:200],
        f"{_label(actor)} đã chỉnh sửa công việc \"{task.title}\" ({', '.join(changed)}).",
        f"Công việc được chỉnh sửa: {task.title}",
    )


def add_follower(db, st, task: Task, actor: User, user: User, ip: str) -> list:
    db.add(TaskFollower(task_id=task.id, user_id=user.id))
    db.flush()
    _audit(db, ip, "follower_added", actor, task, metadata={"follower_id": user.id})
    if user.id == actor.id:
        return []
    return _notify(
        db, st, [user.id], task, "follower_added", f"Bạn theo dõi: {task.title}"[:200],
        f"{_label(actor)} đã thêm bạn làm người theo dõi công việc \"{task.title}\".",
        f"Bạn theo dõi công việc: {task.title}",
    )
