"""Kiểm thử Milestone W6: tích hợp bước ký (pyHanko) & duyệt vào Workflow Engine W5."""
from __future__ import annotations

import json
import os
import tempfile

import pytest
from sqlalchemy import delete, inspect, select

from conftest import TEST_DATA, TEST_SIGN_PIN, build_env, read
from docx_factory import make_docx
from test_workflow_signing import (
    _assign_cert_db,
    _local_real_signer,
    _make_pdf,
    _patch_fake_signer,
    _sign_body,
)


def _patch_fake_wf_signer(monkeypatch):
    import app.routes_wfengine as rwf
    import app.routes_workflow as rw

    _patch_fake_signer(monkeypatch)
    monkeypatch.setattr(rwf, "signer_verify", rw.signer_verify)


def _create_wf(
    api,
    name: str,
    *,
    stages: list[dict],
    visibility: str = "all_visible",
    failure_reasons: list[dict] | None = None,
    roles: list[dict] | None = None,
) -> dict:
    r = api.post(
        "/api/workflows",
        json={
            "name": name,
            "visibility": visibility,
            "stages": stages,
            "failure_reasons": failure_reasons or [],
            "roles": roles or [],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_w6_e2e_5_stage_cong_van_den_real_pyhanko(tmp_path):
    """E2E: QT công văn đến đi hết 5 stage, stage 4 ký thật pyHanko -> PDF cuối có chữ ký hợp lệ."""
    from esign_core import make_vc, verify_pdf

    from app.models import User
    from app.seed_workflows import seed_sample_workflows

    def _run_e2e(env, pub_dir: str, *, level: str):
        env.register_certs("bob")
        vt = env.make_user("cv_vt")
        gd = env.make_user("cv_gd", cert_keys=["bob"], pin="268134")
        bm1 = env.make_user("cv_bm1")
        bm2 = env.make_user("cv_bm2")
        fo = env.make_user("cv_fo")

        sf = env.app.state.session_factory
        db = sf()
        try:
            admin_u = db.scalar(select(User).where(User.username == "admin"))
            seeded = seed_sample_workflows(
                db,
                admin_u,
                member_ids_by_slug={
                    "qt-cong-van-den": [[vt.uid], [gd.uid], [bm1.uid, bm2.uid], [gd.uid], [vt.uid]],
                },
                extra_roles=[(vt.uid, "creator"), (fo.uid, "viewer")],
            )
            wf_id = seeded["workflows"]["qt-cong-van-den"]
        finally:
            db.close()

        wf = env.admin.get(f"/api/workflows/{wf_id}").json()
        f_map = {f["label"]: str(f["id"]) for f in wf["form_fields"]}
        r_scan = vt.post("/api/files", files={"file": ("scan-cv.pdf", _make_pdf(1), "application/pdf")})
        assert r_scan.status_code == 201, r_scan.text
        scan_id = r_scan.json()["id"]

        r_task = vt.post(
            f"/api/workflows/{wf_id}/tasks",
            json={
                "title": "Công văn 88/CV-W6",
                "custom_values": {
                    f_map["Tên công văn"]: "Kế hoạch triển khai ký số W6",
                    f_map["Đơn vị gửi"]: "Văn phòng UBND",
                    f_map["Trích yếu"]: "Đề nghị rà soát và ký duyệt",
                    f_map["Thời hạn trả lời"]: "2026-12-15",
                    f_map["Scan văn bản"]: scan_id,
                },
                "follower_ids": [fo.uid],
            },
        )
        assert r_task.status_code == 201, r_task.text
        tid = r_task.json()["id"]

        # Gắn tài liệu chính (PDF 2 trang) để stage 4 thực hiện ký số
        r_att = vt.post(
            f"/api/tasks/{tid}/document",
            files={"file": ("cong-van-chinh.pdf", read("sample.pdf"), "application/pdf")},
            data={"title": "Công văn 88 cần ký duyệt"},
        )
        assert r_att.status_code == 200, r_att.text
        attached = r_att.json()
        doc_id = attached["document"]["id"]
        assert attached["document_id"] == doc_id
        assert attached["document"]["current_version_no"] == 0

        # Stage 1 (normal): Văn thư tiếp nhận
        td1 = next(x for x in attached["todos"] if x["stage_order"] == 1)
        r_s1 = vt.post(f"/api/todos/{td1['id']}/complete", json={"result": "Đã vào sổ"})
        assert r_s1.status_code == 200 and r_s1.json()["current_stage"]["order"] == 2

        # Stage 2 (approve): Giám đốc cho ý kiến
        td2 = next(x for x in r_s1.json()["todos"] if x["stage_order"] == 2)
        r_s2 = gd.post(
            f"/api/todos/{td2['id']}/complete",
            json={"decision": "approve", "result": "Chuyển bộ môn"},
        )
        assert r_s2.status_code == 200 and r_s2.json()["current_stage"]["order"] == 3

        # Stage 3 (normal, all): Bộ môn thực hiện -> vào Stage 4 (sign)
        s3_todos = {x["assignee"]["id"]: x for x in r_s2.json()["todos"] if x["stage_order"] == 3}
        assert bm1.post(f"/api/todos/{s3_todos[bm1.uid]['id']}/complete", json={}).status_code == 200
        r_s3 = bm2.post(f"/api/todos/{s3_todos[bm2.uid]['id']}/complete", json={})
        assert r_s3.status_code == 200
        s4_state = r_s3.json()
        assert s4_state["current_stage"]["order"] == 4
        assert s4_state["current_stage"]["type"] == "sign"
        assert s4_state["document"]["current_sign_step"]["status"] == "active"
        td4 = next(x for x in s4_state["todos"] if x["stage_order"] == 4)
        assert td4["workflow_step_id"] is not None and td4["step_assignee_id"] is not None

        # Giám đốc nhận được thông báo ký tài liệu kèm document_id & task_id
        gd_notifs = gd.get("/api/notifications").json()["items"]
        assert any(
            n["kind"] == "task_assigned" and n["task_id"] == tid and n["document_id"] == doc_id
            for n in gd_notifs
        )

        # Stage 4 (sign): Giám đốc ký thật qua pyHanko
        r_sign = gd.post(
            f"/api/tasks/{tid}/sign",
            json=_sign_body(
                env.certs["bob"],
                pin="268134",
                page=-1,
                box=[40, 40, 260, 110],
                reason="Giám đốc ký duyệt công văn",
                level=level,
            ),
        )
        assert r_sign.status_code == 200, r_sign.text
        sign_out = r_sign.json()
        assert sign_out["version_no"] == 1
        assert sign_out["step_completed"] is True
        assert sign_out["task_id"] == tid and sign_out["task_status"] == "processing"

        after_sign = vt.get(f"/api/tasks/{tid}").json()
        assert after_sign["current_stage"]["order"] == 5
        td4_done = next(x for x in after_sign["todos"] if x["id"] == td4["id"])
        assert td4_done["status"] == "done"
        sig_ref = json.loads(td4_done["result"])
        assert sig_ref["version_no"] == 1 and sig_ref["cert_key_ref"] == "bob"

        # Stage 5 (normal): Văn thư phát hành -> task completed
        td5 = next(x for x in after_sign["todos"] if x["stage_order"] == 5 and x["status"] == "doing")
        r_s5 = vt.post(f"/api/todos/{td5['id']}/complete", json={"result": "Đã phát hành"})
        assert r_s5.status_code == 200
        final_task = r_s5.json()
        assert final_task["status"] == "completed"
        assert final_task["document"]["status"] == "completed"
        assert final_task["document"]["current_version_no"] == 1
        assert final_task["document"]["current_sign_step"]["status"] == "completed"
        assert len(final_task["document"]["signatures"]) == 1
        assert final_task["document"]["signatures"][0]["version_no"] == 1
        assert final_task["document"]["signatures"][0]["cert_key_ref"] == "bob"
        assert final_task["document"]["signatures"][0]["signer"]["id"] == gd.uid

        # Kiểm tra xác minh PDF cuối qua endpoint & pyHanko trực tiếp
        r_ver = fo.post(f"/api/tasks/{tid}/document/verify")
        assert r_ver.status_code == 200, r_ver.text
        assert r_ver.json()["all_ok"] is True and len(r_ver.json()["signatures"]) == 1

        pdf_bytes = fo.get(f"/api/tasks/{tid}/document/file").content
        vc = make_vc(
            os.path.join(pub_dir, "root.crt"),
            os.path.join(pub_dir, "chain.pem"),
            [os.path.join(pub_dir, "int.crl"), os.path.join(pub_dir, "root.crl")],
            revocation="require",
        )
        with tempfile.TemporaryDirectory() as td:
            final_pdf = os.path.join(td, "final.pdf")
            with open(final_pdf, "wb") as fh:
                fh.write(pdf_bytes)
            sigs = verify_pdf(final_pdf, vc)
            assert len(sigs) == 1
            assert sigs[0]["ok"] and sigs[0]["intact"] and sigs[0]["valid"] and sigs[0]["trusted"]
            assert not sigs[0]["revoked"] and "Trần Thị B" in sigs[0]["signer"]

        # Timeline hợp nhất sự kiện task + document và audit có task_id/workflow_id/document_id
        tl_actions = {e["action"] for e in vt.get(f"/api/tasks/{tid}/timeline").json()["events"]}
        act_actions = {e["action"] for e in final_task["activity"]}
        expected_actions = {
            "task_created",
            "doc_create",
            "task_document_attached",
            "doc_assign",
            "doc_sign",
            "doc_complete",
            "task_completed",
        }
        assert expected_actions <= tl_actions
        assert expected_actions <= act_actions
        audit_rows = env.admin.get(f"/api/admin/audit?task_id={tid}&workflow_id={wf_id}").json()
        assert any(a["action"] == "doc_sign" and a["document_id"] == doc_id for a in audit_rows)

    if os.environ.get("SIGNER_URL"):
        env = build_env(tmp_path, with_services=True)
        pki_dir = os.environ.get("PKI_DIR", TEST_DATA)
        _run_e2e(env, os.path.join(pki_dir, "public"), level="B-T")
    else:
        with _local_real_signer(tmp_path) as (signer_url, tok, pub_dir):
            env = build_env(tmp_path / "app", with_services=False, signer_url=signer_url, signer_token=tok)
            _run_e2e(env, pub_dir, level="B-B")


def test_w6_sign_stage_guards_no_doc_manual_complete_and_doc_attach_rules(tmp_path, monkeypatch):
    """Vào stage sign thiếu doc -> 409; hoàn thành tay -> 409; gắn docx/file_id/doc_id & khóa sau khi ký."""
    import app.routes_files as rf

    env = build_env(tmp_path, with_services=False)
    _patch_fake_wf_signer(monkeypatch)
    if not os.environ.get("CONVERTER_URL"):
        monkeypatch.setattr(rf, "convert_office", lambda _st, _data, _ext, _name: _make_pdf(1))

    creator = env.make_user("guard_creator")
    signer = env.make_user("guard_signer", pin=TEST_SIGN_PIN)
    outsider = env.make_user("guard_outsider")
    _assign_cert_db(env, signer.uid, "cert_guard", "Guard Signer")

    wf = _create_wf(
        env.admin,
        "QT Guard Sign",
        stages=[
            {"name": "Chuẩn bị", "type": "normal", "member_ids": [creator.uid]},
            {"name": "Ký duyệt", "type": "sign", "member_ids": [signer.uid]},
        ],
        roles=[
            {"user_id": creator.uid, "role": "creator"},
            {"user_id": outsider.uid, "role": "viewer"},
        ],
    )
    wf_id = wf["id"]

    # Task chưa đính kèm tài liệu: chuyển sang stage sign phải bị chặn 409
    t1 = creator.post(f"/api/workflows/{wf_id}/tasks", json={"title": "Chưa có tài liệu"}).json()
    td_prep = t1["todos"][0]["id"]
    r_block = creator.post(f"/api/todos/{td_prep}/complete", json={})
    assert r_block.status_code == 409
    t1_check = creator.get(f"/api/tasks/{t1['id']}").json()
    assert t1_check["current_stage"]["order"] == 1 and t1_check["todos"][0]["status"] == "doing"

    # Viewer không có quyền gắn tài liệu -> 403
    assert outsider.post(
        f"/api/tasks/{t1['id']}/document",
        files={"file": ("no.pdf", _make_pdf(1), "application/pdf")},
    ).status_code == 403

    # Gắn file Word .docx qua POST /api/tasks/{id}/document
    r_docx = creator.post(
        f"/api/tasks/{t1['id']}/document",
        files={
            "file": (
                "bien-ban.docx",
                make_docx(marker="DOCX W6 ATTACH"),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert r_docx.status_code == 200, r_docx.text
    assert r_docx.json()["document"]["file_name"] == "bien-ban.pdf"

    # Vào stage sign thành công sau khi có tài liệu
    r_enter = creator.post(f"/api/todos/{td_prep}/complete", json={})
    assert r_enter.status_code == 200 and r_enter.json()["current_stage"]["order"] == 2
    td_sign = next(x for x in r_enter.json()["todos"] if x["stage_order"] == 2)

    # Không thể đổi tài liệu sau khi stage sign đã bắt đầu -> 409
    assert creator.post(
        f"/api/tasks/{t1['id']}/document",
        files={"file": ("replace.pdf", _make_pdf(1), "application/pdf")},
    ).status_code == 409

    # Không thể hoàn thành tay todo trên stage sign -> 409, todo vẫn doing
    assert signer.post(f"/api/todos/{td_sign['id']}/complete", json={"result": "bypass"}).status_code == 409
    assert signer.get(f"/api/tasks/{t1['id']}").json()["status"] == "processing"

    # Kiểm tra gắn bằng file_id và document_id độc lập chưa có bước ký, chặn doc đã gắn task/đã có step (409)
    fid = creator.post("/api/files", files={"file": ("s.pdf", _make_pdf(1), "application/pdf")}).json()["id"]
    t2 = creator.post(f"/api/workflows/{wf_id}/tasks", json={"title": "Gắn qua file_id"}).json()
    assert creator.post(f"/api/tasks/{t2['id']}/document", json={"file_id": fid}).status_code == 200

    # Gắn tài liệu đã thuộc t1 cho t3 -> 409
    t3 = creator.post(f"/api/workflows/{wf_id}/tasks", json={"title": "Gắn doc_id"}).json()
    assert creator.post(
        f"/api/tasks/{t3['id']}/document",
        json={"document_id": r_docx.json()["document_id"]},
    ).status_code == 409

    # Tài liệu tạo qua POST /api/documents đã có bước ký độc lập -> bị chặn 409
    fid_step = creator.post("/api/files", files={"file": ("d1.pdf", _make_pdf(1), "application/pdf")}).json()["id"]
    stepped_doc_id = creator.post(
        "/api/documents",
        json={"file_id": fid_step, "title": "Doc đã có step", "assignee_ids": [signer.uid]},
    ).json()["id"]
    assert creator.post(f"/api/tasks/{t3['id']}/document", json={"document_id": stepped_doc_id}).status_code == 409

    # Document chưa phát sinh bước ký (step_order=0) -> gắn bằng document_id thành công 200
    import uuid
    from app.models import Document, DocumentVersion, utcnow

    fid_free = creator.post("/api/files", files={"file": ("d2.pdf", _make_pdf(1), "application/pdf")}).json()["id"]
    sf = env.app.state.session_factory
    db = sf()
    try:
        now = utcnow()
        free_doc_id = str(uuid.uuid4())
        db.add(Document(
            id=free_doc_id, title="Doc nháp chưa ký", description="", status="in_progress",
            creator_id=creator.uid, original_file_id=fid_free, current_file_id=fid_free,
            current_step_order=0, created_at=now, updated_at=now,
        ))
        db.flush()
        db.add(DocumentVersion(
            document_id=free_doc_id, version_no=0, file_id=fid_free,
            created_by_id=creator.uid, note="Tài liệu gốc", created_at=now,
        ))
        db.commit()
    finally:
        db.close()
    r_d3 = creator.post(f"/api/tasks/{t3['id']}/document", json={"document_id": free_doc_id})
    assert r_d3.status_code == 200 and r_d3.json()["document_id"] == free_doc_id


def test_w6_sign_stage_assign_mode_any_vs_all(tmp_path, monkeypatch):
    """Kiểm tra assign_mode any vs all ở stage sign: mở/đóng todo, hủy todo thừa và chuyển stage."""
    env = build_env(tmp_path, with_services=False)
    _patch_fake_wf_signer(monkeypatch)

    c = env.make_user("m_creator")
    s1 = env.make_user("m_s1", pin=TEST_SIGN_PIN)
    s2 = env.make_user("m_s2", pin=TEST_SIGN_PIN)
    fin = env.make_user("m_fin")
    cert1 = _assign_cert_db(env, s1.uid, "cert_ms1", "Signer One")
    cert2 = _assign_cert_db(env, s2.uid, "cert_ms2", "Signer Two")

    # 1. assign_mode = any
    wf_any = _create_wf(
        env.admin,
        "QT Sign Any",
        stages=[
            {"name": "Ký bất kỳ", "type": "sign", "assign_mode": "any", "member_ids": [s1.uid, s2.uid]},
            {"name": "Hậu kiểm", "type": "normal", "member_ids": [fin.uid]},
        ],
        roles=[{"user_id": c.uid, "role": "creator"}],
    )
    fid_any = c.post("/api/files", files={"file": ("any.pdf", _make_pdf(1), "application/pdf")}).json()["id"]
    r_pre = c.post(f"/api/workflows/{wf_any['id']}/tasks", json={"title": "Task Any"})
    assert r_pre.status_code == 409  # stage 1 là sign nhưng tạo task chưa có document_id -> chặn

    # Khi stage 1 là normal để gắn document trước khi vào stage sign (any)
    wf_any2 = _create_wf(
        env.admin,
        "QT Sign Any 2",
        stages=[
            {"name": "Đính kèm", "type": "normal", "member_ids": [c.uid]},
            {"name": "Ký bất kỳ", "type": "sign", "assign_mode": "any", "member_ids": [s1.uid, s2.uid]},
            {"name": "Kết thúc", "type": "normal", "member_ids": [fin.uid]},
        ],
        roles=[{"user_id": c.uid, "role": "creator"}],
    )
    t_any = c.post(f"/api/workflows/{wf_any2['id']}/tasks", json={"title": "Any Flow"}).json()
    r_att_any = c.post(f"/api/tasks/{t_any['id']}/document", json={"file_id": fid_any})
    assert r_att_any.status_code == 200
    doc_any = r_att_any.json()["document_id"]
    t_any_s2 = c.post(f"/api/todos/{t_any['todos'][0]['id']}/complete", json={}).json()
    assert t_any_s2["current_stage"]["order"] == 2

    # s1 ký qua /api/documents/{doc_id}/sign -> chốt stage sign ngay, todo s2 bị cancelled, sang stage 3
    r_any_sign = s1.post(f"/api/documents/{doc_any}/sign", json=_sign_body(cert1, level="B-B"))
    assert r_any_sign.status_code == 200 and r_any_sign.json()["step_completed"] is True
    assert s2.post(f"/api/documents/{doc_any}/sign", json=_sign_body(cert2, level="B-B")).status_code in (403, 404, 409)

    det_any = c.get(f"/api/tasks/{t_any['id']}").json()
    assert det_any["current_stage"]["order"] == 3
    s2_todos_map = {td["assignee"]["id"]: td["status"] for td in det_any["todos"] if td["stage_order"] == 2}
    assert s2_todos_map == {s1.uid: "done", s2.uid: "cancelled"}

    # 2. assign_mode = all
    wf_all = _create_wf(
        env.admin,
        "QT Sign All",
        stages=[
            {"name": "Đính kèm", "type": "normal", "member_ids": [c.uid]},
            {"name": "Ký đồng thuận", "type": "sign", "assign_mode": "all", "member_ids": [s1.uid, s2.uid]},
            {"name": "Kết thúc", "type": "normal", "member_ids": [fin.uid]},
        ],
        roles=[{"user_id": c.uid, "role": "creator"}],
    )
    t_all = c.post(f"/api/workflows/{wf_all['id']}/tasks", json={"title": "All Flow"}).json()
    assert c.post(
        f"/api/tasks/{t_all['id']}/document",
        files={"file": ("all.pdf", _make_pdf(1), "application/pdf")},
    ).status_code == 200
    t_all_s2 = c.post(f"/api/todos/{t_all['todos'][0]['id']}/complete", json={}).json()
    assert t_all_s2["current_stage"]["order"] == 2

    r_all_1 = s1.post(f"/api/tasks/{t_all['id']}/sign", json=_sign_body(cert1, level="B-B"))
    assert r_all_1.status_code == 200 and r_all_1.json()["step_completed"] is False
    mid_det = c.get(f"/api/tasks/{t_all['id']}").json()
    assert mid_det["current_stage"]["order"] == 2
    assert {td["assignee"]["id"]: td["status"] for td in mid_det["todos"] if td["stage_order"] == 2} == {
        s1.uid: "done",
        s2.uid: "doing",
    }

    r_all_2 = s2.post(f"/api/tasks/{t_all['id']}/sign", json=_sign_body(cert2, box=[260, 40, 460, 100], level="B-B"))
    assert r_all_2.status_code == 200 and r_all_2.json()["step_completed"] is True
    done_all = c.get(f"/api/tasks/{t_all['id']}").json()
    assert done_all["current_stage"]["order"] == 3
    assert done_all["document"]["current_version_no"] == 2
    assert len(done_all["document"]["signatures"]) == 2


def test_w6_reject_signing_and_pin_enforcement(tmp_path, monkeypatch):
    """Từ chối ký bắt buộc failure_reason_id đúng quy trình; chặn bypass PIN; sai 5 lần khóa 423."""
    env = build_env(tmp_path, with_services=False)
    _patch_fake_wf_signer(monkeypatch)

    creator = env.make_user("rj_creator")
    s1 = env.make_user("rj_s1", pin=TEST_SIGN_PIN)
    s2 = env.make_user("rj_s2", pin=TEST_SIGN_PIN)
    outsider = env.make_user("rj_out", pin=TEST_SIGN_PIN)
    cert1 = _assign_cert_db(env, s1.uid, "cert_r1", "Reject 1")
    _assign_cert_db(env, s2.uid, "cert_r2", "Reject 2")

    wf_other = _create_wf(
        env.admin,
        "QT Khác",
        stages=[{"name": "B1", "type": "normal", "member_ids": [creator.uid]}],
        failure_reasons=[{"label": "Lý do workflow khác"}],
    )
    other_reason_id = wf_other["failure_reasons"][0]["id"]

    wf = _create_wf(
        env.admin,
        "QT Reject & PIN",
        visibility="followers_only",
        stages=[
            {"name": "Soạn thảo", "type": "normal", "member_ids": [creator.uid]},
            {"name": "Ký đôi", "type": "sign", "assign_mode": "all", "member_ids": [s1.uid, s2.uid]},
            {"name": "Phát hành", "type": "normal", "member_ids": [creator.uid]},
        ],
        failure_reasons=[{"label": "Sai nội dung văn bản"}],
        roles=[{"user_id": creator.uid, "role": "creator"}],
    )
    wf_reason_id = wf["failure_reasons"][0]["id"]

    t1 = creator.post(f"/api/workflows/{wf['id']}/tasks", json={"title": "Task PIN & Reject"}).json()
    assert creator.post(
        f"/api/tasks/{t1['id']}/document",
        files={"file": ("rj.pdf", _make_pdf(1), "application/pdf")},
    ).status_code == 200
    t1_s2 = creator.post(f"/api/todos/{t1['todos'][0]['id']}/complete", json={}).json()
    td_s1 = next(x for x in t1_s2["todos"] if x["assignee"]["id"] == s1.uid and x["status"] == "doing")
    td_s2 = next(x for x in t1_s2["todos"] if x["assignee"]["id"] == s2.uid and x["status"] == "doing")

    # Người không phải assignee gọi /todos/{id}/fail trên stage sign -> 404/403 (không lộ & không đòi PIN)
    assert outsider.post(
        f"/api/todos/{td_s1['id']}/fail",
        json={"failure_reason_id": wf_reason_id},
    ).status_code in (403, 404)

    # /todos/{id}/fail trên stage sign thiếu PIN -> bị chặn (400/401)
    assert s1.post(
        f"/api/todos/{td_s1['id']}/fail",
        json={"failure_reason_id": wf_reason_id, "note": "Thử bỏ qua PIN"},
    ).status_code in (400, 401)

    # /tasks/{id}/reject thiếu failure_reason_id hoặc dùng lý do của workflow khác -> 400
    assert s2.post(
        f"/api/tasks/{t1['id']}/reject",
        json={"reason": "Không đủ hồ sơ", "pin": TEST_SIGN_PIN},
    ).status_code == 400
    assert s2.post(
        f"/api/tasks/{t1['id']}/reject",
        json={"reason": "Sai workflow reason", "pin": TEST_SIGN_PIN, "failure_reason_id": other_reason_id},
    ).status_code == 400

    # s1 nhập sai PIN 5 lần -> khóa 423 + X-Pin-Error: pin_locked; dùng PIN đúng khi đang khóa vẫn 423
    for _ in range(4):
        r_bad = s1.post(f"/api/tasks/{t1['id']}/sign", json=_sign_body(cert1, pin="000000", level="B-B"))
        assert r_bad.status_code == 401
    r_lock = s1.post(f"/api/tasks/{t1['id']}/sign", json=_sign_body(cert1, pin="000000", level="B-B"))
    assert r_lock.status_code == 423 and r_lock.headers.get("X-Pin-Error") == "pin_locked"
    assert s1.post(
        f"/api/tasks/{t1['id']}/sign",
        json=_sign_body(cert1, pin=TEST_SIGN_PIN, level="B-B"),
    ).status_code == 423
    assert creator.get(f"/api/tasks/{t1['id']}").json()["status"] == "processing"

    # s2 từ chối ký qua /api/tasks/{id}/reject với PIN đúng + lý do hợp lệ -> task failed, todo còn mở bị hủy
    r_rj = s2.post(
        f"/api/tasks/{t1['id']}/reject",
        json={"reason": "Số liệu chưa khớp", "pin": TEST_SIGN_PIN, "failure_reason_id": wf_reason_id},
    )
    assert r_rj.status_code == 200, r_rj.text
    assert r_rj.json()["task_status"] == "failed" and r_rj.json()["status"] == "rejected"

    t1_final = creator.get(f"/api/tasks/{t1['id']}").json()
    assert t1_final["status"] == "failed"
    assert t1_final["failure_reason"]["id"] == wf_reason_id
    assert t1_final["document"]["status"] == "rejected"
    assert {td["assignee"]["id"]: td["status"] for td in t1_final["todos"] if td["stage_order"] == 2} == {
        s1.uid: "cancelled",
        s2.uid: "failed",
    }
    assert [td for td in t1_final["todos"] if td["stage_order"] == 3] == []

    # Task thứ 2: s2 (không phải creator/follower, workflow followers_only) từ chối trực tiếp qua /todos/{id}/fail kèm PIN
    t2 = creator.post(f"/api/workflows/{wf['id']}/tasks", json={"title": "Task Fail Via Todo"}).json()
    assert creator.post(
        f"/api/tasks/{t2['id']}/document",
        files={"file": ("rj2.pdf", _make_pdf(1), "application/pdf")},
    ).status_code == 200
    t2_s2 = creator.post(f"/api/todos/{t2['todos'][0]['id']}/complete", json={}).json()
    t2_td_s2 = next(x for x in t2_s2["todos"] if x["assignee"]["id"] == s2.uid and x["status"] == "doing")
    r_fail = s2.post(
        f"/api/todos/{t2_td_s2['id']}/fail",
        json={"failure_reason_id": wf_reason_id, "note": "Từ chối từ todo", "pin": TEST_SIGN_PIN},
    )
    assert r_fail.status_code == 200, r_fail.text
    assert r_fail.json()["status"] == "failed"
    assert r_fail.json()["document"]["status"] == "rejected"


def test_w6_non_stage_member_cannot_sign_and_security_isolation(tmp_path, monkeypatch):
    """Chỉ stage_members mới ký được; tài liệu gắn task bị khóa sửa/xóa ngoài luồng; visibility & cancel."""
    from app.models import StageMember

    env = build_env(tmp_path, with_services=False)
    _patch_fake_wf_signer(monkeypatch)

    creator = env.make_user("sec_creator", pin=TEST_SIGN_PIN)
    signer = env.make_user("sec_signer", pin=TEST_SIGN_PIN)
    non_member = env.make_user("sec_nonmember", pin=TEST_SIGN_PIN)
    viewer = env.make_user("sec_viewer")
    _assign_cert_db(env, creator.uid, "cert_sc", "Creator")
    cert_s = _assign_cert_db(env, signer.uid, "cert_ss", "Stage Signer")
    cert_nm = _assign_cert_db(env, non_member.uid, "cert_snm", "Outsider Signer")

    wf = _create_wf(
        env.admin,
        "QT Bảo mật W6",
        visibility="followers_only",
        stages=[
            {"name": "Đính kèm", "type": "normal", "member_ids": [creator.uid]},
            {"name": "Ký số", "type": "sign", "member_ids": [signer.uid]},
        ],
        failure_reasons=[{"label": "Hủy quy trình"}],
        roles=[
            {"user_id": creator.uid, "role": "creator"},
            {"user_id": non_member.uid, "role": "supervisor"},  # Thấy task (supervisor) nhưng KHÔNG thuộc stage_members
            {"user_id": viewer.uid, "role": "viewer"},
        ],
    )
    fr_id = wf["failure_reasons"][0]["id"]

    fid = creator.post("/api/files", files={"file": ("sec.pdf", _make_pdf(1), "application/pdf")}).json()["id"]
    t = creator.post(f"/api/workflows/{wf['id']}/tasks", json={"title": "Task Bảo Mật"}).json()
    tid = t["id"]
    att = creator.post(f"/api/tasks/{tid}/document", json={"file_id": fid}).json()
    doc_id = att["document_id"]
    s2_det = creator.post(f"/api/todos/{att['todos'][0]['id']}/complete", json={}).json()
    s2_stage_id = s2_det["current_stage"]["id"]

    # Người có chứng thư nhưng không thuộc stage_members (kể cả creator/supervisor) không ký/từ chối được
    assert non_member.post(f"/api/tasks/{tid}/sign", json=_sign_body(cert_nm, level="B-B")).status_code == 403
    assert non_member.post(f"/api/documents/{doc_id}/sign", json=_sign_body(cert_nm, level="B-B")).status_code == 403
    assert non_member.post(
        f"/api/tasks/{tid}/reject",
        json={"reason": "Không thuộc bước", "pin": TEST_SIGN_PIN, "failure_reason_id": fr_id},
    ).status_code == 403

    # Tài liệu đang gắn task bị khóa trước mọi thao tác ngoài luồng W6
    assert creator.delete(f"/api/files/{fid}").status_code == 409
    assert creator.post(f"/api/sign", json={"file_id": fid, **_sign_body(cert_s, level="B-B")}).status_code == 409
    assert creator.post(
        f"/api/documents/{doc_id}/assign",
        json={"assignee_ids": [non_member.uid], "mode": "all"},
    ).status_code == 409
    assert creator.post(f"/api/documents/{doc_id}/complete").status_code == 409
    assert creator.post(f"/api/documents/{doc_id}/cancel", json={"pin": TEST_SIGN_PIN}).status_code == 409
    assert signer.post(
        f"/api/documents/{doc_id}/sign",
        json=_sign_body(cert_s, level="B-B", next_action="complete"),
    ).status_code == 409

    # Visibility followers_only áp dụng cho cả tài liệu liên kết
    assert viewer.get(f"/api/tasks/{tid}").status_code == 404
    assert viewer.get(f"/api/tasks/{tid}/document/file").status_code == 404
    assert viewer.post(f"/api/tasks/{tid}/document/verify").status_code == 404
    assert viewer.get(f"/api/documents/{doc_id}").status_code == 404
    assert viewer.get(f"/api/documents/{doc_id}/file").status_code == 404
    assert viewer.get(f"/api/documents/{doc_id}/versions/0/file").status_code == 404
    assert doc_id not in {d["id"] for d in viewer.get("/api/documents").json()}

    # Phòng thủ tầng dữ liệu: nếu xóa signer khỏi stage_members, ký qua cả /tasks & /documents đều bị 403
    sf = env.app.state.session_factory
    db = sf()
    try:
        db.execute(
            delete(StageMember).where(StageMember.stage_id == s2_stage_id, StageMember.user_id == signer.uid)
        )
        db.commit()
    finally:
        db.close()
    assert signer.post(f"/api/tasks/{tid}/sign", json=_sign_body(cert_s, level="B-B")).status_code == 403
    assert signer.post(f"/api/documents/{doc_id}/sign", json=_sign_body(cert_s, level="B-B")).status_code == 403

    # Hủy task đang ở stage sign -> đóng toàn bộ todo, hủy WorkflowStep, doc -> cancelled, có audit doc_cancel
    r_can = creator.post(
        f"/api/tasks/{tid}/cancel",
        json={"reason": "Dừng theo yêu cầu", "failure_reason_id": fr_id},
    )
    assert r_can.status_code == 200, r_can.text
    can_det = r_can.json()
    assert can_det["status"] == "failed" and can_det["failure_reason"]["id"] == fr_id
    assert can_det["document"]["status"] == "cancelled"
    assert can_det["document"]["current_sign_step"]["status"] == "cancelled"
    assert all(td["status"] in ("done", "cancelled") for td in can_det["todos"])
    audits = env.admin.get(f"/api/admin/audit?task_id={tid}&workflow_id={wf['id']}").json()
    assert {"doc_cancel", "task_cancelled"} <= {a["action"] for a in audits}
    assert any(a["action"] == "doc_cancel" and a["document_id"] == doc_id for a in audits)


def test_w6_approve_stage_approve_and_reject(tmp_path):
    """Stage type=approve: hoàn thành với decision=approve/reject (không dùng chữ ký số), ghi audit decision."""
    env = build_env(tmp_path, with_services=False)
    creator = env.make_user("ap_creator")
    approver1 = env.make_user("ap_a1")
    approver2 = env.make_user("ap_a2")
    fin = env.make_user("ap_fin")

    wf = _create_wf(
        env.admin,
        "QT Phê duyệt",
        stages=[
            {"name": "Trưởng phòng duyệt", "type": "approve", "assign_mode": "all", "member_ids": [approver1.uid, approver2.uid]},
            {"name": "Lưu hồ sơ", "type": "normal", "member_ids": [fin.uid]},
        ],
        failure_reasons=[{"label": "Chưa đạt tiêu chuẩn"}],
        roles=[{"user_id": creator.uid, "role": "creator"}],
    )
    fr_id = wf["failure_reasons"][0]["id"]

    # 1. Approve flow (không có cert, không dùng PIN)
    t_ok = creator.post(f"/api/workflows/{wf['id']}/tasks", json={"title": "Duyệt đạt"}).json()
    t_todos = {td["assignee"]["id"]: td["id"] for td in t_ok["todos"]}
    assert approver1.post(
        f"/api/todos/{t_todos[approver1.uid]}/complete",
        json={"decision": "approve", "result": "Đồng ý bước 1"},
    ).status_code == 200
    r_ok2 = approver2.post(
        f"/api/todos/{t_todos[approver2.uid]}/complete",
        json={"decision": "approve", "result": "Đồng ý bước 2"},
    )
    assert r_ok2.status_code == 200 and r_ok2.json()["current_stage"]["order"] == 2

    # Truyền decision ở stage normal -> 400
    fin_td = next(td["id"] for td in r_ok2.json()["todos"] if td["status"] == "doing")
    assert fin.post(f"/api/todos/{fin_td}/complete", json={"decision": "approve"}).status_code == 400

    # 2. Reject flow
    t_rj = creator.post(f"/api/workflows/{wf['id']}/tasks", json={"title": "Duyệt không đạt"}).json()
    rj_todos = {td["assignee"]["id"]: td["id"] for td in t_rj["todos"]}
    assert approver1.post(
        f"/api/todos/{rj_todos[approver1.uid]}/complete",
        json={"decision": "reject"},
    ).status_code == 400  # thiếu failure_reason_id
    r_rj = approver1.post(
        f"/api/todos/{rj_todos[approver1.uid]}/complete",
        json={"decision": "reject", "failure_reason_id": fr_id, "note": "Cần bổ sung phụ lục"},
    )
    assert r_rj.status_code == 200
    rj_det = r_rj.json()
    assert rj_det["status"] == "failed" and rj_det["failure_reason"]["id"] == fr_id
    assert {td["assignee"]["id"]: td["status"] for td in rj_det["todos"]} == {
        approver1.uid: "failed",
        approver2.uid: "cancelled",
    }

    aud_ok = env.admin.get(f"/api/admin/audit?task_id={t_ok['id']}").json()
    aud_rj = env.admin.get(f"/api/admin/audit?task_id={t_rj['id']}").json()
    assert any(a["action"] == "todo_completed" and json.loads(a["metadata_json"]).get("decision") == "approve" for a in aud_ok)
    assert any(a["action"] == "todo_failed" and json.loads(a["metadata_json"]).get("decision") == "reject" for a in aud_rj)


def test_w6_idempotent_migration_on_pre_w6_db(tmp_path):
    """Migration W6 thêm các cột/index mới lên DB W5 sẵn có và chạy lại nhiều lần không lỗi."""
    from sqlalchemy import text

    from app.models import init_db, make_engine, migrate_db

    db_path = tmp_path / "pre_w6.db"
    eng = make_engine(f"sqlite:///{db_path}")
    with eng.begin() as con:
        con.execute(text(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(64) UNIQUE NOT NULL, "
            "password_hash VARCHAR(255) NOT NULL, display_name VARCHAR(120) DEFAULT '', "
            "role VARCHAR(16) DEFAULT 'user', email VARCHAR(255) DEFAULT '', "
            "job_title VARCHAR(120) DEFAULT '', department VARCHAR(120) DEFAULT '', "
            "is_active BOOLEAN DEFAULT 1, totp_secret TEXT, mfa_enabled BOOLEAN DEFAULT 0, "
            "created_at DATETIME NOT NULL)"
        ))
        con.execute(text(
            "CREATE TABLE stored_files (id VARCHAR(36) PRIMARY KEY, owner_id INTEGER NOT NULL, "
            "original_name VARCHAR(255) NOT NULL, kind VARCHAR(16) NOT NULL, sha256 VARCHAR(64) NOT NULL, "
            "size INTEGER NOT NULL, pages INTEGER DEFAULT 0, parent_id VARCHAR(36), is_workflow BOOLEAN DEFAULT 0, "
            "created_at DATETIME NOT NULL, expires_at DATETIME NOT NULL, deleted_at DATETIME)"
        ))
        con.execute(text(
            "CREATE TABLE documents (id VARCHAR(36) PRIMARY KEY, title VARCHAR(255) NOT NULL, "
            "description VARCHAR(2000) DEFAULT '', status VARCHAR(16) DEFAULT 'in_progress', "
            "creator_id INTEGER NOT NULL, original_file_id VARCHAR(36) NOT NULL, "
            "current_file_id VARCHAR(36) NOT NULL, current_step_order INTEGER DEFAULT 1, "
            "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, completed_at DATETIME, rejected_at DATETIME)"
        ))
        con.execute(text(
            "CREATE TABLE workflow_steps (id INTEGER PRIMARY KEY, document_id VARCHAR(36) NOT NULL, "
            "step_order INTEGER NOT NULL, mode VARCHAR(8) NOT NULL, status VARCHAR(16) DEFAULT 'active', "
            "assigned_by_id INTEGER NOT NULL, created_at DATETIME NOT NULL, completed_at DATETIME)"
        ))
        con.execute(text(
            "CREATE TABLE workflows (id INTEGER PRIMARY KEY, name VARCHAR(200) NOT NULL, "
            "slug VARCHAR(120) UNIQUE NOT NULL, category VARCHAR(100) DEFAULT '', description TEXT DEFAULT '', "
            "guide_html TEXT DEFAULT '', visibility VARCHAR(20) DEFAULT 'followers_only', "
            "status VARCHAR(16) DEFAULT 'active', creator_id INTEGER NOT NULL, created_at DATETIME NOT NULL)"
        ))
        con.execute(text(
            "CREATE TABLE stages (id INTEGER PRIMARY KEY, workflow_id INTEGER NOT NULL, "
            "\"order\" INTEGER NOT NULL, name VARCHAR(200) NOT NULL, type VARCHAR(16) DEFAULT 'normal', "
            "sla_hours INTEGER, guide_html TEXT DEFAULT '', assign_mode VARCHAR(8) DEFAULT 'all')"
        ))
        con.execute(text(
            "CREATE TABLE tasks (id INTEGER PRIMARY KEY, workflow_id INTEGER NOT NULL, current_stage_id INTEGER, "
            "title VARCHAR(255) NOT NULL, labels JSON, creator_id INTEGER NOT NULL, description_html TEXT DEFAULT '', "
            "custom_values JSON, deadline DATETIME, status VARCHAR(16) DEFAULT 'processing', "
            "failure_reason_id INTEGER, document_id VARCHAR(36), created_at DATETIME NOT NULL, "
            "updated_at DATETIME NOT NULL, completed_at DATETIME, archived_at DATETIME)"
        ))
        con.execute(text(
            "CREATE TABLE todos (id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL, stage_id INTEGER NOT NULL, "
            "assignee_id INTEGER NOT NULL, result TEXT, deadline DATETIME, status VARCHAR(16) DEFAULT 'doing', "
            "created_at DATETIME NOT NULL, completed_at DATETIME)"
        ))
        con.execute(text(
            "INSERT INTO workflow_steps (id, document_id, step_order, mode, status, assigned_by_id, created_at) "
            "VALUES (10, 'doc-1', 1, 'all', 'completed', 1, '2026-01-01 00:00:00')"
        ))
        con.execute(text(
            "INSERT INTO todos (id, task_id, stage_id, assignee_id, status, created_at) "
            "VALUES (20, 5, 2, 1, 'done', '2026-01-01 00:00:00')"
        ))

    init_db(eng)
    migrate_db(eng)  # chạy lần 2 kiểm tra idempotent

    insp = inspect(eng)
    ws_cols = {c["name"] for c in insp.get_columns("workflow_steps")}
    td_cols = {c["name"] for c in insp.get_columns("todos")}
    assert "task_id" in ws_cols
    assert {"workflow_step_id", "step_assignee_id"} <= td_cols
    with eng.connect() as con:
        assert con.execute(text("SELECT task_id FROM workflow_steps WHERE id=10")).scalar() is None
        row = con.execute(text("SELECT workflow_step_id, step_assignee_id, status FROM todos WHERE id=20")).first()
        assert tuple(row) == (None, None, "done")
