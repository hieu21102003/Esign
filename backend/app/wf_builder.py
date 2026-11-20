"""Logic Workflow Builder & ràng buộc sửa cấu trúc khi đã có task/todo (W8)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from fastapi import HTTPException
from sqlalchemy import delete, func, select

from .audit import audit
from .html_sanitize import sanitize_html
from .models import (
    Certificate,
    FormField,
    Stage,
    StageMember,
    Task,
    Todo,
    User,
    UserCertificate,
    Workflow,
    WorkflowFailureReason,
    WorkflowRole,
    WorkflowTemplate,
    utcnow,
)
from .wf_engine import clean_line, ordered_stages
from .wf_templates import validate_template_definition, workflow_to_definition


def require_active_users(db, ids: Sequence[int], what: str) -> None:
    uniq = set(ids)
    if not uniq:
        return
    found = set(db.scalars(select(User.id).where(User.id.in_(uniq), User.is_active.is_(True))))
    if found != uniq:
        raise HTTPException(400, f"{what} gồm người dùng không tồn tại hoặc đã bị vô hiệu hóa")


def validate_stage_specs(stages_in: Sequence[Any], *, allow_empty_members: bool = False) -> None:
    """Validate danh sách stage đầu vào: >=1 active, tên không trùng, sla_hours > 0, sign có thành viên."""
    active_items = [s for s in stages_in if getattr(s, "is_active", True)]
    if not active_items:
        raise HTTPException(400, "Quy trình phải có ít nhất 1 giai đoạn đang hoạt động")
    seen_names: set[str] = set()
    for s in active_items:
        name = clean_line(s.name, "Tên bước", max_len=200)
        key = name.casefold()
        if key in seen_names:
            raise HTTPException(400, f"Tên bước '{name}' bị trùng lặp trong quy trình")
        seen_names.add(key)
        if s.sla_hours is not None and s.sla_hours <= 0:
            raise HTTPException(400, f"Bước '{name}': sla_hours phải > 0")
        mids = set(s.member_ids or [])
        if not allow_empty_members and not mids:
            raise HTTPException(400, f"Bước '{name}' cần ít nhất 1 thành viên")
        if not allow_empty_members and s.type == "sign" and not mids:
            raise HTTPException(400, f"Bước ký '{name}' bắt buộc phải có ít nhất 1 thành viên")


def compute_workflow_warnings(db, workflow_id: int) -> List[dict]:
    """Cảnh báo không chặn cứng cho các bước sign (thiếu thành viên, thiếu cert, thiếu PIN hoặc PIN bị khóa)."""
    now = utcnow()
    warnings: List[dict] = []
    stages = [s for s in ordered_stages(db, workflow_id) if s.type == "sign"]
    if not stages:
        return warnings

    sids = [s.id for s in stages]
    rows = db.execute(select(StageMember.stage_id, StageMember.user_id).where(StageMember.stage_id.in_(sids))).all()
    by_stage: Dict[int, List[int]] = {sid: [] for sid in sids}
    all_uids: set[int] = set()
    for sid, uid in rows:
        by_stage[sid].append(uid)
        all_uids.add(uid)

    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(all_uids)))} if all_uids else {}
    cert_uids = set(db.scalars(
        select(UserCertificate.user_id)
        .join(Certificate, Certificate.id == UserCertificate.cert_id)
        .where(UserCertificate.user_id.in_(all_uids), Certificate.active.is_(True))
    )) if all_uids else set()

    for st in stages:
        uids = sorted(set(by_stage.get(st.id, [])))
        if not uids:
            warnings.append({
                "code": "sign_stage_no_members",
                "stage_id": st.id,
                "stage_name": st.name,
                "message": f"Bước ký '{st.name}' chưa có thành viên thực hiện.",
            })
            continue
        for uid in uids:
            u = users.get(uid)
            if u is None or not u.is_active:
                warnings.append({
                    "code": "member_inactive",
                    "stage_id": st.id,
                    "stage_name": st.name,
                    "user_id": uid,
                    "message": f"Bước ký '{st.name}': thành viên #{uid} đã bị vô hiệu hóa.",
                })
                continue
            label = u.display_name or u.username
            if uid not in cert_uids:
                warnings.append({
                    "code": "missing_cert",
                    "stage_id": st.id,
                    "stage_name": st.name,
                    "user_id": uid,
                    "username": u.username,
                    "message": f"Bước ký '{st.name}': {label} (@{u.username}) chưa có chứng thư số hoạt động.",
                })
            if not u.signing_pin_hash:
                warnings.append({
                    "code": "missing_pin",
                    "stage_id": st.id,
                    "stage_name": st.name,
                    "user_id": uid,
                    "username": u.username,
                    "message": f"Bước ký '{st.name}': {label} (@{u.username}) chưa thiết lập mã PIN ký 6 số.",
                })
            elif u.pin_locked_until and u.pin_locked_until > now:
                warnings.append({
                    "code": "pin_locked",
                    "stage_id": st.id,
                    "stage_name": st.name,
                    "user_id": uid,
                    "username": u.username,
                    "message": f"Bước ký '{st.name}': PIN ký của {label} (@{u.username}) đang tạm khóa.",
                })
    return warnings


def stage_usage_stats(db, workflow_id: int) -> Dict[int, dict]:
    """Trả map stage_id -> {processing_count, history_count, doing_assignees}."""
    proc_rows = db.execute(
        select(Task.current_stage_id, func.count(Task.id))
        .where(Task.workflow_id == workflow_id, Task.status == "processing", Task.current_stage_id.is_not(None))
        .group_by(Task.current_stage_id)
    ).all()
    hist_task_rows = db.execute(
        select(Task.current_stage_id, func.count(Task.id))
        .where(Task.workflow_id == workflow_id, Task.current_stage_id.is_not(None))
        .group_by(Task.current_stage_id)
    ).all()
    todo_rows = db.execute(
        select(Todo.stage_id, Todo.assignee_id, Todo.status)
        .join(Task, Task.id == Todo.task_id)
        .where(Task.workflow_id == workflow_id)
    ).all()

    out: Dict[int, dict] = {}

    def slot(sid: int) -> dict:
        return out.setdefault(sid, {"processing_count": 0, "history_count": 0, "doing_assignees": set()})

    for sid, cnt in proc_rows:
        slot(int(sid))["processing_count"] = int(cnt or 0)
    for sid, cnt in hist_task_rows:
        slot(int(sid))["history_count"] += int(cnt or 0)
    for sid, uid, status in todo_rows:
        st = slot(int(sid))
        st["history_count"] += 1
        if status == "doing":
            st["doing_assignees"].add(int(uid))
    return out


def field_ids_with_data(db, workflow_id: int) -> set[int]:
    """Các form_field.id đã từng lưu giá trị thực tế trong ít nhất một task của quy trình."""
    used: set[int] = set()
    for cv in db.scalars(select(Task.custom_values).where(Task.workflow_id == workflow_id)):
        if not isinstance(cv, dict):
            continue
        for k, v in cv.items():
            if v is not None and (not isinstance(v, str) or v.strip() != ""):
                try:
                    used.add(int(k))
                except (TypeError, ValueError):
                    pass
    return used


def renumber_stages(db, wf_id: int, active_ordered: List[Stage], inactive_list: List[Stage]) -> None:
    """Cập nhật thứ tự stage theo 2 pha để tránh đụng UniqueConstraint(workflow_id, order)."""
    all_rows = active_ordered + inactive_list
    if not all_rows:
        return
    max_order = db.scalar(select(func.max(Stage.order)).where(Stage.workflow_id == wf_id)) or 0
    temp_base = max(100000, int(max_order) + 1000)
    for idx, s in enumerate(all_rows):
        s.order = temp_base + idx
    db.flush()
    for idx, s in enumerate(active_ordered, start=1):
        s.order = idx
    base_inact = len(active_ordered)
    for idx, s in enumerate(inactive_list, start=1):
        s.order = base_inact + idx
    db.flush()


def _build_stage_row(db, wf_id: int, order: int, s_in: Any) -> Stage:
    require_active_users(db, s_in.member_ids, f"Thành viên bước '{s_in.name}'")
    row = Stage(
        workflow_id=wf_id,
        order=order,
        name=clean_line(s_in.name, "Tên bước", max_len=200),
        type=s_in.type,
        sla_hours=s_in.sla_hours or None,
        guide_html=sanitize_html(s_in.guide_html),
        assign_mode=s_in.assign_mode,
        is_active=getattr(s_in, "is_active", True),
    )
    db.add(row)
    db.flush()
    for uid in sorted(set(s_in.member_ids)):
        db.add(StageMember(stage_id=row.id, user_id=uid))
    return row


def patch_stages_with_rules(db, wf: Workflow, stages_in: Sequence[Any]) -> dict:
    """Áp dụng đầy đủ quy tắc sửa/thêm/xóa/vô hiệu/đổi thứ tự stage khi đã có task."""
    validate_stage_specs(stages_in, allow_empty_members=(wf.status == "closed"))
    existing = ordered_stages(db, wf.id, include_inactive=True)
    by_id = {s.id: s for s in existing}
    existing_active = [s for s in existing if s.is_active]
    active_ids_in_order = [s.id for s in existing_active]

    stats = stage_usage_stats(db, wf.id)
    processing_orders = {
        s.order for s in existing_active if stats.get(s.id, {}).get("processing_count", 0) > 0
    }
    any_processing = bool(processing_orders)

    diff: dict = {"added": [], "updated": [], "deactivated": [], "deleted": [], "reordered": False}
    seen_ids: set[int] = set()
    desired_active: List[Stage] = []
    explicit_inactive: List[Stage] = []

    temp_order = max((s.order for s in existing), default=0) + 50000

    for idx_in, s_in in enumerate(stages_in):
        if s_in.id is None:
            if not getattr(s_in, "is_active", True):
                raise HTTPException(400, f"Không thể tạo bước mới '{s_in.name}' ở trạng thái vô hiệu")
            # Kiểm tra quy tắc chèn giữa: các stage cũ xuất hiện sau vị trí chèn này không được có task processing
            following_existing = [x.id for x in stages_in[idx_in + 1:] if x.id is not None and getattr(x, "is_active", True)]
            if following_existing and any_processing:
                after_orders = [by_id[eid].order for eid in following_existing if eid in by_id]
                if after_orders and any(po >= min(after_orders) for po in processing_orders):
                    raise HTTPException(
                        409,
                        f"Không thể chèn bước '{s_in.name}' vào giữa khi có công việc đang xử lý ở bước sau; hãy thêm vào cuối quy trình",
                    )
            temp_order += 1
            row = _build_stage_row(db, wf.id, temp_order, s_in)
            desired_active.append(row)
            diff["added"].append({"id": row.id, "name": row.name, "type": row.type})
            continue

        row = by_id.get(s_in.id)
        if row is None:
            raise HTTPException(400, f"Bước id={s_in.id} không thuộc quy trình này")
        if s_in.id in seen_ids:
            raise HTTPException(400, f"Trùng lặp bước id={s_in.id} trong danh sách")
        seen_ids.add(s_in.id)

        st_stat = stats.get(row.id, {"processing_count": 0, "history_count": 0, "doing_assignees": set()})
        want_active = getattr(s_in, "is_active", True)

        if row.is_active and not want_active:
            if st_stat["processing_count"] > 0:
                raise HTTPException(409, f"Bước '{row.name}' đang có công việc xử lý, không thể vô hiệu hóa")
            row.is_active = False
            explicit_inactive.append(row)
            diff["deactivated"].append({"id": row.id, "name": row.name})
            continue

        if s_in.type != row.type and any_processing:
            raise HTTPException(409, f"Bước '{row.name}': không được đổi loại stage ({row.type} -> {s_in.type}) khi còn công việc đang xử lý")

        new_members = set(s_in.member_ids)
        if wf.status == "active" and not new_members:
            raise HTTPException(400, f"Bước '{s_in.name}' cần ít nhất 1 thành viên")
        if s_in.type == "sign" and not new_members and (wf.status == "active"):
            raise HTTPException(400, f"Bước ký '{s_in.name}' cần ít nhất 1 thành viên")
        require_active_users(db, list(new_members), f"Thành viên bước '{s_in.name}'")

        old_members = set(db.scalars(select(StageMember.user_id).where(StageMember.stage_id == row.id)))
        busy = st_stat["doing_assignees"]
        if (old_members - new_members) & busy:
            raise HTTPException(409, f"Bước '{row.name}': không thể gỡ thành viên đang có việc cần làm chưa xử lý")
        if busy and s_in.assign_mode != row.assign_mode:
            raise HTTPException(409, f"Bước '{row.name}': không đổi chế độ any/all khi còn việc đang xử lý")

        row_changes: dict = {}
        new_name = clean_line(s_in.name, "Tên bước", max_len=200)
        new_guide = sanitize_html(s_in.guide_html)
        new_sla = s_in.sla_hours or None
        if new_name != row.name:
            row_changes["name"] = [row.name, new_name]
        if s_in.type != row.type:
            row_changes["type"] = [row.type, s_in.type]
        if new_sla != row.sla_hours:
            row_changes["sla_hours"] = [row.sla_hours, new_sla]
        if new_guide != (row.guide_html or ""):
            row_changes["guide_html"] = True
        if s_in.assign_mode != row.assign_mode:
            row_changes["assign_mode"] = [row.assign_mode, s_in.assign_mode]
        if new_members != old_members:
            row_changes["member_ids"] = [sorted(old_members), sorted(new_members)]
        if not row.is_active and want_active:
            row_changes["is_active"] = [False, True]

        row.name = new_name
        row.type = s_in.type
        row.sla_hours = new_sla
        row.guide_html = new_guide
        row.assign_mode = s_in.assign_mode
        row.is_active = want_active

        for uid in new_members - old_members:
            db.add(StageMember(stage_id=row.id, user_id=uid))
        if old_members - new_members:
            db.execute(delete(StageMember).where(
                StageMember.stage_id == row.id, StageMember.user_id.in_(old_members - new_members)
            ))

        if row_changes:
            diff["updated"].append({"id": row.id, "changes": row_changes})
        if row.is_active:
            desired_active.append(row)
        else:
            explicit_inactive.append(row)

    # Kiểm tra các stage hiện có nhưng KHÔNG được gửi lên -> yêu cầu xóa hoặc tự chuyển sang is_active=false nếu đã có lịch sử
    omitted_ids = [s.id for s in existing if s.id not in seen_ids]
    inactive_keep = list(explicit_inactive)
    for sid in omitted_ids:
        row = by_id[sid]
        st_stat = stats.get(sid, {"processing_count": 0, "history_count": 0})
        if st_stat["processing_count"] > 0:
            raise HTTPException(409, f"Bước '{row.name}' đang có công việc xử lý, không thể xóa hoặc vô hiệu hóa")
        if st_stat["history_count"] > 0 or not row.is_active:
            if row.is_active:
                row.is_active = False
                diff["deactivated"].append({"id": row.id, "name": row.name})
            inactive_keep.append(row)
        else:
            db.delete(row)
            diff["deleted"].append({"id": sid, "name": row.name})

    if not desired_active:
        raise HTTPException(400, "Quy trình phải còn ít nhất 1 giai đoạn đang hoạt động")

    # Kiểm tra đổi thứ tự tương đối giữa các stage active cũ đang tồn tại
    surviving_new = [s.id for s in desired_active if s.id in active_ids_in_order]
    surviving_old = [sid for sid in active_ids_in_order if sid in set(surviving_new)]
    if surviving_new != surviving_old:
        if any_processing:
            raise HTTPException(409, "Không được đổi thứ tự giai đoạn khi đang có công việc xử lý")
        diff["reordered"] = True

    renumber_stages(db, wf.id, desired_active, inactive_keep)
    return diff


def patch_fields_with_rules(db, wf: Workflow, fields_in: Sequence[Any]) -> dict:
    """Thêm/sửa/vô hiệu form_fields; chặn xóa hoặc đổi kiểu trường đã có dữ liệu."""
    existing = {f.id: f for f in db.scalars(select(FormField).where(FormField.workflow_id == wf.id))}
    used_ids = field_ids_with_data(db, wf.id)
    diff: dict = {"added": [], "updated": [], "deactivated": [], "deleted": []}
    keep: set[int] = set()

    for idx, f in enumerate(fields_in, start=1):
        if f.type == "select":
            opts = [clean_line(o, "Lựa chọn", max_len=200) for o in (f.options or [])]
            if not opts:
                raise HTTPException(400, f"Trường '{f.label}' (select) cần ít nhất 1 lựa chọn")
        else:
            opts = None
        label = clean_line(f.label, "Nhãn trường", max_len=200)
        placeholder = clean_line(f.placeholder, "Placeholder", max_len=200, required=False)
        want_active = getattr(f, "is_active", True)

        if f.id is None:
            row = FormField(
                workflow_id=wf.id, label=label, type=f.type, required=f.required,
                placeholder=placeholder, options=opts, order=idx, is_active=want_active,
            )
            db.add(row)
            db.flush()
            keep.add(row.id)
            diff["added"].append({"id": row.id, "label": label, "type": f.type})
            continue

        row = existing.get(f.id)
        if row is None:
            raise HTTPException(400, f"Trường id={f.id} không thuộc quy trình này")
        keep.add(f.id)

        if f.id in used_ids and f.type != row.type:
            raise HTTPException(
                409,
                f"Trường '{row.label}' đã có dữ liệu trên công việc cũ: không thể đổi kiểu ({row.type} -> {f.type}); "
                f"hãy vô hiệu hóa (is_active=false) và tạo trường mới",
            )

        changes: dict = {}
        if label != row.label:
            changes["label"] = [row.label, label]
        if f.type != row.type:
            changes["type"] = [row.type, f.type]
        if bool(f.required) != bool(row.required):
            changes["required"] = [bool(row.required), bool(f.required)]
        if bool(want_active) != bool(row.is_active):
            changes["is_active"] = [bool(row.is_active), bool(want_active)]
            if not want_active:
                diff["deactivated"].append({"id": row.id, "label": label})

        row.label = label
        row.type = f.type
        row.required = f.required
        row.placeholder = placeholder
        row.options = opts
        row.order = idx
        row.is_active = want_active
        if changes:
            diff["updated"].append({"id": row.id, "changes": changes})

    for fid in set(existing) - keep:
        row = existing[fid]
        if fid in used_ids:
            raise HTTPException(
                409,
                f"Trường '{row.label}' đã có dữ liệu trên công việc cũ nên không thể xóa; hãy chuyển sang vô hiệu hóa (is_active=false)",
            )
        diff["deleted"].append({"id": fid, "label": row.label})
        db.delete(row)

    db.flush()
    return diff


def ensure_unique_slug(db, raw_slug: str) -> str:
    base = raw_slug[:90].strip("-") or "workflow"
    cand = base
    idx = 2
    while db.scalar(select(Workflow.id).where(Workflow.slug == cand)) is not None:
        suffix = f"-{idx}"
        cand = f"{base[:100 - len(suffix)]}{suffix}"
        idx += 1
    return cand


def ensure_unique_template_slug(db, raw_slug: str) -> str:
    base = raw_slug[:90].strip("-") or "template"
    cand = base
    idx = 2
    while db.scalar(select(WorkflowTemplate.id).where(WorkflowTemplate.slug == cand)) is not None:
        suffix = f"-{idx}"
        cand = f"{base[:100 - len(suffix)]}{suffix}"
        idx += 1
    return cand


def create_workflow_from_template(
    db,
    actor: User,
    ip: str,
    tpl: WorkflowTemplate,
    *,
    name: Optional[str],
    slug: Optional[str],
    category: Optional[str],
    description: Optional[str],
    visibility: Optional[str],
    status: str,
    admin_ids: Optional[List[int]],
    stage_members: Optional[Dict[str, List[int]]],
    default_member_ids: Optional[List[int]],
    slugify_fn,
) -> Workflow:
    defn = validate_template_definition(tpl.definition)
    wf_name = clean_line(name, "Tên quy trình", max_len=200) if name else tpl.name
    base_slug = (slug or "").strip().lower() or slugify_fn(wf_name)
    wf_slug = ensure_unique_slug(db, base_slug)

    vis = visibility or defn["visibility"]
    if vis not in ("followers_only", "all_visible"):
        raise HTTPException(400, "visibility không hợp lệ")

    admin_uids = sorted(set(admin_ids or []) | {actor.id})
    require_active_users(db, admin_uids, "Quản trị quy trình")
    if default_member_ids:
        require_active_users(db, default_member_ids, "Thành viên mặc định")

    wf = Workflow(
        name=wf_name,
        slug=wf_slug,
        category=clean_line(category if category is not None else tpl.category, "Phân loại", max_len=100, required=False),
        description=(description if description is not None else tpl.description).strip(),
        guide_html=defn["guide_html"],
        visibility=vis,
        status=status if status in ("active", "closed") else "active",
        creator_id=actor.id,
        created_at=utcnow(),
    )
    db.add(wf)
    db.flush()

    sm_map = {str(k): list(v) for k, v in (stage_members or {}).items()}
    for idx, s in enumerate(defn["stages"], start=1):
        row = Stage(
            workflow_id=wf.id,
            order=idx,
            name=s["name"],
            type=s["type"],
            sla_hours=s["sla_hours"],
            guide_html=s["guide_html"],
            assign_mode=s["assign_mode"],
            is_active=True,
        )
        db.add(row)
        db.flush()
        mids = sm_map.get(str(idx)) or sm_map.get(s["name"]) or list(default_member_ids or [])
        if mids:
            require_active_users(db, mids, f"Thành viên bước '{s['name']}'")
            for uid in sorted(set(mids)):
                db.add(StageMember(stage_id=row.id, user_id=uid))

    for idx, f in enumerate(defn["form_fields"], start=1):
        db.add(FormField(
            workflow_id=wf.id,
            label=f["label"],
            type=f["type"],
            required=f["required"],
            placeholder=f["placeholder"],
            options=f.get("options"),
            order=idx,
            is_active=True,
        ))

    for uid in admin_uids:
        db.add(WorkflowRole(workflow_id=wf.id, user_id=uid, role="admin"))

    for idx, lbl in enumerate(defn["failure_reasons"], start=1):
        db.add(WorkflowFailureReason(workflow_id=wf.id, label=lbl, order=idx))

    db.flush()
    audit(
        db, ip, "workflow_created", user=actor, workflow_id=wf.id, detail=wf.name, commit=False,
        metadata={"slug": wf.slug, "template_id": tpl.id, "template_slug": tpl.slug, "source": "from_template"},
    )
    return wf


def clone_workflow(
    db,
    src: Workflow,
    actor: User,
    ip: str,
    *,
    name: Optional[str],
    slug: Optional[str],
    slugify_fn,
) -> Workflow:
    wf_name = clean_line(name, "Tên quy trình", max_len=200) if name else f"{src.name} (Bản sao)"[:200]
    wf_slug = ensure_unique_slug(db, (slug or "").strip().lower() or slugify_fn(wf_name))

    dst = Workflow(
        name=wf_name,
        slug=wf_slug,
        category=src.category,
        description=src.description,
        guide_html=src.guide_html,
        visibility=src.visibility,
        status="active",
        creator_id=actor.id,
        created_at=utcnow(),
    )
    db.add(dst)
    db.flush()

    stages = ordered_stages(db, src.id)
    for idx, s in enumerate(stages, start=1):
        row = Stage(
            workflow_id=dst.id,
            order=idx,
            name=s.name,
            type=s.type,
            sla_hours=s.sla_hours,
            guide_html=s.guide_html,
            assign_mode=s.assign_mode,
            is_active=True,
        )
        db.add(row)
        db.flush()
        for uid in db.scalars(select(StageMember.user_id).where(StageMember.stage_id == s.id)):
            db.add(StageMember(stage_id=row.id, user_id=uid))

    fields = list(db.scalars(
        select(FormField).where(FormField.workflow_id == src.id, FormField.is_active.is_(True))
        .order_by(FormField.order, FormField.id)
    ))
    for idx, f in enumerate(fields, start=1):
        db.add(FormField(
            workflow_id=dst.id, label=f.label, type=f.type, required=f.required,
            placeholder=f.placeholder, options=list(f.options) if f.options else None,
            order=idx, is_active=True,
        ))

    roles = list(db.scalars(select(WorkflowRole).where(WorkflowRole.workflow_id == src.id)))
    pairs = {(r.user_id, r.role) for r in roles} | {(actor.id, "admin")}
    for uid, role in sorted(pairs):
        db.add(WorkflowRole(workflow_id=dst.id, user_id=uid, role=role))

    reasons = list(db.scalars(
        select(WorkflowFailureReason).where(WorkflowFailureReason.workflow_id == src.id)
        .order_by(WorkflowFailureReason.order, WorkflowFailureReason.id)
    ))
    for idx, r in enumerate(reasons, start=1):
        db.add(WorkflowFailureReason(workflow_id=dst.id, label=r.label, order=idx))

    db.flush()
    audit(
        db, ip, "workflow_cloned", user=actor, workflow_id=dst.id, detail=dst.name, commit=False,
        metadata={"source_workflow_id": src.id, "slug": dst.slug},
    )
    return dst


def save_workflow_as_template(
    db,
    wf: Workflow,
    actor: User,
    ip: str,
    *,
    name: Optional[str],
    category: Optional[str],
    description: Optional[str],
    slugify_fn,
) -> WorkflowTemplate:
    tname = clean_line(name, "Tên mẫu", max_len=200) if name else wf.name
    tslug = ensure_unique_template_slug(db, slugify_fn(tname))
    stages = ordered_stages(db, wf.id)
    fields = list(db.scalars(
        select(FormField).where(FormField.workflow_id == wf.id, FormField.is_active.is_(True))
        .order_by(FormField.order, FormField.id)
    ))
    reasons = list(db.scalars(
        select(WorkflowFailureReason).where(WorkflowFailureReason.workflow_id == wf.id)
        .order_by(WorkflowFailureReason.order, WorkflowFailureReason.id)
    ))
    defn = validate_template_definition(workflow_to_definition(db, wf, stages, fields, reasons))
    tpl = WorkflowTemplate(
        slug=tslug,
        name=tname,
        category=clean_line(category if category is not None else wf.category, "Phân loại", max_len=100, required=False),
        description=(description if description is not None else wf.description).strip(),
        definition=defn,
        is_builtin=False,
        creator_id=actor.id,
        created_at=utcnow(),
    )
    db.add(tpl)
    db.flush()
    audit(
        db, ip, "workflow_saved_as_template", user=actor, workflow_id=wf.id, detail=tpl.name, commit=False,
        metadata={"template_id": tpl.id, "template_slug": tpl.slug},
    )
    return tpl


def add_single_stage(db, wf: Workflow, s_in: Any, *, insert_after_order: Optional[int] = None) -> tuple[Stage, dict]:
    """Thêm 1 stage mới vào cuối (mặc định) hoặc chèn sau insert_after_order."""
    existing = ordered_stages(db, wf.id, include_inactive=True)
    active = [s for s in existing if s.is_active]
    inactive = [s for s in existing if not s.is_active]
    name = clean_line(s_in.name, "Tên bước", max_len=200)
    if any(s.name.casefold() == name.casefold() for s in active):
        raise HTTPException(400, f"Tên bước '{name}' bị trùng lặp trong quy trình")
    if s_in.sla_hours is not None and s_in.sla_hours <= 0:
        raise HTTPException(400, f"Bước '{name}': sla_hours phải > 0")
    if wf.status == "active" and not set(s_in.member_ids or []):
        raise HTTPException(400, f"Bước '{name}' cần ít nhất 1 thành viên")
    if s_in.type == "sign" and wf.status == "active" and not set(s_in.member_ids or []):
        raise HTTPException(400, f"Bước ký '{name}' cần ít nhất 1 thành viên")

    stats = stage_usage_stats(db, wf.id)
    proc_orders = {s.order for s in active if stats.get(s.id, {}).get("processing_count", 0) > 0}
    pos = len(active)
    if insert_after_order is not None:
        pos = sum(1 for s in active if s.order <= insert_after_order)
        if any(po > insert_after_order for po in proc_orders):
            raise HTTPException(409, "Không thể chèn bước vào giữa khi có công việc đang xử lý ở bước sau vị trí chèn")

    temp_order = max((s.order for s in existing), default=0) + 50000
    row = _build_stage_row(db, wf.id, temp_order, s_in)
    new_active = active[:pos] + [row] + active[pos:]
    renumber_stages(db, wf.id, new_active, inactive)
    return row, {"stage_added": {"id": row.id, "name": row.name, "order": row.order, "type": row.type}}


def update_single_stage(db, wf: Workflow, stage_id: int, s_in: Any) -> tuple[Stage, dict]:
    row = db.get(Stage, stage_id)
    if row is None or row.workflow_id != wf.id:
        raise HTTPException(404, "Không tìm thấy giai đoạn")
    stats = stage_usage_stats(db, wf.id)
    any_processing = any(st["processing_count"] > 0 for st in stats.values())
    st_stat = stats.get(row.id, {"processing_count": 0, "doing_assignees": set()})

    diff: dict = {}
    if s_in.name is not None:
        new_name = clean_line(s_in.name, "Tên bước", max_len=200)
        dup = db.scalar(
            select(Stage.id).where(
                Stage.workflow_id == wf.id, Stage.id != row.id, Stage.is_active.is_(True),
                func.lower(Stage.name) == new_name.lower(),
            ).limit(1)
        )
        if dup is not None:
            raise HTTPException(400, f"Tên bước '{new_name}' bị trùng lặp trong quy trình")
        if new_name != row.name:
            diff["name"] = [row.name, new_name]
            row.name = new_name
    if s_in.type is not None and s_in.type != row.type:
        if any_processing:
            raise HTTPException(409, f"Bước '{row.name}': không được đổi loại stage khi còn công việc đang xử lý")
        diff["type"] = [row.type, s_in.type]
        row.type = s_in.type
    if s_in.sla_hours is not None:
        if s_in.sla_hours <= 0:
            raise HTTPException(400, "sla_hours phải > 0")
        if s_in.sla_hours != row.sla_hours:
            diff["sla_hours"] = [row.sla_hours, s_in.sla_hours]
            row.sla_hours = s_in.sla_hours
    if s_in.guide_html is not None:
        new_g = sanitize_html(s_in.guide_html)
        if new_g != (row.guide_html or ""):
            diff["guide_html"] = True
            row.guide_html = new_g
    if s_in.assign_mode is not None and s_in.assign_mode != row.assign_mode:
        if st_stat["doing_assignees"]:
            raise HTTPException(409, f"Bước '{row.name}': không đổi chế độ any/all khi còn việc đang xử lý")
        diff["assign_mode"] = [row.assign_mode, s_in.assign_mode]
        row.assign_mode = s_in.assign_mode
    if s_in.is_active is not None and s_in.is_active != row.is_active:
        if not s_in.is_active and st_stat["processing_count"] > 0:
            raise HTTPException(409, f"Bước '{row.name}' đang có công việc xử lý, không thể vô hiệu hóa")
        all_st = ordered_stages(db, wf.id, include_inactive=True)
        if not s_in.is_active and sum(1 for x in all_st if x.is_active and x.id != row.id) == 0:
            raise HTTPException(400, "Quy trình phải còn ít nhất 1 giai đoạn đang hoạt động")
        diff["is_active"] = [row.is_active, s_in.is_active]
        row.is_active = s_in.is_active
        active = [x for x in all_st if x.is_active]
        inactive = [x for x in all_st if not x.is_active]
        renumber_stages(db, wf.id, active, inactive)
    if s_in.member_ids is not None:
        new_members = set(s_in.member_ids)
        if wf.status == "active" and not new_members:
            raise HTTPException(400, f"Bước '{row.name}' cần ít nhất 1 thành viên")
        require_active_users(db, list(new_members), f"Thành viên bước '{row.name}'")
        old_members = set(db.scalars(select(StageMember.user_id).where(StageMember.stage_id == row.id)))
        if (old_members - new_members) & st_stat["doing_assignees"]:
            raise HTTPException(409, f"Bước '{row.name}': không thể gỡ thành viên đang có việc cần làm chưa xử lý")
        if new_members != old_members:
            diff["member_ids"] = [sorted(old_members), sorted(new_members)]
            for uid in new_members - old_members:
                db.add(StageMember(stage_id=row.id, user_id=uid))
            if old_members - new_members:
                db.execute(delete(StageMember).where(
                    StageMember.stage_id == row.id, StageMember.user_id.in_(old_members - new_members)
                ))
    db.flush()
    return row, {"stage_updated": {"id": row.id, "changes": diff}}


def delete_or_deactivate_stage(db, wf: Workflow, stage_id: int) -> dict:
    row = db.get(Stage, stage_id)
    if row is None or row.workflow_id != wf.id:
        raise HTTPException(404, "Không tìm thấy giai đoạn")
    all_st = ordered_stages(db, wf.id, include_inactive=True)
    active = [s for s in all_st if s.is_active]
    inactive = [s for s in all_st if not s.is_active]
    if row.is_active and len(active) <= 1:
        raise HTTPException(400, "Không thể xóa giai đoạn hoạt động cuối cùng của quy trình")
    stats = stage_usage_stats(db, wf.id)
    st_stat = stats.get(row.id, {"processing_count": 0, "history_count": 0})
    if st_stat["processing_count"] > 0:
        raise HTTPException(409, f"Bước '{row.name}' đang có công việc xử lý, không thể xóa hoặc vô hiệu hóa")

    name = row.name
    if st_stat["history_count"] > 0:
        row.is_active = False
        new_active = [s for s in active if s.id != row.id]
        new_inactive = [s for s in inactive if s.id != row.id] + [row]
        renumber_stages(db, wf.id, new_active, new_inactive)
        return {"action": "deactivated", "stage_id": stage_id, "name": name}

    new_active = [s for s in active if s.id != row.id]
    new_inactive = [s for s in inactive if s.id != row.id]
    db.delete(row)
    db.flush()
    renumber_stages(db, wf.id, new_active, new_inactive)
    return {"action": "deleted", "stage_id": stage_id, "name": name}


def reorder_active_stages(db, wf: Workflow, stage_ids: List[int]) -> dict:
    stats = stage_usage_stats(db, wf.id)
    if any(st["processing_count"] > 0 for st in stats.values()):
        raise HTTPException(409, "Không được đổi thứ tự giai đoạn khi đang có công việc xử lý")
    all_st = ordered_stages(db, wf.id, include_inactive=True)
    active_by_id = {s.id: s for s in all_st if s.is_active}
    inactive = [s for s in all_st if not s.is_active]
    if len(stage_ids) != len(active_by_id) or set(stage_ids) != set(active_by_id):
        raise HTTPException(400, "Danh sách stage_ids phải chứa đúng tất cả giai đoạn đang hoạt động")
    new_active = [active_by_id[sid] for sid in stage_ids]
    renumber_stages(db, wf.id, new_active, inactive)
    return {"stage_order": stage_ids}

