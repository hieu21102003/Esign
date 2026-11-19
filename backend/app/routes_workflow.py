"""API luồng ký tài liệu (Signing Workflow): tạo luồng, assign nhiều người (all/any), ký nối tiếp an toàn đồng thời."""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime
from typing import List, Literal, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import and_, func, or_, select

from .audit import audit
from .deps import Ctx, get_db, rate_limit, require_mfa
from .models import (
    DOC_STATUSES,
    Certificate,
    Document,
    DocumentVersion,
    SignJob,
    StepAssignee,
    StoredFile,
    User,
    UserCertificate,
    WorkflowStep,
    utcnow,
)
from .routes_files import SignParams, validate_sign_inputs
from .services import ServiceError, b64, inspect_pdf, signer_sign, signer_verify
from .storage import get_file, read_bytes, remove_from_disk, store_bytes

router = APIRouter(prefix="/api", tags=["workflow"])
_CTRL = re.compile(r"[\x00-\x1f\x7f]")

_LOCKS_GUARD = threading.Lock()
_DOC_LOCKS: dict[str, threading.Lock] = {}


def _doc_lock(doc_id: str) -> threading.Lock:
    """Khóa tuần tự hóa mọi thao tác ký/chuyển tiếp/hoàn tất trên cùng một tài liệu."""
    with _LOCKS_GUARD:
        lk = _DOC_LOCKS.get(doc_id)
        if lk is None:
            lk = threading.Lock()
            _DOC_LOCKS[doc_id] = lk
        return lk


class DocumentCreateIn(BaseModel):
    file_id: str
    title: str = Field(min_length=1, max_length=255)
    description: str = Field("", max_length=2000)
    assignee_ids: List[int] = Field(min_length=1, max_length=50)
    mode: Literal["all", "any"] = "all"

    @field_validator("title")
    @classmethod
    def _title(cls, v: str) -> str:
        s = _CTRL.sub(" ", v).strip()
        if not s:
            raise ValueError("Tiêu đề không được để trống")
        return s

    @field_validator("description")
    @classmethod
    def _desc(cls, v: str) -> str:
        return _CTRL.sub(" ", v).strip()


class AssignNextIn(BaseModel):
    assignee_ids: List[int] = Field(min_length=1, max_length=50)
    mode: Literal["all", "any"] = "all"


class DocumentSignIn(SignParams):
    next_action: Optional[Literal["assign", "complete"]] = None
    next_assignee_ids: Optional[List[int]] = Field(None, max_length=50)
    next_mode: Literal["all", "any"] = "all"

    @model_validator(mode="after")
    def _check_next(self) -> "DocumentSignIn":
        if self.next_action == "assign" and not self.next_assignee_ids:
            raise ValueError("Cần chọn ít nhất 1 người ký tiếp theo")
        if self.next_action != "assign" and self.next_assignee_ids:
            raise ValueError("Chỉ truyền next_assignee_ids khi next_action='assign'")
        return self


def _user_brief(u: Optional[User], fallback_id: Optional[int] = None) -> dict:
    if u is None:
        return {"id": fallback_id, "username": "", "display_name": ""}
    return {"id": u.id, "username": u.username, "display_name": u.display_name}


def _validate_uuid(doc_id: str) -> None:
    try:
        uuid.UUID(doc_id)
    except ValueError:
        raise HTTPException(404, "Không tìm thấy tài liệu")


def _validate_assignees(db, raw_ids: List[int]) -> List[int]:
    ids = list(dict.fromkeys(raw_ids))
    if not ids:
        raise HTTPException(400, "Cần chọn ít nhất 1 người ký")
    active = set(db.scalars(select(User.id).where(User.id.in_(ids), User.is_active.is_(True))))
    if active != set(ids):
        raise HTTPException(400, "Người được giao ký không hợp lệ hoặc đã bị vô hiệu hóa")
    return ids


def _can_view_document(db, doc: Document, user: User) -> bool:
    if user.role == "admin" or doc.creator_id == user.id:
        return True
    assigned = db.scalar(
        select(StepAssignee.id)
        .join(WorkflowStep, StepAssignee.step_id == WorkflowStep.id)
        .where(WorkflowStep.document_id == doc.id, StepAssignee.user_id == user.id)
        .limit(1)
    )
    return assigned is not None


def _get_doc_for_view(db, doc_id: str, user: User) -> Document:
    _validate_uuid(doc_id)
    doc = db.get(Document, doc_id)
    if doc is None or not _can_view_document(db, doc, user):
        raise HTTPException(404, "Không tìm thấy tài liệu")
    return doc


def _completing_signer_id(doc: Document, step: WorkflowStep, assignees: List[StepAssignee]) -> Optional[int]:
    if step.status != "completed":
        return None
    for a in assignees:
        if a.status == "signed" and a.signed_file_id == doc.current_file_id:
            return a.user_id
    signed = [a for a in assignees if a.status == "signed"]
    if not signed:
        return None
    return max(signed, key=lambda a: (a.signed_at or datetime.min, a.id)).user_id


def _load_doc_graph(db, doc: Document):
    steps = list(db.scalars(
        select(WorkflowStep).where(WorkflowStep.document_id == doc.id).order_by(WorkflowStep.step_order)
    ))
    step_ids = [s.id for s in steps]
    assignees_by_step: dict[int, List[StepAssignee]] = {sid: [] for sid in step_ids}
    if step_ids:
        for a in db.scalars(
            select(StepAssignee).where(StepAssignee.step_id.in_(step_ids)).order_by(StepAssignee.id)
        ):
            assignees_by_step[a.step_id].append(a)
    versions = list(db.scalars(
        select(DocumentVersion).where(DocumentVersion.document_id == doc.id).order_by(DocumentVersion.version_no)
    ))
    uids = {doc.creator_id}
    uids.update(s.assigned_by_id for s in steps)
    for lst in assignees_by_step.values():
        uids.update(a.user_id for a in lst)
    uids.update(v.created_by_id for v in versions)
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(uids)))} if uids else {}
    return steps, assignees_by_step, versions, users


def _summary_from_graph(doc: Document, steps, assignees_by_step, users, viewer: User) -> dict:
    cur_step = next((s for s in steps if s.step_order == doc.current_step_order), None)
    cur_assignees = assignees_by_step.get(cur_step.id, []) if cur_step else []
    my_pending = (
        doc.status == "in_progress"
        and cur_step is not None
        and cur_step.status == "active"
        and any(a.user_id == viewer.id and a.status == "pending" for a in cur_assignees)
    )
    awaiting_next = doc.status == "in_progress" and cur_step is not None and cur_step.status == "completed"
    completer_id = _completing_signer_id(doc, cur_step, cur_assignees) if awaiting_next else None
    return {
        "id": doc.id,
        "title": doc.title,
        "description": doc.description,
        "status": doc.status,
        "creator": _user_brief(users.get(doc.creator_id), doc.creator_id),
        "original_file_id": doc.original_file_id,
        "current_file_id": doc.current_file_id,
        "current_step_order": doc.current_step_order,
        "total_steps": len(steps),
        "my_pending": my_pending,
        "awaiting_next_step": awaiting_next,
        "can_assign_or_complete": bool(awaiting_next and completer_id == viewer.id),
        "created_at": doc.created_at.isoformat(),
        "updated_at": doc.updated_at.isoformat(),
        "completed_at": doc.completed_at.isoformat() if doc.completed_at else None,
        "rejected_at": doc.rejected_at.isoformat() if doc.rejected_at else None,
        "cancelled_at": doc.cancelled_at.isoformat() if doc.cancelled_at else None,
    }


def _document_summary(db, doc: Document, viewer: User) -> dict:
    steps, assignees_by_step, _versions, users = _load_doc_graph(db, doc)
    return _summary_from_graph(doc, steps, assignees_by_step, users, viewer)


def _document_detail(db, st, doc: Document, viewer: User) -> dict:
    steps, assignees_by_step, versions, users = _load_doc_graph(db, doc)
    base = _summary_from_graph(doc, steps, assignees_by_step, users, viewer)

    file_ids = {doc.current_file_id, *(v.file_id for v in versions)}
    files = {f.id: f for f in db.scalars(select(StoredFile).where(StoredFile.id.in_(file_ids)))} if file_ids else {}
    cur_file = files.get(doc.current_file_id)
    page_sizes = []
    if cur_file is not None and cur_file.deleted_at is None:
        try:
            page_sizes = inspect_pdf(read_bytes(st, cur_file), st.max_pages)["sizes"]
        except HTTPException:
            page_sizes = []

    cur_step = next((s for s in steps if s.step_order == doc.current_step_order), None)
    cur_assignees = assignees_by_step.get(cur_step.id, []) if cur_step else []
    can_sign = base["my_pending"]
    pending_count = sum(1 for a in cur_assignees if a.status == "pending")
    can_decide_next_on_sign = bool(
        can_sign and cur_step is not None and (cur_step.mode == "any" or pending_count == 1)
    )

    return {
        **base,
        "current_file": {
            "id": cur_file.id,
            "name": cur_file.original_name,
            "size": cur_file.size,
            "sha256": cur_file.sha256,
            "pages": cur_file.pages,
            "page_sizes": page_sizes,
        } if cur_file else None,
        "steps": [
            {
                "id": s.id,
                "step_order": s.step_order,
                "mode": s.mode,
                "status": s.status,
                "assigned_by": _user_brief(users.get(s.assigned_by_id), s.assigned_by_id),
                "created_at": s.created_at.isoformat(),
                "completed_at": s.completed_at.isoformat() if s.completed_at else None,
                "assignees": [
                    {
                        "id": a.id,
                        "user": _user_brief(users.get(a.user_id), a.user_id),
                        "status": a.status,
                        "signed_at": a.signed_at.isoformat() if a.signed_at else None,
                        "remark": a.remark,
                        "signed_file_id": a.signed_file_id,
                    }
                    for a in assignees_by_step.get(s.id, [])
                ],
            }
            for s in steps
        ],
        "versions": [
            {
                "version_no": v.version_no,
                "file_id": v.file_id,
                "step_id": v.step_id,
                "created_by": _user_brief(users.get(v.created_by_id), v.created_by_id),
                "note": v.note,
                "sha256": files[v.file_id].sha256 if v.file_id in files else "",
                "size": files[v.file_id].size if v.file_id in files else 0,
                "created_at": v.created_at.isoformat(),
            }
            for v in versions
        ],
        "can_sign": can_sign,
        "can_decide_next_on_sign": can_decide_next_on_sign,
        "can_reject": can_sign,
        "can_cancel": doc.status == "in_progress" and (doc.creator_id == viewer.id or viewer.role == "admin"),
    }


def _create_step(
    db, doc: Document, step_order: int, mode: str, assigned_by_id: int, assignee_ids: List[int], now: datetime
) -> WorkflowStep:
    step = WorkflowStep(
        document_id=doc.id, step_order=step_order, mode=mode,
        status="active", assigned_by_id=assigned_by_id, created_at=now,
    )
    db.add(step)
    db.flush()
    for uid in assignee_ids:
        db.add(StepAssignee(step_id=step.id, user_id=uid, status="pending", created_at=now))
    return step


def _pdf_response(f: StoredFile, data: bytes) -> Response:
    fname = f.original_name or "document.pdf"
    cd = f"attachment; filename=\"document.pdf\"; filename*=UTF-8''{quote(fname)}"
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": cd, "X-Content-Type-Options": "nosniff"})


@router.get("/users/assignable")
def list_assignable_users(ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    """Danh sách người dùng đang hoạt động để chọn khi assign luồng ký."""
    users = list(db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.display_name, User.username)))
    cert_user_ids = set(db.scalars(
        select(UserCertificate.user_id)
        .join(Certificate, Certificate.id == UserCertificate.cert_id)
        .where(Certificate.active.is_(True))
    ))
    return [
        {**_user_brief(u), "has_cert": u.id in cert_user_ids}
        for u in users
    ]


@router.post("/documents", status_code=201)
def create_document(body: DocumentCreateIn, request: Request, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    st = request.app.state.settings
    f = get_file(db, ctx, body.file_id, owner_only=True, kinds=("upload", "signed"))
    if f.is_workflow:
        raise HTTPException(409, "File này đã thuộc một luồng ký khác")
    assignee_ids = _validate_assignees(db, body.assignee_ids)

    now = utcnow()
    f.is_workflow = True
    doc = Document(
        id=str(uuid.uuid4()), title=body.title, description=body.description,
        status="in_progress", creator_id=ctx.user.id,
        original_file_id=f.id, current_file_id=f.id, current_step_order=1,
        created_at=now, updated_at=now,
    )
    db.add(doc)
    db.flush()
    step = _create_step(db, doc, 1, body.mode, ctx.user.id, assignee_ids, now)
    db.add(DocumentVersion(
        document_id=doc.id, version_no=0, file_id=f.id,
        step_id=None, created_by_id=ctx.user.id, note="Tài liệu gốc", created_at=now,
    ))
    audit(
        db, ctx.ip, "doc_create", user=ctx.user, document_id=doc.id, file_id=f.id, file_sha256=f.sha256,
        detail=f"title={doc.title}", metadata={"step_order": 1, "mode": body.mode, "assignee_ids": assignee_ids},
        commit=False,
    )
    audit(
        db, ctx.ip, "doc_assign", user=ctx.user, document_id=doc.id, file_id=f.id,
        metadata={"step_id": step.id, "step_order": 1, "mode": body.mode, "assignee_ids": assignee_ids},
        commit=False,
    )
    db.commit()
    return _document_detail(db, st, doc, ctx.user)


@router.get("/documents")
def list_documents(
    scope: Literal["all", "pending", "created", "participated"] = "all",
    status: str = "",
    ctx: Ctx = Depends(require_mfa),
    db=Depends(get_db),
):
    if status and status not in DOC_STATUSES:
        raise HTTPException(400, "Trạng thái không hợp lệ")
    assigned_doc_ids = (
        select(WorkflowStep.document_id)
        .join(StepAssignee, StepAssignee.step_id == WorkflowStep.id)
        .where(StepAssignee.user_id == ctx.user.id)
    )
    q = select(Document)
    if status:
        q = q.where(Document.status == status)
    if scope == "created":
        q = q.where(Document.creator_id == ctx.user.id)
    elif scope == "participated":
        q = q.where(Document.id.in_(assigned_doc_ids))
    elif scope == "pending":
        pending_ids = (
            select(Document.id)
            .join(WorkflowStep, and_(
                WorkflowStep.document_id == Document.id,
                WorkflowStep.step_order == Document.current_step_order,
            ))
            .join(StepAssignee, StepAssignee.step_id == WorkflowStep.id)
            .where(
                Document.status == "in_progress",
                WorkflowStep.status == "active",
                StepAssignee.user_id == ctx.user.id,
                StepAssignee.status == "pending",
            )
        )
        q = q.where(Document.id.in_(pending_ids))
    elif ctx.user.role != "admin":
        q = q.where(or_(Document.creator_id == ctx.user.id, Document.id.in_(assigned_doc_ids)))

    docs = list(db.scalars(q.order_by(Document.updated_at.desc(), Document.created_at.desc())))
    return [_document_summary(db, d, ctx.user) for d in docs]


@router.get("/documents/{doc_id}")
def get_document(doc_id: str, request: Request, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    doc = _get_doc_for_view(db, doc_id, ctx.user)
    return _document_detail(db, request.app.state.settings, doc, ctx.user)


def _resolve_doc_file(db, doc: Document, version_no: Optional[int] = None) -> StoredFile:
    if version_no is None:
        fid = doc.current_file_id
    else:
        ver = db.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == doc.id,
                DocumentVersion.version_no == version_no,
            )
        )
        if ver is None:
            raise HTTPException(404, "Không tìm thấy phiên bản tài liệu")
        fid = ver.file_id
    f = db.get(StoredFile, fid)
    if f is None or f.deleted_at is not None:
        raise HTTPException(404, "File không tồn tại hoặc đã bị xóa")
    return f


@router.get("/documents/{doc_id}/file")
def download_document_file(doc_id: str, request: Request, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    doc = _get_doc_for_view(db, doc_id, ctx.user)
    f = _resolve_doc_file(db, doc)
    data = read_bytes(request.app.state.settings, f)
    audit(db, ctx.ip, "download", user=ctx.user, document_id=doc.id, file_id=f.id, file_sha256=f.sha256)
    return _pdf_response(f, data)


@router.get("/documents/{doc_id}/versions/{version_no}/file")
def download_document_version(
    doc_id: str, version_no: int, request: Request, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)
):
    doc = _get_doc_for_view(db, doc_id, ctx.user)
    f = _resolve_doc_file(db, doc, version_no=version_no)
    data = read_bytes(request.app.state.settings, f)
    audit(
        db, ctx.ip, "download", user=ctx.user, document_id=doc.id,
        file_id=f.id, file_sha256=f.sha256, metadata={"version_no": version_no},
    )
    return _pdf_response(f, data)


@router.post("/documents/{doc_id}/verify")
def verify_document(
    doc_id: str, request: Request, version_no: Optional[int] = None,
    ctx: Ctx = Depends(require_mfa), db=Depends(get_db),
):
    st = request.app.state.settings
    rate_limit(request, "verify", str(ctx.user.id), st.verify_rate)
    doc = _get_doc_for_view(db, doc_id, ctx.user)
    f = _resolve_doc_file(db, doc, version_no=version_no)
    sigs = signer_verify(st, read_bytes(st, f))
    audit(
        db, ctx.ip, "verify", user=ctx.user, document_id=doc.id, file_id=f.id, file_sha256=f.sha256,
        metadata={"version_no": version_no} if version_no is not None else None,
        detail=f"signatures={len(sigs)} all_ok={bool(sigs) and all(s.get('ok') for s in sigs)}",
    )
    return {
        "document_id": doc.id,
        "file_id": f.id,
        "signatures": sigs,
        "all_ok": bool(sigs) and all(s.get("ok") for s in sigs),
    }


def _load_locked_doc_and_step(db, doc_id: str, uid: int):
    db.rollback()
    actor = db.get(User, uid)
    doc = db.scalar(select(Document).where(Document.id == doc_id).with_for_update())
    if actor is None or doc is None or not _can_view_document(db, doc, actor):
        raise HTTPException(404, "Không tìm thấy tài liệu")
    if doc.status != "in_progress":
        raise HTTPException(409, "Tài liệu không ở trạng thái đang ký")
    step = db.scalar(
        select(WorkflowStep).where(WorkflowStep.document_id == doc.id, WorkflowStep.step_order == doc.current_step_order)
    )
    if step is None:
        raise HTTPException(409, "Không tìm thấy bước ký hiện tại")
    assignees = list(db.scalars(
        select(StepAssignee).where(StepAssignee.step_id == step.id).order_by(StepAssignee.id)
    ))
    return actor, doc, step, assignees


@router.post("/documents/{doc_id}/sign")
def sign_document(
    doc_id: str, body: DocumentSignIn, request: Request, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)
):
    """Ký PDF nối tiếp trong luồng ký, dùng khóa theo document_id để tuần tự hóa ký song song."""
    st = request.app.state.settings
    rate_limit(request, "sign", str(ctx.user.id), st.sign_rate)
    _validate_uuid(doc_id)
    uid, ip = ctx.user.id, ctx.ip

    with _doc_lock(doc_id):
        actor, doc, step, assignees = _load_locked_doc_and_step(db, doc_id, uid)
        if step.status != "active":
            raise HTTPException(409, "Bước hiện tại đã hoàn tất, không thể ký thêm")
        me = next((a for a in assignees if a.user_id == uid), None)
        if me is None:
            raise HTTPException(403, "Bạn không được giao ký ở bước hiện tại")
        if me.status == "signed":
            raise HTTPException(409, "Bạn đã ký ở bước này rồi")
        if me.status != "pending":
            raise HTTPException(409, "Lượt ký của bạn không còn hiệu lực")

        other_pending = [a for a in assignees if a.id != me.id and a.status == "pending"]
        will_complete_step = (step.mode == "any") or (len(other_pending) == 0)
        if body.next_action is not None and not will_complete_step:
            raise HTTPException(
                409, "Vẫn còn người ký khác chưa hoàn tất bước này; hãy ký trước, người ký cuối sẽ chuyển tiếp hoặc hoàn tất"
            )
        next_uids = _validate_assignees(db, body.next_assignee_ids) if body.next_action == "assign" else []

        cur_file = _resolve_doc_file(db, doc)
        actor_ctx = Ctx(user=actor, sess=ctx.sess, ip=ip)
        cert, pdf, info, img = validate_sign_inputs(db, st, actor_ctx, body, cur_file, document_id=doc.id)

        params = body.model_dump(exclude={"cert_id", "next_action", "next_assignee_ids", "next_mode"})
        payload = {
            "key_ref": cert.key_ref, "pdf_b64": b64(pdf), "image_b64": b64(img),
            "mode": body.mode, "page": body.page, "box": body.box,
            "reason": body.reason, "location": body.location, "title": body.title,
            "show": body.show, "level": body.level,
        }
        try:
            signed = signer_sign(st, payload)
        except ValueError as exc:
            db.add(SignJob(
                user_id=uid, input_file_id=cur_file.id, cert_id=cert.id,
                params_json=json.dumps(params), status="failed", error=str(exc)[:400], finished_at=utcnow(),
            ))
            audit(
                db, ip, "sign_failed", user=actor, status="error", document_id=doc.id,
                file_id=cur_file.id, file_sha256=cur_file.sha256, cert_key_ref=cert.key_ref,
                detail=str(exc)[:300], commit=False,
            )
            db.commit()
            raise HTTPException(400, str(exc))
        except ServiceError as exc:
            db.add(SignJob(
                user_id=uid, input_file_id=cur_file.id, cert_id=cert.id,
                params_json=json.dumps(params), status="failed", error=str(exc)[:400], finished_at=utcnow(),
            ))
            audit(
                db, ip, "sign_failed", user=actor, status="error", document_id=doc.id,
                file_id=cur_file.id, file_sha256=cur_file.sha256, cert_key_ref=cert.key_ref,
                detail=str(exc)[:300], commit=False,
            )
            db.commit()
            raise HTTPException(502, "Dịch vụ ký không khả dụng")

        now = utcnow()
        next_ver = (db.scalar(select(func.max(DocumentVersion.version_no)).where(DocumentVersion.document_id == doc.id)) or 0) + 1
        stem = cur_file.original_name[:-4] if cur_file.original_name.lower().endswith(".pdf") else cur_file.original_name
        stem = re.sub(r"-v\d+$", "", re.sub(r"-signed(-\d+)?$", "", stem))

        out = store_bytes(
            db, st, actor, signed, kind="signed", name=f"{stem}-v{next_ver}.pdf",
            pages=info["pages"], parent_id=cur_file.id, is_workflow=True, commit=False,
        )
        try:
            job = SignJob(
                user_id=uid, input_file_id=cur_file.id, output_file_id=out.id, cert_id=cert.id,
                params_json=json.dumps(params), status="done", created_at=now, finished_at=now,
            )
            db.add(job)
            db.flush()

            db.add(DocumentVersion(
                document_id=doc.id, version_no=next_ver, file_id=out.id, step_id=step.id,
                created_by_id=uid, note=body.reason, created_at=now,
            ))
            me.status = "signed"
            me.signed_at = now
            me.remark = body.reason
            me.signed_file_id = out.id
            me.sign_job_id = job.id
            doc.current_file_id = out.id
            doc.updated_at = now

            audit(
                db, ip, "doc_sign", user=actor, document_id=doc.id, file_id=out.id,
                file_sha256=cur_file.sha256, output_sha256=out.sha256, cert_key_ref=cert.key_ref,
                detail=f"step={step.step_order} ver={next_ver}",
                metadata={"step_id": step.id, "step_order": step.step_order, "version_no": next_ver,
                          "remark": body.reason, "mode": body.mode, "level": body.level},
                commit=False,
            )

            if will_complete_step:
                if step.mode == "any":
                    for other in other_pending:
                        other.status = "skipped"
                step.status = "completed"
                step.completed_at = now

                if body.next_action == "assign":
                    next_step = _create_step(db, doc, step.step_order + 1, body.next_mode, uid, next_uids, now)
                    doc.current_step_order = next_step.step_order
                    audit(
                        db, ip, "doc_assign", user=actor, document_id=doc.id, file_id=out.id,
                        metadata={"step_id": next_step.id, "step_order": next_step.step_order,
                                  "mode": body.next_mode, "assignee_ids": next_uids},
                        commit=False,
                    )
                elif body.next_action == "complete":
                    doc.status = "completed"
                    doc.completed_at = now
                    audit(
                        db, ip, "doc_complete", user=actor, document_id=doc.id,
                        file_id=out.id, output_sha256=out.sha256,
                        metadata={"final_version_no": next_ver}, commit=False,
                    )

            db.commit()
        except Exception:
            db.rollback()
            remove_from_disk(st, out.id)
            raise

        return {
            "job_id": job.id,
            "version_no": next_ver,
            "step_completed": will_complete_step,
            "document": _document_detail(db, st, doc, actor),
        }


@router.post("/documents/{doc_id}/assign")
def assign_next_step(
    doc_id: str, body: AssignNextIn, request: Request, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)
):
    st = request.app.state.settings
    _validate_uuid(doc_id)
    uid, ip = ctx.user.id, ctx.ip

    with _doc_lock(doc_id):
        actor, doc, step, assignees = _load_locked_doc_and_step(db, doc_id, uid)
        if step.status != "completed":
            raise HTTPException(409, "Bước hiện tại chưa hoàn tất")
        if _completing_signer_id(doc, step, assignees) != uid:
            raise HTTPException(403, "Chỉ người ký hoàn tất bước hiện tại mới có thể chuyển tiếp")
        next_uids = _validate_assignees(db, body.assignee_ids)

        now = utcnow()
        next_step = _create_step(db, doc, step.step_order + 1, body.mode, uid, next_uids, now)
        doc.current_step_order = next_step.step_order
        doc.updated_at = now
        audit(
            db, ip, "doc_assign", user=actor, document_id=doc.id, file_id=doc.current_file_id,
            metadata={"step_id": next_step.id, "step_order": next_step.step_order,
                      "mode": body.mode, "assignee_ids": next_uids},
            commit=False,
        )
        db.commit()
        return _document_detail(db, st, doc, actor)


@router.post("/documents/{doc_id}/complete")
def complete_document(doc_id: str, request: Request, ctx: Ctx = Depends(require_mfa), db=Depends(get_db)):
    st = request.app.state.settings
    _validate_uuid(doc_id)
    uid, ip = ctx.user.id, ctx.ip

    with _doc_lock(doc_id):
        actor, doc, step, assignees = _load_locked_doc_and_step(db, doc_id, uid)
        if step.status != "completed":
            raise HTTPException(409, "Bước hiện tại chưa hoàn tất")
        if _completing_signer_id(doc, step, assignees) != uid:
            raise HTTPException(403, "Chỉ người ký hoàn tất bước hiện tại mới có thể hoàn tất tài liệu")

        now = utcnow()
        cur_file = db.get(StoredFile, doc.current_file_id)
        doc.status = "completed"
        doc.completed_at = now
        doc.updated_at = now
        audit(
            db, ip, "doc_complete", user=actor, document_id=doc.id,
            file_id=doc.current_file_id, output_sha256=cur_file.sha256 if cur_file else None,
            commit=False,
        )
        db.commit()
        return _document_detail(db, st, doc, actor)
