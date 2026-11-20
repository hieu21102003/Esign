"""Template quy trình nội bộ (W8): schema validation & seed 5 mẫu cho công ty tư vấn giao thông."""
from __future__ import annotations

from typing import Any, Dict, Tuple

from fastapi import HTTPException
from sqlalchemy import select

from .html_sanitize import sanitize_html
from .models import WorkflowTemplate, utcnow

VALID_STAGE_TYPES = {"normal", "sign", "approve"}
VALID_ASSIGN_MODES = {"any", "all"}
VALID_FIELD_TYPES = {"text", "textarea", "date", "file", "number", "select", "user"}
VALID_VISIBILITIES = {"followers_only", "all_visible"}

BUILTIN_TEMPLATES: Tuple[dict, ...] = (
    {
        "slug": "tpl-cong-van-den",
        "name": "Công văn đến",
        "category": "Hành chính - Văn thư",
        "description": "Tiếp nhận công văn đến, xin ý kiến lãnh đạo (duyệt), bộ môn xử lý, ký số ban hành và lưu trữ.",
        "definition": {
            "visibility": "followers_only",
            "guide_html": (
                "<p><strong>Quy trình Công văn đến</strong> dành cho văn bản hành chính gửi tới công ty:</p>"
                "<ul><li>Bước 2 (<em>approve</em>): Lãnh đạo cho ý kiến phân công.</li>"
                "<li>Bước 4 (<em>sign</em>): Giám đốc ký số văn bản phúc đáp/duyệt.</li></ul>"
            ),
            "stages": [
                {"name": "Văn thư tiếp nhận", "type": "normal", "sla_hours": 8, "assign_mode": "any",
                 "guide_html": "<p>Kiểm tra thể thức, scan đính kèm và phân loại công văn.</p>"},
                {"name": "Lãnh đạo cho ý kiến", "type": "approve", "sla_hours": 4, "assign_mode": "any",
                 "guide_html": "<p>Loại bước: <strong>Phê duyệt (approve)</strong> — chỉ đạo bộ môn xử lý.</p>"},
                {"name": "Bộ môn chuyên môn xử lý", "type": "normal", "sla_hours": 24, "assign_mode": "all",
                 "guide_html": "<p>Bộ môn nghiên cứu, soạn thảo dự thảo trả lời.</p>"},
                {"name": "Giám đốc ký duyệt", "type": "sign", "sla_hours": 4, "assign_mode": "any",
                 "guide_html": "<p>Loại bước: <strong>Ký số (sign)</strong> — ký số bằng chứng thư nội bộ + PIN.</p>"},
                {"name": "Văn thư phát hành & lưu trữ", "type": "normal", "sla_hours": 2, "assign_mode": "any",
                 "guide_html": "<p>Vào sổ phát hành và lưu hồ sơ.</p>"},
            ],
            "form_fields": [
                {"label": "Tên / Số hiệu công văn", "type": "text", "required": True, "placeholder": "VD: 102/CV-BGTVT"},
                {"label": "Cơ quan ban hành", "type": "text", "required": True, "placeholder": "Tên đơn vị gửi"},
                {"label": "Trích yếu nội dung", "type": "textarea", "required": True, "placeholder": "Tóm tắt nội dung chính"},
                {"label": "Thời hạn trả lời", "type": "date", "required": True, "placeholder": "YYYY-MM-DD"},
                {"label": "Scan văn bản gốc", "type": "file", "required": True, "placeholder": ""},
            ],
            "failure_reasons": [
                "Sai thẩm quyền / chuyển nhầm bộ môn",
                "Hồ sơ kèm theo chưa đầy đủ",
                "Lãnh đạo không phê duyệt",
            ],
        },
    },
    {
        "slug": "tpl-cong-van-di",
        "name": "Công văn đi",
        "category": "Hành chính - Văn thư",
        "description": "Soạn thảo văn bản gửi đối tác/chủ đầu tư, trưởng bộ môn duyệt, giám đốc ký số và văn thư phát hành.",
        "definition": {
            "visibility": "followers_only",
            "guide_html": (
                "<p><strong>Quy trình Công văn đi</strong> áp dụng cho văn bản phát hành ra ngoài:</p>"
                "<ul><li>Bước 2 (<em>approve</em>): Trưởng bộ môn kiểm tra kỹ thuật/nội dung.</li>"
                "<li>Bước 3 (<em>sign</em>): Giám đốc ký số phát hành.</li></ul>"
            ),
            "stages": [
                {"name": "Chuyên viên soạn thảo", "type": "normal", "sla_hours": 12, "assign_mode": "any",
                 "guide_html": "<p>Soạn công văn theo mẫu chuẩn và đính kèm bản PDF/Word.</p>"},
                {"name": "Trưởng bộ môn kiểm tra", "type": "approve", "sla_hours": 8, "assign_mode": "any",
                 "guide_html": "<p>Loại bước: <strong>Phê duyệt (approve)</strong> — rà soát nội dung chuyên môn.</p>"},
                {"name": "Giám đốc ký số ban hành", "type": "sign", "sla_hours": 4, "assign_mode": "any",
                 "guide_html": "<p>Loại bước: <strong>Ký số (sign)</strong> — ký số chính thức trước khi cấp số.</p>"},
                {"name": "Văn thư đóng số & phát hành", "type": "normal", "sla_hours": 2, "assign_mode": "any",
                 "guide_html": "<p>Cấp số công văn đi và gửi đơn vị nhận.</p>"},
            ],
            "form_fields": [
                {"label": "Tên công văn đi", "type": "text", "required": True, "placeholder": "Nhập tiêu đề văn bản"},
                {"label": "Nơi nhận", "type": "text", "required": True, "placeholder": "Ban QLDA / Sở GTVT / Đối tác"},
                {"label": "Mức độ khẩn", "type": "select", "required": True, "placeholder": "Chọn mức độ",
                 "options": ["Thường", "Khẩn", "Hỏa tốc"]},
                {"label": "Hạn phát hành", "type": "date", "required": False, "placeholder": "YYYY-MM-DD"},
                {"label": "Bản thảo công văn", "type": "file", "required": True, "placeholder": ""},
            ],
            "failure_reasons": [
                "Nội dung / thể thức chưa đạt",
                "Chưa được trưởng bộ môn thông qua",
                "Lãnh đạo từ chối ký ban hành",
            ],
        },
    },
    {
        "slug": "tpl-cham-cong",
        "name": "Chấm công",
        "category": "Nhân sự",
        "description": "Tổng hợp bảng chấm công tháng, trưởng bộ phận đối chiếu và lãnh đạo phê duyệt chốt công.",
        "definition": {
            "visibility": "all_visible",
            "guide_html": (
                "<p><strong>Quy trình Chấm công</strong> định kỳ hàng tháng:</p>"
                "<ul><li>Bước 2 (<em>approve</em>): Trưởng bộ phận xác nhận công thực tế.</li>"
                "<li>Bước 3 (<em>approve</em>): Giám đốc duyệt chốt công tính lương.</li></ul>"
            ),
            "stages": [
                {"name": "Nhân sự tổng hợp bảng công", "type": "normal", "sla_hours": 4, "assign_mode": "any",
                 "guide_html": "<p>Tổng hợp dữ liệu máy chấm công và đơn nghỉ phép.</p>"},
                {"name": "Trưởng bộ phận đối chiếu", "type": "approve", "sla_hours": 8, "assign_mode": "all",
                 "guide_html": "<p>Loại bước: <strong>Phê duyệt (approve)</strong> — kiểm tra ngày công bộ phận.</p>"},
                {"name": "Giám đốc phê duyệt chốt công", "type": "approve", "sla_hours": 4, "assign_mode": "any",
                 "guide_html": "<p>Loại bước: <strong>Phê duyệt (approve)</strong> — phê duyệt kỳ công.</p>"},
            ],
            "form_fields": [
                {"label": "Kỳ chấm công (tháng/năm)", "type": "text", "required": True, "placeholder": "VD: 11/2026"},
                {"label": "Khối / Phòng ban", "type": "text", "required": True, "placeholder": "VD: Phòng Thiết kế Cầu Đường"},
                {"label": "Ghi chú biến động", "type": "textarea", "required": False, "placeholder": "Nghỉ phép, công tác hiện trường..."},
            ],
            "failure_reasons": [
                "Sai lệch ngày công / tăng ca",
                "Thiếu đơn nghỉ phép đối chiếu",
            ],
        },
    },
    {
        "slug": "tpl-trinh-ky-hop-dong-ho-so",
        "name": "Trình ký hợp đồng / hồ sơ",
        "category": "Dự án - Tư vấn",
        "description": "Luồng thẩm định kỹ thuật, rà soát tài chính và ký số hợp đồng tư vấn hoặc hồ sơ thiết kế giao thông.",
        "definition": {
            "visibility": "followers_only",
            "guide_html": (
                "<p><strong>Quy trình Trình ký hợp đồng / hồ sơ thiết kế</strong>:</p>"
                "<ul><li>Bước 2 (<em>approve</em>): Phòng KH-KT thẩm định hồ sơ.</li>"
                "<li>Bước 3 (<em>approve</em>): Kế toán trưởng kiểm tra giá trị/điều khoản.</li>"
                "<li>Bước 4 (<em>sign</em>): Giám đốc ký số chính thức trên PDF.</li></ul>"
            ),
            "stages": [
                {"name": "Chủ nhiệm đồ án lập trình ký", "type": "normal", "sla_hours": 12, "assign_mode": "any",
                 "guide_html": "<p>Kiểm tra đủ phụ lục, thuyết minh và tệp PDF hợp đồng/hồ sơ.</p>"},
                {"name": "Phòng KH-KT thẩm định", "type": "approve", "sla_hours": 24, "assign_mode": "all",
                 "guide_html": "<p>Loại bước: <strong>Phê duyệt (approve)</strong> — thẩm định tiêu chuẩn thiết kế.</p>"},
                {"name": "Kế toán rà soát tài chính", "type": "approve", "sla_hours": 12, "assign_mode": "any",
                 "guide_html": "<p>Loại bước: <strong>Phê duyệt (approve)</strong> — kiểm tra giá trị và tiến độ thanh toán.</p>"},
                {"name": "Giám đốc ký số hợp đồng / hồ sơ", "type": "sign", "sla_hours": 8, "assign_mode": "any",
                 "guide_html": "<p>Loại bước: <strong>Ký số (sign)</strong> — ký số PDF hợp đồng/hồ sơ.</p>"},
                {"name": "Văn thư lưu trữ & bàn giao", "type": "normal", "sla_hours": 4, "assign_mode": "any",
                 "guide_html": "<p>Đóng dấu lưu trữ và bàn giao chủ đầu tư.</p>"},
            ],
            "form_fields": [
                {"label": "Tên dự án / gói thầu", "type": "text", "required": True, "placeholder": "VD: Tư vấn TKBVTC Cao tốc đoạn A-B"},
                {"label": "Chủ đầu tư / Đối tác", "type": "text", "required": True, "placeholder": "Tên chủ đầu tư"},
                {"label": "Loại hồ sơ", "type": "select", "required": True, "placeholder": "Chọn loại",
                 "options": ["Hợp đồng tư vấn", "Hồ sơ TKBVTC", "Báo cáo khảo sát", "Phụ lục điều chỉnh"]},
                {"label": "Giá trị hợp đồng (triệu VND)", "type": "number", "required": False, "placeholder": "VD: 1250"},
                {"label": "Người phụ trách kỹ thuật", "type": "user", "required": False, "placeholder": ""},
                {"label": "File hồ sơ / hợp đồng trình ký", "type": "file", "required": True, "placeholder": ""},
            ],
            "failure_reasons": [
                "Hồ sơ kỹ thuật chưa đạt yêu cầu",
                "Điều khoản tài chính / pháp lý chưa phù hợp",
                "Ban Giám đốc không phê duyệt",
            ],
        },
    },
    {
        "slug": "tpl-phe-duyet-thanh-toan",
        "name": "Phê duyệt thanh toán",
        "category": "Tài chính - Kế toán",
        "description": "Kiểm soát chứng từ tạm ứng/thanh toán khối lượng khảo sát, thiết kế và ký số lệnh duyệt chi.",
        "definition": {
            "visibility": "followers_only",
            "guide_html": (
                "<p><strong>Quy trình Phê duyệt thanh toán</strong>:</p>"
                "<ul><li>Bước 2 (<em>approve</em>): Kế toán kiểm tra hóa đơn/chứng từ.</li>"
                "<li>Bước 3 (<em>sign</em>): Giám đốc ký số phiếu đề nghị/lệnh chi.</li></ul>"
            ),
            "stages": [
                {"name": "Lập đề nghị & tập hợp chứng từ", "type": "normal", "sla_hours": 8, "assign_mode": "any",
                 "guide_html": "<p>Đính kèm bảng kê, hóa đơn GTGT và biên bản nghiệm thu.</p>"},
                {"name": "Kế toán kiểm tra chứng từ", "type": "approve", "sla_hours": 12, "assign_mode": "any",
                 "guide_html": "<p>Loại bước: <strong>Phê duyệt (approve)</strong> — đối chiếu hợp lệ chứng từ.</p>"},
                {"name": "Giám đốc ký số lệnh chi", "type": "sign", "sla_hours": 6, "assign_mode": "any",
                 "guide_html": "<p>Loại bước: <strong>Ký số (sign)</strong> — ký duyệt chi trên chứng từ PDF.</p>"},
                {"name": "Kế toán / Thủ quỹ giải ngân", "type": "normal", "sla_hours": 8, "assign_mode": "any",
                 "guide_html": "<p>Thực hiện chuyển khoản và hạch toán chứng từ.</p>"},
            ],
            "form_fields": [
                {"label": "Nội dung thanh toán", "type": "text", "required": True, "placeholder": "VD: Tạm ứng đợt 1 khảo sát địa chất"},
                {"label": "Số tiền đề nghị (VND)", "type": "number", "required": True, "placeholder": "VD: 50000000"},
                {"label": "Dự án / Hợp đồng liên quan", "type": "text", "required": True, "placeholder": "Mã/tên dự án"},
                {"label": "Hạn thanh toán", "type": "date", "required": True, "placeholder": "YYYY-MM-DD"},
                {"label": "Bộ chứng từ / Hóa đơn", "type": "file", "required": True, "placeholder": ""},
            ],
            "failure_reasons": [
                "Chứng từ / hóa đơn không hợp lệ",
                "Vượt dự toán được duyệt",
                "Giám đốc từ chối duyệt chi",
            ],
        },
    },
)


def validate_template_definition(defn: Any) -> dict:
    """Kiểm tra & chuẩn hóa cấu trúc JSON definition của template trước khi lưu hoặc sinh workflow."""
    if not isinstance(defn, dict):
        raise HTTPException(400, "definition của template phải là đối tượng JSON")
    vis = str(defn.get("visibility") or "followers_only")
    if vis not in VALID_VISIBILITIES:
        raise HTTPException(400, "visibility của template phải là followers_only hoặc all_visible")

    raw_stages = defn.get("stages")
    if not isinstance(raw_stages, list) or not raw_stages:
        raise HTTPException(400, "Template phải có ít nhất 1 giai đoạn (stage)")
    seen_names: set[str] = set()
    clean_stages = []
    for idx, s in enumerate(raw_stages, start=1):
        if not isinstance(s, dict):
            raise HTTPException(400, f"Giai đoạn thứ {idx} không hợp lệ")
        name = str(s.get("name") or "").strip()
        if not name or len(name) > 200:
            raise HTTPException(400, f"Tên giai đoạn thứ {idx} không hợp lệ (1..200 ký tự)")
        key = name.casefold()
        if key in seen_names:
            raise HTTPException(400, f"Tên giai đoạn '{name}' bị trùng lặp trong template")
        seen_names.add(key)

        stype = str(s.get("type") or "normal")
        if stype not in VALID_STAGE_TYPES:
            raise HTTPException(400, f"Loại giai đoạn '{stype}' không hợp lệ")
        mode = str(s.get("assign_mode") or "all")
        if mode not in VALID_ASSIGN_MODES:
            raise HTTPException(400, f"Chế độ giao việc '{mode}' không hợp lệ")
        sla = s.get("sla_hours")
        if sla is not None:
            if isinstance(sla, bool) or not isinstance(sla, int) or sla <= 0 or sla > 8760:
                raise HTTPException(400, f"Giai đoạn '{name}': sla_hours phải > 0 và <= 8760")
        clean_stages.append({
            "name": name,
            "type": stype,
            "sla_hours": sla,
            "assign_mode": mode,
            "guide_html": sanitize_html(str(s.get("guide_html") or "")),
        })

    raw_fields = defn.get("form_fields") or []
    if not isinstance(raw_fields, list):
        raise HTTPException(400, "form_fields của template phải là mảng")
    clean_fields = []
    for idx, f in enumerate(raw_fields, start=1):
        if not isinstance(f, dict):
            raise HTTPException(400, f"Trường động thứ {idx} không hợp lệ")
        label = str(f.get("label") or "").strip()
        ftype = str(f.get("type") or "")
        if not label or len(label) > 200:
            raise HTTPException(400, f"Nhãn trường thứ {idx} không hợp lệ")
        if ftype not in VALID_FIELD_TYPES:
            raise HTTPException(400, f"Kiểu trường '{ftype}' không hợp lệ")
        opts = None
        if ftype == "select":
            opts = [str(o).strip() for o in (f.get("options") or []) if str(o).strip()]
            if not opts:
                raise HTTPException(400, f"Trường '{label}' (select) cần ít nhất 1 lựa chọn")
        clean_fields.append({
            "label": label,
            "type": ftype,
            "required": bool(f.get("required", False)),
            "placeholder": str(f.get("placeholder") or "")[:200],
            "options": opts,
        })

    raw_reasons = defn.get("failure_reasons") or []
    if not isinstance(raw_reasons, list):
        raise HTTPException(400, "failure_reasons của template phải là mảng")
    clean_reasons = [
        str(r.get("label") if isinstance(r, dict) else r).strip()[:200]
        for r in raw_reasons
        if str(r.get("label") if isinstance(r, dict) else r).strip()
    ]

    return {
        "visibility": vis,
        "guide_html": sanitize_html(str(defn.get("guide_html") or "")),
        "stages": clean_stages,
        "form_fields": clean_fields,
        "failure_reasons": clean_reasons,
    }


def seed_builtin_templates(db, *, commit: bool = True) -> dict:
    """Nạp 5 template nội bộ mặc định (idempotent theo slug)."""
    created = []
    skipped = []
    for spec in BUILTIN_TEMPLATES:
        slug = spec["slug"]
        if db.scalar(select(WorkflowTemplate.id).where(WorkflowTemplate.slug == slug)) is not None:
            skipped.append(slug)
            continue
        defn = validate_template_definition(spec["definition"])
        db.add(WorkflowTemplate(
            slug=slug,
            name=spec["name"],
            category=spec["category"],
            description=spec["description"],
            definition=defn,
            is_builtin=True,
            creator_id=None,
            created_at=utcnow(),
        ))
        created.append(slug)
    if commit and created:
        db.commit()
    elif created:
        db.flush()
    return {"created": created, "skipped": skipped}


def workflow_to_definition(db, wf, ordered_active_stages, fields, reasons) -> Dict[str, Any]:
    """Chuyển cấu trúc workflow hiện tại thành JSON definition cho template."""
    return {
        "visibility": wf.visibility,
        "guide_html": wf.guide_html or "",
        "stages": [
            {
                "name": s.name,
                "type": s.type,
                "sla_hours": s.sla_hours,
                "assign_mode": s.assign_mode,
                "guide_html": s.guide_html or "",
            }
            for s in ordered_active_stages
        ],
        "form_fields": [
            {
                "label": f.label,
                "type": f.type,
                "required": bool(f.required),
                "placeholder": f.placeholder or "",
                "options": list(f.options) if f.options else None,
            }
            for f in fields
            if f.is_active
        ],
        "failure_reasons": [r.label for r in reasons],
    }
