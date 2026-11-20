"""Kiểm thử Milestone W5: Workflow Engine tổng quát (Workflow -> Stage -> Task -> Todo), visibility, form động, seed & W6 hook."""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import pytest
from pypdf import PdfWriter
from sqlalchemy import inspect, select, text

from conftest import build_env


def _make_pdf_bytes() -> bytes:
    w = PdfWriter()
    w.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _upload_pdf(api, name: str = "scan.pdf") -> str:
    r = api.post("/api/files", files={"file": (name, _make_pdf_bytes(), "application/pdf")})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_w5_migration_on_existing_db_and_audit_triggers(tmp_path):
    from app.models import AuditLog, Notification, User, Workflow, init_db, make_engine, make_session_factory

    db_path = tmp_path / "pre_w5.db"
    eng = make_engine("sqlite:///" + str(db_path).replace("\\", "/"))

    # Tạo DB dạng tiền-W5 (có users, files, documents, notifications chưa có task_id, audit_logs chưa có task_id/workflow_id)
    ddls = [
        """CREATE TABLE users (
            id INTEGER PRIMARY KEY, username VARCHAR(64) UNIQUE NOT NULL,
            display_name VARCHAR(120) NOT NULL DEFAULT '', email VARCHAR(255),
            password_hash VARCHAR(255) NOT NULL, role VARCHAR(16) NOT NULL DEFAULT 'user',
            is_active BOOLEAN NOT NULL DEFAULT 1, totp_secret_enc TEXT, totp_enabled BOOLEAN NOT NULL DEFAULT 0,
            totp_last_step INTEGER NOT NULL DEFAULT 0, failed_attempts INTEGER NOT NULL DEFAULT 0,
            locked_until DATETIME, signing_pin_hash VARCHAR(255), pin_set_at TIMESTAMP,
            pin_failed_attempts INTEGER NOT NULL DEFAULT 0, pin_fail_times_json TEXT,
            pin_locked_until TIMESTAMP, job_title VARCHAR(120) NOT NULL DEFAULT '',
            department VARCHAR(120) NOT NULL DEFAULT '', manager_name VARCHAR(120) NOT NULL DEFAULT '',
            manager_user_id INTEGER, signature_image_file_id VARCHAR(36),
            onboarding_step INTEGER NOT NULL DEFAULT 1, onboarding_completed_at TIMESTAMP,
            accepted_terms_version VARCHAR(64), onboarding_otp_verified_at TIMESTAMP,
            onboarding_otp_session_id VARCHAR(64), created_at DATETIME NOT NULL
        )""",
        """CREATE TABLE files (
            id VARCHAR(36) PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id),
            original_name VARCHAR(255) NOT NULL DEFAULT '', kind VARCHAR(16) NOT NULL,
            sha256 VARCHAR(64) NOT NULL, size INTEGER NOT NULL, pages INTEGER NOT NULL DEFAULT 0,
            parent_id VARCHAR(36), is_workflow BOOLEAN NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL, expires_at DATETIME NOT NULL, deleted_at DATETIME
        )""",
        """CREATE TABLE documents (
            id VARCHAR(36) PRIMARY KEY, title VARCHAR(255) NOT NULL, description TEXT NOT NULL DEFAULT '',
            status VARCHAR(16) NOT NULL DEFAULT 'in_progress', creator_id INTEGER NOT NULL REFERENCES users(id),
            original_file_id VARCHAR(36) NOT NULL REFERENCES files(id),
            current_file_id VARCHAR(36) NOT NULL REFERENCES files(id),
            current_step_order INTEGER NOT NULL DEFAULT 1,
            completed_at DATETIME, rejected_at DATETIME, cancelled_at DATETIME,
            cancel_reason VARCHAR(500), created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
        )""",
        """CREATE TABLE notifications (
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            document_id VARCHAR(36) REFERENCES documents(id) ON DELETE CASCADE,
            kind VARCHAR(24) NOT NULL, title VARCHAR(200) NOT NULL, message VARCHAR(500) NOT NULL DEFAULT '',
            is_read BOOLEAN NOT NULL DEFAULT 0, created_at DATETIME NOT NULL, read_at DATETIME
        )""",
        """CREATE TABLE audit_logs (
            id INTEGER PRIMARY KEY, ts DATETIME NOT NULL, user_id INTEGER,
            username VARCHAR(64) NOT NULL DEFAULT '', action VARCHAR(48) NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'ok', ip VARCHAR(64) NOT NULL DEFAULT '',
            file_id VARCHAR(36), file_sha256 VARCHAR(64), output_sha256 VARCHAR(64),
            cert_key_ref VARCHAR(64), detail VARCHAR(500), document_id VARCHAR(36), metadata_json TEXT
        )""",
    ]
    with eng.begin() as con:
        for ddl in ddls:
            con.exec_driver_sql(ddl)
        con.exec_driver_sql(
            "INSERT INTO users (id, username, display_name, password_hash, role, created_at) "
            "VALUES (1, 'old_admin', 'Old Admin', 'h', 'admin', '2026-01-01 00:00:00')"
        )
        con.exec_driver_sql(
            "INSERT INTO notifications (id, user_id, kind, title, created_at) "
            "VALUES (1, 1, 'assigned', 'Thong bao cu', '2026-01-01 00:00:00')"
        )
        con.exec_driver_sql(
            "INSERT INTO audit_logs (id, ts, user_id, username, action) "
            "VALUES (1, '2026-01-01 00:00:00', 1, 'old_admin', 'login')"
        )

    # Chạy init_db 2 lần để bảo đảm idempotent trên DB đang có dữ liệu
    init_db(eng)
    init_db(eng)

    insp = inspect(eng)
    tables = set(insp.get_table_names())
    assert {
        "workflows", "workflow_roles", "workflow_failure_reasons",
        "stages", "stage_members", "form_fields",
        "tasks", "task_followers", "todos",
    } <= tables
    assert {"ix_tasks_workflow_status", "ix_tasks_current_stage"} <= {i["name"] for i in insp.get_indexes("tasks")}
    assert {"ix_todos_assignee_status", "ix_todos_deadline"} <= {i["name"] for i in insp.get_indexes("todos")}
    assert "task_id" in {c["name"] for c in insp.get_columns("notifications")}
    assert {"task_id", "workflow_id"} <= {c["name"] for c in insp.get_columns("audit_logs")}

    sf = make_session_factory(eng)
    db = sf()
    try:
        u = db.get(User, 1)
        assert u is not None and db.get(Notification, 1).task_id is None
        row = AuditLog(action="task_created", username="old_admin", task_id=99, workflow_id=7)
        db.add(row)
        db.commit()

        # ORM & SQLite trigger phải chặn UPDATE/DELETE trên audit_logs
        row.detail = "tampered"
        with pytest.raises(PermissionError):
            db.commit()
        db.rollback()
        db.delete(db.get(AuditLog, row.id))
        with pytest.raises(PermissionError):
            db.commit()
        db.rollback()
    finally:
        db.close()

    for sql in ("UPDATE audit_logs SET detail='hack'", "DELETE FROM audit_logs WHERE id=1"):
        with pytest.raises(Exception):
            with eng.begin() as con:
                con.execute(text(sql))


def test_w5_seed_and_full_5_stage_flow_to_completed_with_timeline_and_notifications(tmp_path):
    from app.models import User
    from app.seed_workflows import seed_sample_workflows

    env = build_env(tmp_path, with_services=False)
    vt = env.make_user("van_thu")
    gd = env.make_user("giam_doc")
    bm1 = env.make_user("bo_mon_1")
    bm2 = env.make_user("bo_mon_2")
    fo = env.make_user("theo_doi")

    sf = env.app.state.session_factory
    db = sf()
    try:
        admin_u = db.scalar(select(User).where(User.username == "admin"))
        s1 = seed_sample_workflows(
            db, admin_u,
            member_ids_by_slug={
                "qt-cong-van-den": [[vt.uid], [gd.uid], [bm1.uid, bm2.uid], [gd.uid], [vt.uid]],
                "qt-cham-cong": [[bm1.uid], [bm2.uid], [gd.uid]],
            },
            extra_roles=[(vt.uid, "creator"), (bm1.uid, "creator"), (fo.uid, "viewer")],
        )
        assert set(s1["created"]) == {"qt-cong-van-den", "qt-cham-cong"}
        # Seed lần 2 phải bỏ qua (idempotent)
        s2 = seed_sample_workflows(db, admin_u)
        assert s2["created"] == [] and set(s2["skipped"]) == {"qt-cong-van-den", "qt-cham-cong"}
        assert s2["workflows"] == s1["workflows"]
        wf_cv_id = s1["workflows"]["qt-cong-van-den"]
        wf_cc_id = s1["workflows"]["qt-cham-cong"]
    finally:
        db.close()

    # Kiểm tra cấu trúc 2 quy trình đã seed
    wf_cv = env.admin.get(f"/api/workflows/{wf_cv_id}").json()
    assert [(s["name"], s["sla_hours"], s["type"]) for s in wf_cv["stages"]] == [
        ("Văn thư tiếp nhận", 8, "normal"),
        ("Giám đốc cho ý kiến", 2, "approve"),
        ("Bộ môn thực hiện", 24, "normal"),
        ("Giám đốc ký duyệt", 1, "sign"),
        ("Văn thư phát hành", 1, "normal"),
    ]
    assert [f["label"] for f in wf_cv["form_fields"]] == [
        "Tên công văn", "Đơn vị gửi", "Trích yếu", "Thời hạn trả lời", "Scan văn bản",
    ]
    assert all(f["required"] for f in wf_cv["form_fields"])

    wf_cc = env.admin.get(f"/api/workflows/{wf_cc_id}").json()
    assert [s["sla_hours"] for s in wf_cc["stages"]] == [1, 3, 4] and wf_cc["form_fields"] == []

    # Tạo công việc trên QT công văn đến (từ chối key/field, XSS script bị lọc)
    scan_id = _upload_pdf(vt, "cv-102.pdf")
    f_map = {f["label"]: str(f["id"]) for f in wf_cv["form_fields"]}
    t0 = env.clock.t
    r_task = vt.post(f"/api/workflows/{wf_cv_id}/tasks", json={
        "title": "Công văn số 102/CV-KT",
        "labels": ["khẩn", "kế toán"],
        "description_html": "<p>Chuyển <b>gấp</b></p><script>alert(1)</script><a href=\"javascript:evil()\">x</a>",
        "custom_values": {
            f_map["Tên công văn"]: "Công văn đề nghị quyết toán",
            f_map["Đơn vị gửi"]: "Sở Tài chính",
            f_map["Trích yếu"]: "Đề nghị bổ sung hồ sơ trước thứ 6",
            f_map["Thời hạn trả lời"]: "2026-12-01",
            f_map["Scan văn bản"]: scan_id,
        },
        "follower_ids": [fo.uid],
    })
    assert r_task.status_code == 201, r_task.text
    t = r_task.json()
    tid = t["id"]
    assert "<script" not in t["description_html"] and "javascript:" not in t["description_html"]
    assert t["current_stage"]["order"] == 1 and t["status"] == "processing" and t["is_overdue"] is False

    # Kiểm tra deadline stage 1 = now + 8h; chưa hoàn thành không thể archive (409)
    dt_created = datetime.fromisoformat(t["created_at"])
    assert datetime.fromisoformat(t["deadline"]) == dt_created + timedelta(hours=8)
    assert vt.post(f"/api/tasks/{tid}/archive").status_code == 409

    # Người ở bước sau (giam_doc) không có todo bước 1 và không thể xem/hoàn thành todo bước 1 (404)
    todo_s1 = t["todos"][0]["id"]
    assert gd.post(f"/api/todos/{todo_s1}/complete", json={}).status_code == 404

    # Stage 1 (8h) -> Stage 2 (2h)
    env.clock.advance(60)
    r_s1 = vt.post(f"/api/todos/{todo_s1}/complete", json={"result": "Đã vào sổ công văn"})
    assert r_s1.status_code == 200, r_s1.text
    assert r_s1.json()["current_stage"]["order"] == 2
    s2_deadline = datetime.fromisoformat(r_s1.json()["deadline"])
    s2_now = datetime.fromtimestamp(env.clock.t, tz=timezone.utc).replace(tzinfo=None)
    assert abs((s2_deadline - (s2_now + timedelta(hours=2))).total_seconds()) < 1.0
    s2_todo = next(x for x in r_s1.json()["todos"] if x["stage_order"] == 2)
    assert datetime.fromisoformat(s2_todo["deadline"]) - datetime.fromisoformat(s2_todo["created_at"]) == timedelta(hours=2)

    # Sửa task & thêm người theo dõi giữa luồng -> kiểm tra audit & notification
    assert vt.request("PATCH", f"/api/tasks/{tid}", json={"labels": ["khẩn", "ưu tiên"]}).status_code == 200
    assert vt.post(f"/api/tasks/{tid}/followers", json={"user_id": bm1.uid}).status_code == 201

    # Stage 2 (2h) -> Stage 3 (24h, assign_mode=all: cần cả bm1 và bm2)
    r_s2 = gd.post(f"/api/todos/{s2_todo['id']}/complete", json={"result": "Giao bộ môn xử lý"})
    assert r_s2.status_code == 200
    assert r_s2.json()["current_stage"]["order"] == 3
    s3_todos = {x["assignee"]["id"]: x for x in r_s2.json()["todos"] if x["stage_order"] == 3}
    assert set(s3_todos) == {bm1.uid, bm2.uid}

    r_s3a = bm1.post(f"/api/todos/{s3_todos[bm1.uid]['id']}/complete", json={"result": "Phần 1 xong"})
    assert r_s3a.status_code == 200 and r_s3a.json()["current_stage"]["order"] == 3
    assert bm1.post(f"/api/todos/{s3_todos[bm1.uid]['id']}/complete", json={}).status_code == 409

    from app.wf_hooks import reset_stage_handlers
    from app.wf_signing import register_w6_handlers

    # Stage 3 -> Stage 4 (type=sign, W5 thuần chạy như normal) -> Stage 5
    reset_stage_handlers()
    try:
        r_s3b = bm2.post(f"/api/todos/{s3_todos[bm2.uid]['id']}/complete", json={"result": "Phần 2 xong"})
        assert r_s3b.status_code == 200 and r_s3b.json()["current_stage"]["order"] == 4
        s4_todo = next(x for x in r_s3b.json()["todos"] if x["stage_order"] == 4)

        # Stage 4 -> Stage 5
        r_s4 = gd.post(f"/api/todos/{s4_todo['id']}/complete", json={"result": "Đã duyệt ký"})
        assert r_s4.status_code == 200 and r_s4.json()["current_stage"]["order"] == 5
        s5_todo = next(x for x in r_s4.json()["todos"] if x["stage_order"] == 5)
    finally:
        register_w6_handlers()

    # Stage 5 -> completed
    r_s5 = vt.post(f"/api/todos/{s5_todo['id']}/complete", json={"result": "Đã phát hành"})
    assert r_s5.status_code == 200
    done = r_s5.json()
    assert done["status"] == "completed" and done["completed_at"] is not None
    assert all(s["state"] == "done" for s in done["stages"])

    # Archive & Restore + Timeline dùng chung + Notifications + Audit
    assert vt.post(f"/api/tasks/{tid}/archive").json()["archived_at"] is not None
    assert vt.post(f"/api/tasks/{tid}/restore").json()["archived_at"] is None
    det = vt.get(f"/api/tasks/{tid}").json()
    assert len(det["todos"]) == 6 and det["custom_values"][f_map["Scan văn bản"]] == scan_id
    tl = vt.get(f"/api/tasks/{tid}/timeline").json()
    assert {"task_created", "stage_entered", "todo_assigned", "todo_completed", "task_edited",
            "follower_added", "task_completed"} <= {e["action"] for e in tl["events"]}
    assert len(env.admin.get(f"/api/admin/audit?task_id={tid}&workflow_id={wf_cv_id}").json()) >= 10

    fo_notifs = fo.get("/api/notifications").json()["items"]
    assert {"follower_added", "task_edited", "task_completed"} <= {n["kind"] for n in fo_notifs}
    assert all(n["task_id"] == tid for n in fo_notifs)
    assert any(n["kind"] == "task_assigned" for n in gd.get("/api/notifications").json()["items"])
    assert any(n["kind"] == "task_created" for n in env.admin.get("/api/notifications").json()["items"])

    # Lọc /api/jobs theo ngày, sort, page và không thể xóa user đã có lịch sử W5 (409)
    today_iso = datetime.fromisoformat(done["created_at"]).date().isoformat()
    r_jobs = vt.get(
        f"/api/jobs?role=created&status=completed&workflow_id={wf_cv_id}"
        f"&created_from={today_iso}&created_to={today_iso}"
        f"&completed_from={today_iso}&completed_to={today_iso}&sort=-created_at&page=1&page_size=10"
    )
    assert r_jobs.status_code == 200 and [x["id"] for x in r_jobs.json()["items"]] == [tid]
    assert env.admin.delete(f"/api/admin/users/{bm2.uid}").status_code == 409

    # Đi nhanh QT Chấm công (không có form riêng)
    cc_t = bm1.post(f"/api/workflows/{wf_cc_id}/tasks", json={"title": "Chấm công T11"}).json()
    for actor in (bm1, bm2, gd):
        cur_td = next(x for x in cc_t["todos"] if x["status"] == "doing" and x["assignee"]["id"] == actor.uid)
        cc_t = actor.post(f"/api/todos/{cur_td['id']}/complete", json={}).json()
    assert cc_t["status"] == "completed"


def test_w5_fail_mid_flow_cancels_open_todos_and_requires_valid_reason(tmp_path):
    env = build_env(tmp_path, with_services=False)
    u1 = env.make_user("fail_u1")
    u2 = env.make_user("fail_u2")
    u3 = env.make_user("fail_u3")

    wf_other = env.admin.post("/api/workflows", json={
        "name": "Quy trình khác", "stages": [{"name": "B1", "member_ids": [u1.uid]}],
        "failure_reasons": [{"label": "Lý do khác"}],
    }).json()
    other_reason_id = wf_other["failure_reasons"][0]["id"]

    wf = env.admin.post("/api/workflows", json={
        "name": "Quy trình kiểm tra fail",
        "stages": [
            {"name": "Bước 1", "assign_mode": "all", "member_ids": [u1.uid, u2.uid], "sla_hours": 4},
            {"name": "Bước 2", "assign_mode": "any", "member_ids": [u3.uid], "sla_hours": 4},
        ],
        "roles": [{"user_id": u1.uid, "role": "creator"}],
        "failure_reasons": [{"label": "Thiếu chứng từ"}],
    }).json()
    reason_id = wf["failure_reasons"][0]["id"]

    task = u1.post(f"/api/workflows/{wf['id']}/tasks", json={"title": "Task lỗi giữa chừng"}).json()
    td_by_u = {td["assignee"]["id"]: td["id"] for td in task["todos"]}

    # Lý do thuộc workflow khác hoặc không tồn tại -> 400
    assert u1.post(f"/api/todos/{td_by_u[u1.uid]}/fail", json={"failure_reason_id": other_reason_id}).status_code == 400
    assert u1.post(f"/api/todos/{td_by_u[u1.uid]}/fail", json={"failure_reason_id": 999999}).status_code == 400

    # Fail hợp lệ -> task dừng, hủy todo còn mở của u2, không sinh todo bước 2
    rf = u1.post(f"/api/todos/{td_by_u[u1.uid]}/fail", json={"failure_reason_id": reason_id, "note": "Thiếu hóa đơn"})
    assert rf.status_code == 200, rf.text
    failed = rf.json()
    assert failed["status"] == "failed" and failed["failure_reason"]["id"] == reason_id
    by_u = {td["assignee"]["id"]: td["status"] for td in failed["todos"]}
    assert by_u == {u1.uid: "failed", u2.uid: "cancelled"}
    assert u2.post(f"/api/todos/{td_by_u[u2.uid]}/complete", json={}).status_code == 409
    assert any(n["kind"] == "task_failed" for n in u2.get("/api/notifications").json()["items"])
    assert {"todo_failed", "todo_cancelled", "task_failed"} <= {
        e["action"] for e in u1.get(f"/api/tasks/{task['id']}/timeline").json()["events"]
    }


def test_w5_assign_mode_any_vs_all_and_non_stage_member_rejected(tmp_path):
    from app.models import StageMember

    env = build_env(tmp_path, with_services=False)
    u1 = env.make_user("m_u1")
    u2 = env.make_user("m_u2")
    u3 = env.make_user("m_u3")
    outsider = env.make_user("m_out")

    wf = env.admin.post("/api/workflows", json={
        "name": "Kiểm tra any/all",
        "visibility": "all_visible",
        "stages": [
            {"name": "Bước ANY", "assign_mode": "any", "member_ids": [u1.uid, u2.uid], "sla_hours": 2},
            {"name": "Bước ALL", "assign_mode": "all", "member_ids": [u2.uid, u3.uid], "sla_hours": 3},
        ],
        "roles": [
            {"user_id": u1.uid, "role": "creator"},
            {"user_id": outsider.uid, "role": "viewer"},
        ],
    }).json()

    # Giao việc khi tạo task cho người ngoài stage_members -> 400
    assert u1.post(f"/api/workflows/{wf['id']}/tasks", json={
        "title": "Sai assignee", "assignee_ids": [outsider.uid],
    }).status_code == 400

    t = u1.post(f"/api/workflows/{wf['id']}/tasks", json={"title": "Kiểm tra chế độ"}).json()
    s1_by_u = {td["assignee"]["id"]: td["id"] for td in t["todos"]}

    # Outsider có quyền xem (viewer trong all_visible) nhưng không phải assignee -> 403
    assert outsider.post(f"/api/todos/{s1_by_u[u1.uid]}/complete", json={}).status_code == 403

    # Truyền next_assignee_ids là người ngoài stage_members -> 400 và rollback giữ nguyên Stage 1
    assert u1.post(f"/api/todos/{s1_by_u[u1.uid]}/complete", json={
        "next_assignee_ids": [outsider.uid],
    }).status_code == 400
    assert u1.get(f"/api/tasks/{t['id']}").json()["current_stage"]["order"] == 1

    # Stage 1 (mode=any): u1 xong -> đóng stage, hủy todo của u2, chuyển ngay sang Stage 2
    r_s1 = u1.post(f"/api/todos/{s1_by_u[u1.uid]}/complete", json={}).json()
    assert r_s1["current_stage"]["order"] == 2
    u2_s1_todo = next(x for x in r_s1["todos"] if x["stage_order"] == 1 and x["assignee"]["id"] == u2.uid)
    assert u2_s1_todo["status"] == "cancelled"
    assert u2.post(f"/api/todos/{u2_s1_todo['id']}/complete", json={}).status_code == 409

    # Stage 2 (mode=all): u2 xong nhưng u3 chưa xong -> vẫn đang processing ở Stage 2
    s2_by_u = {td["assignee"]["id"]: td["id"] for td in r_s1["todos"] if td["stage_order"] == 2}
    r_s2a = u2.post(f"/api/todos/{s2_by_u[u2.uid]}/complete", json={}).json()
    assert r_s2a["status"] == "processing" and r_s2a["current_stage"]["order"] == 2

    # Gỡ u3 khỏi stage_members trong DB -> không thể hoàn thành todo (403); khôi phục lại -> hoàn tất
    sf = env.app.state.session_factory
    db = sf()
    s2_id = wf["stages"][1]["id"]
    try:
        db.delete(db.get(StageMember, (s2_id, u3.uid)))
        db.commit()
    finally:
        db.close()
    assert u3.post(f"/api/todos/{s2_by_u[u3.uid]}/complete", json={}).status_code == 403

    db = sf()
    try:
        db.add(StageMember(stage_id=s2_id, user_id=u3.uid))
        db.commit()
    finally:
        db.close()
    r_done = u3.post(f"/api/todos/{s2_by_u[u3.uid]}/complete", json={})
    assert r_done.status_code == 200 and r_done.json()["status"] == "completed"


def test_w5_overdue_derived_status_with_simulated_clock(tmp_path):
    from app.models import Task

    env = build_env(tmp_path, with_services=False)
    u1 = env.make_user("od_u1")
    wf = env.admin.post("/api/workflows", json={
        "name": "SLA 2h",
        "stages": [{"name": "Bước 2h", "sla_hours": 2, "member_ids": [u1.uid]}],
        "roles": [{"user_id": u1.uid, "role": "creator"}],
    }).json()

    t = u1.post(f"/api/workflows/{wf['id']}/tasks", json={"title": "Việc có hạn 2h"}).json()
    tid, td_id = t["id"], t["todos"][0]["id"]
    assert t["is_overdue"] is False and t["todos"][0]["is_overdue"] is False
    assert u1.get("/api/jobs?status=overdue").json()["items"] == []
    assert u1.get("/api/todos?status=overdue").json()["items"] == []
    assert u1.get(f"/api/workflows/{wf['id']}/board").json()["stages"][0]["overdue_count"] == 0

    # Giả lập trôi 2h05p -> is_overdue=True mà DB status vẫn giữ "processing"
    env.clock.advance(2 * 3600 + 300)
    db = env.app.state.session_factory()
    try:
        assert db.get(Task, tid).status == "processing"
    finally:
        db.close()
    assert u1.get(f"/api/tasks/{tid}").json()["is_overdue"] is True
    assert [x["id"] for x in u1.get("/api/jobs?status=overdue").json()["items"]] == [tid]
    assert [x["id"] for x in u1.get("/api/todos?status=overdue").json()["items"]] == [td_id]
    assert u1.get(f"/api/workflows/{wf['id']}/board").json()["stages"][0]["overdue_count"] == 1

    # Hoàn thành -> không còn tính là quá hạn
    done = u1.post(f"/api/todos/{td_id}/complete", json={}).json()
    assert done["status"] == "completed" and done["is_overdue"] is False
    assert u1.get("/api/jobs?status=overdue").json()["items"] == []


def test_w5_dynamic_form_validation_all_types(tmp_path):
    env = build_env(tmp_path, with_services=False)
    u1 = env.make_user("fm_u1")
    u2 = env.make_user("fm_u2")
    u_off = env.make_user("fm_off")
    assert env.admin.post(f"/api/admin/users/{u_off.uid}/disable").status_code == 200

    wf = env.admin.post("/api/workflows", json={
        "name": "Form 7 kiểu",
        "stages": [{"name": "B1", "member_ids": [u1.uid]}],
        "roles": [{"user_id": u1.uid, "role": "creator"}],
        "form_fields": [
            {"label": "Chuỗi", "type": "text", "required": True},
            {"label": "Đoạn văn", "type": "textarea", "required": False},
            {"label": "Ngày", "type": "date", "required": True},
            {"label": "Số", "type": "number", "required": True},
            {"label": "Chọn", "type": "select", "required": True, "options": ["A", "B"]},
            {"label": "Người dùng", "type": "user", "required": True},
            {"label": "Đính kèm", "type": "file", "required": True},
        ],
    }).json()
    f_by_lbl = {f["label"]: str(f["id"]) for f in wf["form_fields"]}
    pdf_u1 = _upload_pdf(u1, "u1.pdf")
    pdf_u2 = _upload_pdf(u2, "u2.pdf")

    valid_vals = {
        f_by_lbl["Chuỗi"]: "  Giá trị hợp lệ  ",
        f_by_lbl["Đoạn văn"]: "Ghi chú dài",
        f_by_lbl["Ngày"]: "2026-12-31",
        f_by_lbl["Số"]: 42.5,
        f_by_lbl["Chọn"]: "B",
        f_by_lbl["Người dùng"]: u2.uid,
        f_by_lbl["Đính kèm"]: pdf_u1,
    }

    bad_payloads = [
        {k: v for k, v in valid_vals.items() if k != f_by_lbl["Chuỗi"]},            # thiếu required
        {**valid_vals, "key_la": "x"},                                            # key lạ
        {**valid_vals, f_by_lbl["Chuỗi"]: 123},                                   # text sai kiểu
        {**valid_vals, f_by_lbl["Số"]: "42"},                                     # number dạng chuỗi
        {**valid_vals, f_by_lbl["Số"]: True},                                     # bool không phải số
        {**valid_vals, f_by_lbl["Ngày"]: "20261231"},                             # date không đúng YYYY-MM-DD
        {**valid_vals, f_by_lbl["Ngày"]: "2026-02-30"},                             # date không tồn tại
        {**valid_vals, f_by_lbl["Chọn"]: "C"},                                    # ngoài options
        {**valid_vals, f_by_lbl["Người dùng"]: u_off.uid},                        # user bị vô hiệu hóa
        {**valid_vals, f_by_lbl["Người dùng"]: 999999},                           # user không tồn tại
        {**valid_vals, f_by_lbl["Đính kèm"]: pdf_u2},                             # file của người khác
        {**valid_vals, f_by_lbl["Đính kèm"]: "not-a-uuid"},                       # file id rác
    ]
    for bad in bad_payloads:
        r_bad = u1.post(f"/api/workflows/{wf['id']}/tasks", json={"title": "Test", "custom_values": bad})
        assert r_bad.status_code == 422, f"Phải từ chối {bad}: {r_bad.text}"

    r_ok = u1.post(f"/api/workflows/{wf['id']}/tasks", json={"title": "Hợp lệ", "custom_values": valid_vals})
    assert r_ok.status_code == 201, r_ok.text
    assert r_ok.json()["custom_values"][f_by_lbl["Chuỗi"]] == "Giá trị hợp lệ"


def test_w5_visibility_followers_only_vs_all_visible_across_jobs_board_export_search(tmp_path):
    from app.models import User

    env = build_env(tmp_path, with_services=False)
    creator = env.make_user("vis_creator")
    u1 = env.make_user("vis_s1")
    u2 = env.make_user("vis_s2")
    fo = env.make_user("vis_fo")
    wf_admin = env.make_user("vis_admin")
    sup = env.make_user("vis_sup")
    viewer = env.make_user("vis_viewer")
    c2 = env.make_user("vis_c2")
    outsider = env.make_user("vis_out")

    # Đưa u1, viewer, outsider vào cùng phòng ban để kiểm chứng role=team không làm rò rỉ task ẩn
    db = env.app.state.session_factory()
    try:
        for uid in (u1.uid, viewer.uid, outsider.uid):
            db.get(User, uid).department = "Phòng Kế toán"
        db.commit()
    finally:
        db.close()

    wf = env.admin.post("/api/workflows", json={
        "name": "Bảo mật visibility",
        "visibility": "followers_only",
        "stages": [
            {"name": "Bước 1", "member_ids": [u1.uid]},
            {"name": "Bước 2", "member_ids": [u2.uid]},
        ],
        "roles": [
            {"user_id": creator.uid, "role": "creator"},
            {"user_id": c2.uid, "role": "creator"},
            {"user_id": wf_admin.uid, "role": "admin"},
            {"user_id": sup.uid, "role": "supervisor"},
            {"user_id": viewer.uid, "role": "viewer"},
        ],
    }).json()
    wf_id = wf["id"]

    secret_title = "Hợp đồng mật M99"
    t = creator.post(f"/api/workflows/{wf_id}/tasks", json={
        "title": secret_title, "labels": ["bimat"], "follower_ids": [fo.uid],
    }).json()
    tid = t["id"]

    def _can_see(api_client) -> bool:
        by_id = api_client.get(f"/api/tasks/{tid}").status_code == 200
        by_tl = api_client.get(f"/api/tasks/{tid}/timeline").status_code == 200
        jobs = [x["id"] for x in api_client.get(f"/api/jobs?q={secret_title}&workflow_id={wf_id}").json()["items"]]
        team_jobs = [x["id"] for x in api_client.get("/api/jobs?role=team").json()["items"]]
        csv_txt = api_client.get(f"/api/jobs/export?q=M99&workflow_id={wf_id}").text
        board_res = api_client.get(f"/api/workflows/{wf_id}/board")
        todos_ids = [x["task_id"] for x in api_client.get("/api/todos?role=&status=").json()["items"]]
        if by_id:
            assert by_tl and tid in jobs and secret_title in csv_txt
            assert any(col["count"] >= 1 and any(x["id"] == tid for x in col["tasks"]) for col in board_res.json()["stages"])
            return True
        assert not by_tl and tid not in jobs and tid not in team_jobs
        assert secret_title not in csv_txt and tid not in todos_ids
        if board_res.status_code == 200:
            assert all(col["count"] == 0 and col["tasks"] == [] for col in board_res.json()["stages"])
        return False

    # Bước 1 (followers_only): creator, u1 (đang làm), fo, wf_admin, sup thấy; viewer, c2, u2 (bước 2), outsider KHÔNG thấy
    for allowed in (creator, u1, fo, wf_admin, sup):
        assert _can_see(allowed) is True
    for hidden in (viewer, c2, u2, outsider):
        assert _can_see(hidden) is False

    # Hoàn thành Bước 1 -> sang Bước 2: u2 (assignee hiện tại) thấy được, u1 (không còn giữ việc) không còn thấy
    u1_td = next(x["id"] for x in t["todos"] if x["assignee"]["id"] == u1.uid)
    assert u1.post(f"/api/todos/{u1_td}/complete", json={}).status_code == 200
    assert _can_see(u2) is True
    assert _can_see(u1) is False

    # Đổi workflow sang all_visible -> mọi user có role trong workflow đều thấy; outsider vẫn bị chặn
    assert wf_admin.request("PATCH", f"/api/workflows/{wf_id}", json={"visibility": "all_visible"}).status_code == 200
    assert _can_see(viewer) is True and _can_see(c2) is True
    assert _can_see(outsider) is False


def test_w5_permissions_admin_vs_supervisor_and_w6_sign_hook(tmp_path):
    from app.wf_hooks import StageHandler, register_stage_handler, reset_stage_handlers

    env = build_env(tmp_path, with_services=False)
    wf_admin = env.make_user("perm_adm")
    sup = env.make_user("perm_sup")
    creator = env.make_user("perm_cre")
    signer = env.make_user("perm_sig")

    # Chỉ system admin tạo được workflow (403)
    assert wf_admin.post("/api/workflows", json={"name": "No", "stages": [{"name": "S1", "member_ids": [signer.uid]}]}).status_code == 403

    wf = env.admin.post("/api/workflows", json={
        "name": "Phân quyền + W6 Hook",
        "stages": [
            {"name": "Bước nhập", "type": "normal", "member_ids": [creator.uid]},
            {"name": "Bước ký W6", "type": "sign", "member_ids": [signer.uid]},
        ],
        "roles": [
            {"user_id": wf_admin.uid, "role": "admin"},
            {"user_id": sup.uid, "role": "supervisor"},
            {"user_id": creator.uid, "role": "creator"},
        ],
    }).json()
    wf_id = wf["id"]

    # supervisor chỉ đọc
    assert sup.get(f"/api/workflows/{wf_id}").status_code == 200
    assert sup.get(f"/api/workflows/{wf_id}/board").status_code == 200
    assert sup.request("PATCH", f"/api/workflows/{wf_id}", json={"description": "Sửa lậu"}).status_code == 403
    assert sup.post(f"/api/workflows/{wf_id}/tasks", json={"title": "Tạo lậu"}).status_code == 403

    # workflow admin sửa được cấu trúc workflow
    r_patch = wf_admin.request("PATCH", f"/api/workflows/{wf_id}", json={"description": "Mô tả đã cập nhật"})
    assert r_patch.status_code == 200 and r_patch.json()["description"] == "Mô tả đã cập nhật"

    # Kiểm tra Task privileges của supervisor
    t = creator.post(f"/api/workflows/{wf_id}/tasks", json={"title": "Task hook W6"}).json()
    tid = t["id"]
    assert sup.get(f"/api/tasks/{tid}").status_code == 200
    assert sup.request("PATCH", f"/api/tasks/{tid}", json={"title": "Đổi tên"}).status_code == 403
    assert sup.post(f"/api/tasks/{tid}/archive").status_code == 403

    # Kiểm tra hook cho bước type=sign (W6)
    events: list[str] = []

    class MockW6SignHandler(StageHandler):
        def on_enter(self, ctx, todos):
            events.append(f"enter:{ctx.stage.type}:{len(todos)}")

        def before_complete(self, ctx, todo, payload):  # noqa: ARG002
            events.append(f"before:{ctx.task.document_id or 'none'}")
            if not payload.get("result") or payload["result"] != "signed":
                from fastapi import HTTPException
                raise HTTPException(409, "Chưa ký số tài liệu")

        def after_complete(self, ctx, todo, payload):  # noqa: ARG002
            events.append("after")

    try:
        register_stage_handler("sign", MockW6SignHandler())
        s1_td = t["todos"][0]["id"]
        t2 = creator.post(f"/api/todos/{s1_td}/complete", json={}).json()
        s2_td = next(x["id"] for x in t2["todos"] if x["stage_order"] == 2)
        assert events == ["enter:sign:1"]

        # Chưa đủ điều kiện ký -> before_complete chặn 409, chưa đánh dấu done
        assert signer.post(f"/api/todos/{s2_td}/complete", json={"result": "chưa ký"}).status_code == 409
        assert signer.get(f"/api/tasks/{tid}").json()["status"] == "processing"

        # Đủ điều kiện -> hoàn tất và chạy after_complete
        r_ok = signer.post(f"/api/todos/{s2_td}/complete", json={"result": "signed"})
        assert r_ok.status_code == 200 and r_ok.json()["status"] == "completed"
        assert events == ["enter:sign:1", "before:none", "before:none", "after"]
    finally:
        from app.wf_signing import register_w6_handlers

        reset_stage_handlers()
        register_w6_handlers()
