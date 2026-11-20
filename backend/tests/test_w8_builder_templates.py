"""Kiểm thử Milestone W8: Workflow Builder, quy tắc sửa cấu trúc khi có task, phân quyền, cảnh báo sign & 5 template nội bộ."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import inspect, select

from conftest import build_env


def _me_id(api) -> int:
    r = api.get("/api/auth/me")
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_w8_migration_idempotent_on_pre_w8_db(tmp_path):
    from app.models import FormField, Stage, Task, Workflow, WorkflowTemplate, init_db, make_engine, make_session_factory, migrate_db
    from app.wf_templates import seed_builtin_templates

    db_path = tmp_path / "pre_w8.db"
    eng = make_engine("sqlite:///" + str(db_path).replace("\\", "/"))

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
        """CREATE TABLE workflows (
            id INTEGER PRIMARY KEY, name VARCHAR(200) NOT NULL, slug VARCHAR(100) UNIQUE NOT NULL,
            category VARCHAR(100) NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
            guide_html TEXT NOT NULL DEFAULT '', visibility VARCHAR(20) NOT NULL DEFAULT 'followers_only',
            status VARCHAR(16) NOT NULL DEFAULT 'active', creator_id INTEGER NOT NULL REFERENCES users(id),
            created_at DATETIME NOT NULL
        )""",
        """CREATE TABLE stages (
            id INTEGER PRIMARY KEY, workflow_id INTEGER NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
            "order" INTEGER NOT NULL, name VARCHAR(200) NOT NULL,
            type VARCHAR(16) NOT NULL DEFAULT 'normal', sla_hours INTEGER,
            guide_html TEXT NOT NULL DEFAULT '', assign_mode VARCHAR(8) NOT NULL DEFAULT 'all'
        )""",
        """CREATE TABLE form_fields (
            id INTEGER PRIMARY KEY, workflow_id INTEGER NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
            label VARCHAR(200) NOT NULL, type VARCHAR(16) NOT NULL DEFAULT 'text',
            required BOOLEAN NOT NULL DEFAULT 0, placeholder VARCHAR(200) NOT NULL DEFAULT '',
            options TEXT, "order" INTEGER NOT NULL DEFAULT 1
        )""",
        """CREATE TABLE tasks (
            id INTEGER PRIMARY KEY, workflow_id INTEGER NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
            current_stage_id INTEGER REFERENCES stages(id), title VARCHAR(255) NOT NULL,
            labels TEXT NOT NULL DEFAULT '[]', creator_id INTEGER NOT NULL REFERENCES users(id),
            description_html TEXT NOT NULL DEFAULT '', custom_values TEXT NOT NULL DEFAULT '{}',
            deadline DATETIME, status VARCHAR(16) NOT NULL DEFAULT 'processing',
            failure_reason_id INTEGER, document_id VARCHAR(36),
            created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
            completed_at DATETIME, archived_at DATETIME
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
            "INSERT INTO workflows (id, name, slug, creator_id, created_at) "
            "VALUES (1, 'QT Tiền W8', 'qt-tien-w8', 1, '2026-01-01 00:00:00')"
        )
        con.exec_driver_sql(
            "INSERT INTO stages (id, workflow_id, \"order\", name, type) VALUES (1, 1, 1, 'B1 cũ', 'normal')"
        )
        con.exec_driver_sql(
            "INSERT INTO form_fields (id, workflow_id, label, type, \"order\") VALUES (1, 1, 'Trường cũ', 'text', 1)"
        )
        con.exec_driver_sql(
            "INSERT INTO tasks (id, workflow_id, current_stage_id, title, creator_id, created_at, updated_at) "
            "VALUES (1, 1, 1, 'Task cũ trước W8', 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        )

    init_db(eng)
    migrate_db(eng)  # gọi lần 2 phải idempotent

    insp = inspect(eng)
    assert "templates" in set(insp.get_table_names())
    assert "is_active" in {c["name"] for c in insp.get_columns("stages")}
    assert "is_active" in {c["name"] for c in insp.get_columns("form_fields")}

    sf = make_session_factory(eng)
    db = sf()
    try:
        assert db.get(Stage, 1).is_active is True
        assert db.get(FormField, 1).is_active is True
        assert db.get(Task, 1).title == "Task cũ trước W8"
        assert db.scalar(select(Workflow.slug).where(Workflow.id == 1)) == "qt-tien-w8"

        s1 = seed_builtin_templates(db)
        assert len(s1["created"]) == 5
        s2 = seed_builtin_templates(db)
        assert s2["created"] == [] and len(s2["skipped"]) == 5
        assert len(list(db.scalars(select(WorkflowTemplate)))) == 5
    finally:
        db.close()
    eng.dispose()


def test_w8_structural_edit_rules_with_active_and_historical_tasks(tmp_path):
    from app.models import AuditLog

    env = build_env(tmp_path, with_services=False)
    wf_admin = env.make_user("w8_wfadmin")
    u1 = env.make_user("w8_u1")
    u2 = env.make_user("w8_u2")
    u3 = env.make_user("w8_u3")

    rwf = env.admin.post(
        "/api/workflows",
        json={
            "name": "QT kiểm tra cấu trúc W8",
            "slug": "qt-cau-truc-w8",
            "stages": [
                {"name": "Bước 1", "type": "normal", "sla_hours": 8, "assign_mode": "all", "member_ids": [u1.uid, u2.uid]},
                {"name": "Bước 2", "type": "normal", "sla_hours": 12, "assign_mode": "any", "member_ids": [u2.uid]},
                {"name": "Bước 3", "type": "approve", "sla_hours": 4, "assign_mode": "any", "member_ids": [u3.uid]},
            ],
            "form_fields": [
                {"label": "Mã hồ sơ", "type": "text", "required": True, "placeholder": "VD: HS-01"},
                {"label": "Ghi chú thêm", "type": "number", "required": False, "placeholder": "Số"},
            ],
            "roles": [
                {"user_id": wf_admin.uid, "role": "admin"},
                {"user_id": u1.uid, "role": "creator"},
            ],
            "failure_reasons": [{"label": "Không đạt chuẩn"}],
        },
    )
    assert rwf.status_code == 201, rwf.text
    wf = rwf.json()
    wf_id = wf["id"]
    s1, s2, s3 = wf["stages"]
    f_code, f_num = wf["form_fields"]

    # Tạo T1 ở Bước 1 (deadline Stage 1 = +8h)
    r_t1 = u1.post(
        f"/api/workflows/{wf_id}/tasks",
        json={"title": "Task T1 cấu trúc", "custom_values": {str(f_code["id"]): "HS-2026-01"}},
    )
    assert r_t1.status_code == 201, r_t1.text
    t1 = r_t1.json()
    t1_id = t1["id"]
    old_t1_s1_deadlines = {td["id"]: td["deadline"] for td in t1["todos"] if td["stage_id"] == s1["id"]}

    # 1. Đổi tên, guide_html (XSS phải được sanitize), thêm thành viên, đổi sla_hours -> ĐƯỢC PHÉP
    r_p1 = wf_admin.request(
        "PATCH",
        f"/api/workflows/{wf_id}",
        json={
            "name": "QT cấu trúc W8 (v2)",
            "guide_html": "<p>Hướng dẫn mới</p><script>alert(1)</script>",
            "stages": [
                {
                    "id": s1["id"],
                    "name": "Bước 1 đã sửa",
                    "type": "normal",
                    "sla_hours": 24,
                    "assign_mode": "all",
                    "member_ids": [u1.uid, u2.uid, u3.uid],
                },
                {
                    "id": s2["id"],
                    "name": s2["name"],
                    "type": s2["type"],
                    "sla_hours": s2["sla_hours"],
                    "assign_mode": s2["assign_mode"],
                    "member_ids": [u2.uid],
                },
                {
                    "id": s3["id"],
                    "name": s3["name"],
                    "type": s3["type"],
                    "sla_hours": s3["sla_hours"],
                    "assign_mode": s3["assign_mode"],
                    "member_ids": [u3.uid],
                },
            ],
        },
    )
    assert r_p1.status_code == 200, r_p1.text
    assert "<script>" not in r_p1.json()["guide_html"]
    assert "Hướng dẫn mới" in r_p1.json()["guide_html"]

    # Todo đang mở của T1 KHÔNG bị tính lại deadline
    t1_after_sla = u1.get(f"/api/tasks/{t1_id}").json()
    for td in t1_after_sla["todos"]:
        if td["id"] in old_t1_s1_deadlines:
            assert td["deadline"] == old_t1_s1_deadlines[td["id"]]

    # Tạo T2 sau khi sửa SLA -> todo mới nhận sla_hours=24 mới
    env.clock.advance(60)
    r_t2 = u1.post(
        f"/api/workflows/{wf_id}/tasks",
        json={"title": "Task T2 SLA mới", "custom_values": {str(f_code["id"]): "HS-2026-02"}},
    )
    assert r_t2.status_code == 201, r_t2.text
    t2 = r_t2.json()
    assert t2["todos"][0]["deadline"] > t1_after_sla["todos"][0]["deadline"]
    # Cho T2 hoàn thành hết để chỉ còn T1 ở Bước 2 khi test chèn giữa
    for _ in range(5):
        t2_cur = wf_admin.get(f"/api/tasks/{t2['id']}").json()
        if t2_cur["status"] != "processing":
            break
        stype = t2_cur["current_stage"]["type"]
        for td in [x for x in t2_cur["todos"] if x["status"] == "doing"]:
            actor = {u1.uid: u1, u2.uid: u2, u3.uid: u3}[td["assignee"]["id"]]
            body = {"decision": "approve", "result": "OK"} if stype == "approve" else {"result": "OK"}
            assert actor.post(f"/api/todos/{td['id']}/complete", json=body).status_code == 200
    assert wf_admin.get(f"/api/tasks/{t2['id']}").json()["status"] == "completed"
    # T1 hoàn thành Bước 1 (u1, u2) -> chuyển vào Bước 2 (T1 là task processing duy nhất ở Bước 2)
    for actor, td in ((u1, next(x for x in t1_after_sla["todos"] if x["assignee"]["id"] == u1.uid)),
                      (u2, next(x for x in t1_after_sla["todos"] if x["assignee"]["id"] == u2.uid))):
        assert actor.post(f"/api/todos/{td['id']}/complete", json={}).status_code == 200
    assert wf_admin.get(f"/api/tasks/{t1_id}").json()["current_stage"]["id"] == s2["id"]

    # 2. Chèn stage:
    # - Chèn trước Bước 2 (nơi có T1 đang processing) -> 409
    r_ins_bad = wf_admin.post(
        f"/api/workflows/{wf_id}/stages",
        json={"name": "Chèn trước B2", "type": "normal", "sla_hours": 2, "member_ids": [u1.uid], "insert_after_order": 1},
    )
    assert r_ins_bad.status_code == 409, r_ins_bad.text
    # - Chèn sau Bước 2 (các bước sau Bước 2 không có task processing) -> 201
    r_ins_ok = wf_admin.post(
        f"/api/workflows/{wf_id}/stages",
        json={"name": "Bước trung gian chưa dùng", "type": "normal", "sla_hours": 2, "member_ids": [u1.uid], "insert_after_order": 2},
    )
    assert r_ins_ok.status_code == 201, r_ins_ok.text
    mid_stage = next(s for s in r_ins_ok.json()["stages"] if s["name"] == "Bước trung gian chưa dùng")
    assert mid_stage["order"] == 3
    # - Thêm vào cuối qua PATCH -> 200
    cur_wf = wf_admin.get(f"/api/workflows/{wf_id}").json()
    cur_stages_payload = [
        {"id": s["id"], "name": s["name"], "type": s["type"], "sla_hours": s["sla_hours"],
         "assign_mode": s["assign_mode"], "member_ids": [m["id"] for m in s["members"]]}
        for s in cur_wf["stages"] if s["is_active"]
    ]
    cur_stages_payload.append({"name": "Bước cuối mới", "type": "normal", "sla_hours": 2, "assign_mode": "any", "member_ids": [u1.uid]})
    assert wf_admin.request("PATCH", f"/api/workflows/{wf_id}", json={"stages": cur_stages_payload}).status_code == 200

    # 3. Đổi thứ tự & đổi type khi có task đang processing -> 409
    cur_wf = wf_admin.get(f"/api/workflows/{wf_id}").json()
    active_ids = [s["id"] for s in cur_wf["stages"] if s["is_active"]]
    r_reord_bad = wf_admin.put(f"/api/workflows/{wf_id}/stages/reorder", json={"stage_ids": list(reversed(active_ids))})
    assert r_reord_bad.status_code == 409, r_reord_bad.text

    r_type_bad = wf_admin.request("PATCH", f"/api/workflows/{wf_id}/stages/{s3['id']}", json={"type": "sign"})
    assert r_type_bad.status_code == 409, r_type_bad.text

    # 4. Xóa thành viên đang có todo doing ở Bước 2 (u2) -> 409; thêm u1 vào Bước 2 rồi xóa u1 (rảnh) -> 200
    assert wf_admin.delete(f"/api/workflows/{wf_id}/stages/{s2['id']}/members/{u2.uid}").status_code == 409
    assert wf_admin.post(f"/api/workflows/{wf_id}/stages/{s2['id']}/members", json={"user_ids": [u1.uid]}).status_code == 200
    assert wf_admin.delete(f"/api/workflows/{wf_id}/stages/{s2['id']}/members/{u1.uid}").status_code == 200

    # 5. Xóa stage đang có task processing (Bước 2) -> 409; xóa stage chưa từng dùng -> xóa hẳn & đánh số lại order
    assert wf_admin.delete(f"/api/workflows/{wf_id}/stages/{s2['id']}").status_code == 409
    assert wf_admin.delete(f"/api/workflows/{wf_id}/stages/{mid_stage['id']}").status_code == 200
    wf_after_del = wf_admin.get(f"/api/workflows/{wf_id}").json()
    assert mid_stage["id"] not in {s["id"] for s in wf_after_del["stages"]}
    active_orders = [s["order"] for s in wf_after_del["stages"] if s["is_active"]]
    assert active_orders == list(range(1, len(active_orders) + 1))

    # Cho T1 hoàn tất hết các bước còn lại -> 0 task processing
    for _ in range(5):
        t1_now = wf_admin.get(f"/api/tasks/{t1_id}").json()
        if t1_now["status"] != "processing":
            break
        stype = t1_now["current_stage"]["type"]
        doing_td = next(td for td in t1_now["todos"] if td["status"] == "doing")
        actor = {u1.uid: u1, u2.uid: u2, u3.uid: u3}[doing_td["assignee"]["id"]]
        body = {"decision": "approve", "result": "Xong"} if stype == "approve" else {"result": "Xong"}
        assert actor.post(f"/api/todos/{doing_td['id']}/complete", json=body).status_code == 200
    assert wf_admin.get(f"/api/tasks/{t1_id}").json()["status"] == "completed"

    # 6. Khi không còn processing:
    # - Xóa Bước 2 (đã có lịch sử từ T1/T2) -> CHUYỂN THÀNH is_active=False (không xóa mất lịch sử)
    r_deact_s2 = wf_admin.delete(f"/api/workflows/{wf_id}/stages/{s2['id']}")
    assert r_deact_s2.status_code == 200, r_deact_s2.text
    s2_obj = next(s for s in r_deact_s2.json()["stages"] if s["id"] == s2["id"])
    assert s2_obj["is_active"] is False
    # Board không hiển thị stage vô hiệu, nhưng chi tiết task cũ T1 vẫn giữ đủ giai đoạn
    board_ids = [col["stage"]["id"] for col in wf_admin.get(f"/api/workflows/{wf_id}/board").json()["stages"]]
    assert s2["id"] not in board_ids
    assert any(st["id"] == s2["id"] for st in wf_admin.get(f"/api/tasks/{t1_id}").json()["stages"])

    # - Đổi thứ tự stage active & đổi type stage -> 200
    rem_active = [s["id"] for s in r_deact_s2.json()["stages"] if s["is_active"]]
    assert wf_admin.put(f"/api/workflows/{wf_id}/stages/reorder", json={"stage_ids": list(reversed(rem_active))}).status_code == 200
    assert wf_admin.request("PATCH", f"/api/workflows/{wf_id}/stages/{s3['id']}", json={"type": "normal"}).status_code == 200

    # 7. Quy tắc form_fields:
    # - Tạo task T_old trước khi trường mới bắt buộc được thêm
    t_old = u1.post(
        f"/api/workflows/{wf_id}/tasks",
        json={"title": "Task trước field mới", "custom_values": {str(f_code["id"]): "HS-OLD"}},
    ).json()
    # - Thêm trường mới required=True -> PATCH title của task cũ vẫn OK, nhưng tạo task mới thiếu trường mới -> 422
    r_add_req = wf_admin.post(
        f"/api/workflows/{wf_id}/form-fields",
        json={"label": "Trường bắt buộc mới", "type": "text", "required": True},
    )
    assert r_add_req.status_code == 201, r_add_req.text
    assert u1.request("PATCH", f"/api/tasks/{t_old['id']}", json={"title": "Task cũ sửa tên"}).status_code == 200
    r_new_missing = u1.post(
        f"/api/workflows/{wf_id}/tasks",
        json={"title": "Task thiếu field mới", "custom_values": {str(f_code["id"]): "HS-NEW"}},
    )
    assert r_new_missing.status_code == 422, r_new_missing.text

    # - Trường f_code đã có dữ liệu: đổi kiểu hoặc xóa -> 409; chuyển is_active=False -> 200
    assert wf_admin.request("PATCH", f"/api/workflows/{wf_id}/form-fields/{f_code['id']}", json={"type": "number"}).status_code == 409
    assert wf_admin.delete(f"/api/workflows/{wf_id}/form-fields/{f_code['id']}").status_code == 409
    r_deact_f = wf_admin.request(
        "PATCH", f"/api/workflows/{wf_id}/form-fields/{f_code['id']}", json={"is_active": False}
    )
    assert r_deact_f.status_code == 200, r_deact_f.text
    # T1 cũ vẫn giữ và hiển thị giá trị của trường đã vô hiệu
    t1_form_map = {x["field_id"]: x["value"] for x in u1.get(f"/api/tasks/{t1_id}").json()["form"]}
    assert t1_form_map[f_code["id"]] == "HS-2026-01"
    # Trường chưa có dữ liệu (f_num) -> xóa được (200)
    assert wf_admin.delete(f"/api/workflows/{wf_id}/form-fields/{f_num['id']}").status_code == 200

    # 8. Đóng & mở lại quy trình
    r_close = wf_admin.post(f"/api/workflows/{wf_id}/close")
    assert r_close.status_code == 200 and r_close.json()["status"] == "closed"
    new_req_fid = next(f["id"] for f in r_close.json()["form_fields"] if f["label"] == "Trường bắt buộc mới")
    # Khi đã đóng: không tạo được task mới (409), nhưng T_old đang chạy vẫn hoàn tất được
    r_blocked_new = u1.post(
        f"/api/workflows/{wf_id}/tasks",
        json={"title": "Bị chặn do đóng", "custom_values": {str(new_req_fid): "X"}},
    )
    assert r_blocked_new.status_code == 409
    for _ in range(5):
        st_old = wf_admin.get(f"/api/tasks/{t_old['id']}").json()
        if st_old["status"] != "processing":
            break
        td_cur = next(td for td in st_old["todos"] if td["status"] == "doing")
        actor = {u1.uid: u1, u2.uid: u2, u3.uid: u3}[td_cur["assignee"]["id"]]
        assert actor.post(f"/api/todos/{td_cur['id']}/complete", json={"result": "Xong khi đóng"}).status_code == 200
    assert wf_admin.get(f"/api/tasks/{t_old['id']}").json()["status"] == "completed"

    # Mở lại -> tạo task bình thường
    r_reopen = wf_admin.post(f"/api/workflows/{wf_id}/reopen")
    assert r_reopen.status_code == 200 and r_reopen.json()["status"] == "active"
    r_after_reopen = u1.post(
        f"/api/workflows/{wf_id}/tasks",
        json={"title": "Tạo sau khi mở lại", "custom_values": {str(new_req_fid): "OK"}},
    )
    assert r_after_reopen.status_code == 201, r_after_reopen.text

    # 9. Audit log phải có workflow_id, actor và diff cho các lần sửa cấu trúc/đóng/mở lại
    sf = env.app.state.session_factory
    db = sf()
    try:
        logs = list(db.scalars(select(AuditLog).where(AuditLog.workflow_id == wf_id).order_by(AuditLog.id)))
        actions = {a.action for a in logs}
        assert {"workflow_created", "workflow_updated", "workflow_closed", "workflow_reopened"} <= actions
        upd = next(a for a in logs if a.action == "workflow_updated" and a.user_id == wf_admin.uid)
        assert "diff" in (upd.metadata_json or "")
    finally:
        db.close()


def test_w8_permissions_validation_and_sign_warnings(tmp_path):
    from app.models import Certificate, UserCertificate

    env = build_env(tmp_path, with_services=False)
    wf_admin = env.make_user("w8p_admin")
    sup = env.make_user("w8p_sup")
    no_cert_signer = env.make_user("w8p_nocert", cert_keys=(), pin=None)
    ready_signer = env.make_user("w8p_ready", cert_keys=(), pin="268134")
    outsider = env.make_user("w8p_out")

    sf = env.app.state.session_factory
    db = sf()
    try:
        cert = Certificate(key_ref="alice", subject="CN=Alice", serial="123", not_after="2030-01-01", active=True)
        db.add(cert)
        db.flush()
        db.add(UserCertificate(user_id=ready_signer.uid, cert_id=cert.id))
        db.commit()
    finally:
        db.close()

    # Tạo quy trình hợp lệ + 1 bước sign có thành viên chưa có cert/PIN -> trả về warnings không chặn lưu
    rwf = env.admin.post(
        "/api/workflows",
        json={
            "name": "QT Cảnh báo Sign",
            "stages": [
                {"name": "Chuẩn bị", "type": "normal", "sla_hours": 4, "member_ids": [ready_signer.uid]},
                {"name": "Ký số", "type": "sign", "sla_hours": 4, "member_ids": [no_cert_signer.uid]},
            ],
            "roles": [
                {"user_id": wf_admin.uid, "role": "admin"},
                {"user_id": sup.uid, "role": "supervisor"},
            ],
        },
    )
    assert rwf.status_code == 201, rwf.text
    wf = rwf.json()
    wf_id = wf["id"]
    warn_codes = {w["code"] for w in wf["warnings"]}
    assert "missing_cert" in warn_codes and "missing_pin" in warn_codes

    # Supervisor chỉ đọc -> 403 trên mọi mutation W8
    assert sup.get(f"/api/workflows/{wf_id}").status_code == 200
    assert sup.post(f"/api/workflows/{wf_id}/close").status_code == 403
    assert sup.post(f"/api/workflows/{wf_id}/clone").status_code == 403
    assert sup.post(f"/api/workflows/{wf_id}/save-as-template").status_code == 403
    assert sup.post(f"/api/workflows/{wf_id}/stages", json={"name": "Lậu", "member_ids": [sup.uid]}).status_code == 403
    assert sup.post(f"/api/workflows/{wf_id}/roles", json={"user_id": outsider.uid, "role": "viewer"}).status_code == 403
    assert outsider.get(f"/api/workflows/{wf_id}").status_code == 404

    # Validation cấu trúc (workflow admin thao tác)
    assert wf_admin.post(f"/api/workflows/{wf_id}/stages", json={"name": "Chuẩn bị", "member_ids": [ready_signer.uid]}).status_code == 400
    assert env.admin.post("/api/workflows", json={"name": "SLA 0", "stages": [{"name": "B1", "sla_hours": 0, "member_ids": [ready_signer.uid]}]}).status_code == 422
    assert env.admin.post("/api/workflows", json={"name": "Sign rỗng", "stages": [{"name": "Ký", "type": "sign", "member_ids": []}]}).status_code == 400

    # Đổi người ký sang ready_signer (đã có cert + PIN) -> hết cảnh báo
    sign_stage_id = wf["stages"][1]["id"]
    assert wf_admin.post(f"/api/workflows/{wf_id}/stages/{sign_stage_id}/members", json={"user_ids": [ready_signer.uid]}).status_code == 200
    r_rm_nocert = wf_admin.delete(f"/api/workflows/{wf_id}/stages/{sign_stage_id}/members/{no_cert_signer.uid}")
    assert r_rm_nocert.status_code == 200 and r_rm_nocert.json()["warnings"] == []


def test_w8_builtin_templates_from_template_clone_and_save_as_template(tmp_path):
    env = build_env(tmp_path, with_services=False)
    u1 = env.make_user("w8t_u1")
    u2 = env.make_user("w8t_u2")

    # 1. Startup create_app() phải seed sẵn đủ 5 mẫu nội bộ
    tpl_res = u1.get("/api/templates")
    assert tpl_res.status_code == 200, tpl_res.text
    tpls = tpl_res.json()["items"]
    slugs = {t["slug"] for t in tpls}
    assert {
        "tpl-cong-van-den",
        "tpl-cong-van-di",
        "tpl-cham-cong",
        "tpl-trinh-ky-hop-dong-ho-so",
        "tpl-phe-duyet-thanh-toan",
    } <= slugs

    # 2. Tạo workflow từ mẫu "Chấm công" (tổng hợp -> trưởng BP duyệt -> Giám đốc duyệt) và chạy hết luồng
    tpl_cc = next(t for t in tpls if t["slug"] == "tpl-cham-cong")
    r_from = env.admin.post(
        "/api/workflows/from-template",
        json={
            "template_id": tpl_cc["id"],
            "name": "Chấm công T11 (từ mẫu)",
            "admin_ids": [u1.uid],
            "stage_members": {"1": [u1.uid], "2": [u2.uid], "3": [u2.uid]},
        },
    )
    assert r_from.status_code == 201, r_from.text
    wf = r_from.json()
    f_map = {f["label"]: str(f["id"]) for f in wf["form_fields"]}
    r_task = env.admin.post(
        f"/api/workflows/{wf['id']}/tasks",
        json={
            "title": "Bảng công tháng 11/2026",
            "custom_values": {f_map["Kỳ chấm công (tháng/năm)"]: "11/2026", f_map["Khối / Phòng ban"]: "Phòng Cầu"},
        },
    )
    assert r_task.status_code == 201, r_task.text
    tid = r_task.json()["id"]
    for actor in (u1, u2, u2):
        cur = actor.get(f"/api/tasks/{tid}").json()
        td = next(x for x in cur["todos"] if x["status"] == "doing")
        body = {"decision": "approve", "result": "OK"} if cur["current_stage"]["type"] == "approve" else {"result": "OK"}
        assert actor.post(f"/api/todos/{td['id']}/complete", json=body).status_code == 200
    assert u1.get(f"/api/tasks/{tid}").json()["status"] == "completed"

    # 3. Clone workflow -> nhân bản cấu trúc, KHÔNG copy task/todo cũ
    r_clone = u1.post(f"/api/workflows/{wf['id']}/clone", json={"name": "Chấm công T12 (Bản sao)"})
    assert r_clone.status_code == 201, r_clone.text
    cloned = r_clone.json()
    assert cloned["id"] != wf["id"] and len(cloned["stages"]) == len(wf["stages"])
    assert u1.get(f"/api/jobs?workflow_id={cloned['id']}").json()["total"] == 0

    # 4. Save as template -> sinh mẫu mới is_builtin=False và dùng tạo tiếp workflow được
    r_save_tpl = u1.post(
        f"/api/workflows/{wf['id']}/save-as-template",
        json={"name": "Mẫu chấm công tùy biến", "category": "Nhân sự", "description": "Mẫu lưu lại"},
    )
    assert r_save_tpl.status_code == 201, r_save_tpl.text
    custom_tpl = r_save_tpl.json()
    assert custom_tpl["is_builtin"] is False
    # Tạo workflow từ mẫu chưa gán thành viên -> vẫn cho tạo kèm cảnh báo sign_stage_no_members ở mẫu có sign
    tpl_pay = next(t for t in tpls if t["slug"] == "tpl-phe-duyet-thanh-toan")
    r_pay = env.admin.post("/api/workflows/from-template", json={"template_id": tpl_pay["id"], "name": "Thanh toán nháp"})
    assert r_pay.status_code == 201, r_pay.text
    assert any(w["code"] == "sign_stage_no_members" for w in r_pay.json()["warnings"])
