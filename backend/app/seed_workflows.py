"""Script seed 2 quy trình mẫu cho W5 (an toàn, chạy nhiều lần không trùng lặp theo slug).

Chạy trong container api:
    python -m app.seed_workflows
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import select

from .audit import audit
from .config import Settings
from .models import (
    FormField,
    Stage,
    StageMember,
    User,
    Workflow,
    WorkflowFailureReason,
    WorkflowRole,
    init_db,
    make_engine,
    make_session_factory,
    utcnow,
)

SAMPLE_WORKFLOWS: Tuple[dict, ...] = (
    {
        "slug": "qt-cong-van-den",
        "name": "QT công văn đến",
        "category": "Hành chính",
        "description": "Quy trình tiếp nhận, xin ý kiến, xử lý, ký duyệt và phát hành công văn đến.",
        "guide_html": "<p>Tiếp nhận công văn đến, chuyển Giám đốc cho ý kiến, giao bộ môn thực hiện, ký duyệt và phát hành.</p>",
        "visibility": "followers_only",
        "stages": [
            {"name": "Văn thư tiếp nhận", "type": "normal", "sla_hours": 8, "assign_mode": "any"},
            {"name": "Giám đốc cho ý kiến", "type": "approve", "sla_hours": 2, "assign_mode": "any"},
            {"name": "Bộ môn thực hiện", "type": "normal", "sla_hours": 24, "assign_mode": "all"},
            {"name": "Giám đốc ký duyệt", "type": "sign", "sla_hours": 1, "assign_mode": "any"},
            {"name": "Văn thư phát hành", "type": "normal", "sla_hours": 1, "assign_mode": "any"},
        ],
        "form_fields": [
            {"label": "Tên công văn", "type": "text", "required": True, "placeholder": "Nhập tên hoặc số hiệu công văn"},
            {"label": "Đơn vị gửi", "type": "text", "required": True, "placeholder": "Tên cơ quan / đơn vị ban hành"},
            {"label": "Trích yếu", "type": "textarea", "required": True, "placeholder": "Tóm tắt nội dung công văn"},
            {"label": "Thời hạn trả lời", "type": "date", "required": True, "placeholder": "YYYY-MM-DD"},
            {"label": "Scan văn bản", "type": "file", "required": True, "placeholder": ""},
        ],
        "failure_reasons": [
            "Sai thẩm quyền / chuyển nhầm đơn vị",
            "Hồ sơ chưa đầy đủ / không hợp lệ",
            "Lãnh đạo không phê duyệt",
        ],
    },
    {
        "slug": "qt-cham-cong",
        "name": "QT Chấm công",
        "category": "Nhân sự",
        "description": "Quy trình tổng hợp, đối chiếu và phê duyệt bảng chấm công định kỳ.",
        "guide_html": "<p>Tổng hợp chấm công hàng tháng, đối chiếu bộ phận và chốt bảng công.</p>",
        "visibility": "all_visible",
        "stages": [
            {"name": "Tổng hợp bảng chấm công", "type": "normal", "sla_hours": 1, "assign_mode": "any"},
            {"name": "Trưởng bộ phận đối chiếu", "type": "approve", "sla_hours": 3, "assign_mode": "all"},
            {"name": "Phê duyệt & chốt công", "type": "approve", "sla_hours": 4, "assign_mode": "any"},
        ],
        "form_fields": [],
        "failure_reasons": [
            "Sai số liệu chấm công",
            "Thiếu xác nhận của bộ phận",
        ],
    },
)


def seed_sample_workflows(
    db,
    creator: User,
    *,
    member_ids_by_slug: Optional[Dict[str, List[List[int]]]] = None,
    extra_roles: Optional[Iterable[Tuple[int, str]]] = None,
    commit: bool = True,
) -> dict:
    """Tạo 2 quy trình mẫu (idempotent theo slug). Trả về danh sách created, skipped và id quy trình."""
    created: List[str] = []
    skipped: List[str] = []
    workflows: Dict[str, int] = {}
    role_pairs = {(creator.id, "admin")} | {(int(uid), str(r)) for uid, r in (extra_roles or ())}

    for spec in SAMPLE_WORKFLOWS:
        slug = spec["slug"]
        existing = db.scalar(select(Workflow).where(Workflow.slug == slug))
        if existing is not None:
            skipped.append(slug)
            workflows[slug] = existing.id
            continue

        wf = Workflow(
            name=spec["name"],
            slug=slug,
            category=spec["category"],
            description=spec["description"],
            guide_html=spec["guide_html"],
            visibility=spec["visibility"],
            status="active",
            creator_id=creator.id,
            created_at=utcnow(),
        )
        db.add(wf)
        db.flush()

        custom_members = (member_ids_by_slug or {}).get(slug)
        for idx, s in enumerate(spec["stages"], start=1):
            st_row = Stage(
                workflow_id=wf.id,
                order=idx,
                name=s["name"],
                type=s["type"],
                sla_hours=s["sla_hours"],
                guide_html="",
                assign_mode=s["assign_mode"],
            )
            db.add(st_row)
            db.flush()
            mids = (
                custom_members[idx - 1]
                if custom_members is not None and idx - 1 < len(custom_members) and custom_members[idx - 1]
                else [creator.id]
            )
            for uid in sorted(set(mids)):
                db.add(StageMember(stage_id=st_row.id, user_id=uid))

        for idx, f in enumerate(spec["form_fields"], start=1):
            db.add(FormField(
                workflow_id=wf.id,
                label=f["label"],
                type=f["type"],
                required=f["required"],
                placeholder=f["placeholder"],
                options=f.get("options"),
                order=idx,
            ))

        for uid, role in sorted(role_pairs):
            db.add(WorkflowRole(workflow_id=wf.id, user_id=uid, role=role))

        for idx, label in enumerate(spec["failure_reasons"], start=1):
            db.add(WorkflowFailureReason(workflow_id=wf.id, label=label, order=idx))

        audit(
            db, "seed", "workflow_created", user=creator, workflow_id=wf.id, detail=wf.name, commit=False,
            metadata={"slug": wf.slug, "stages": len(spec["stages"]), "source": "seed_workflows"},
        )
        created.append(slug)
        workflows[slug] = wf.id

    if commit:
        db.commit()
    return {"created": created, "skipped": skipped, "workflows": workflows}


def main() -> int:
    st = Settings.from_env()
    engine = make_engine(st.db_url)
    init_db(engine)
    sf = make_session_factory(engine)
    db = sf()
    try:
        creator = db.scalar(
            select(User).where(User.role == "admin", User.is_active.is_(True)).order_by(User.id).limit(1)
        )
        if creator is None:
            creator = db.scalar(select(User).where(User.is_active.is_(True)).order_by(User.id).limit(1))
        if creator is None:
            raise RuntimeError("Chưa có tài khoản người dùng nào trong DB để gán làm người tạo quy trình")
        res = seed_sample_workflows(db, creator, commit=True)
        print("Seed workflows hoàn tất:", res)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
