"""Kiểm thử Playwright E2E cho Milestone W8: Workflow Builder, Template Gallery, xung đột cấu trúc 409, thành viên và đóng/mở quy trình."""
from __future__ import annotations

import os
import socket
import threading

import pytest
import uvicorn

from conftest import ADMIN_PASS, ADMIN_USER, ROOT, TEST_DATA, TEST_SIGN_PIN, USER_PASS, build_env
from test_w7_wfengine_playwright import _launch_browser, _make_spa_asgi, _open_local_session, _wait_for_port
from test_workflow_signing import _local_real_signer

FRONTEND_DIST = os.path.join(ROOT, "frontend", "dist")


@pytest.mark.skipif(
    not os.path.isfile(os.path.join(FRONTEND_DIST, "index.html")),
    reason="Cần frontend/dist đã build (npm run build)",
)
def test_w8_builder_templates_and_rules_playwright(tmp_path):
    """E2E W8 qua trình duyệt thật (SPA + pyHanko thật): Builder 3 bước -> tạo & ký task -> mẫu -> 409 -> people -> close/reopen."""
    from playwright.sync_api import expect, sync_playwright

    with _local_real_signer(tmp_path) as (signer_url, tok, _pub_dir):
        env = build_env(tmp_path / "app", with_services=False, signer_url=signer_url, signer_token=tok)
        env.register_certs("bob")
        staff = env.make_user("w8_staff", pin=TEST_SIGN_PIN)
        gd = env.make_user("w8_gd", cert_keys=["bob"], pin="268134")

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        config = uvicorn.Config(_make_spa_asgi(env.app), host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)
        t = threading.Thread(target=server.run, daemon=True)
        t.start()
        try:
            _wait_for_port("127.0.0.1", port)
            base_url = f"http://127.0.0.1:{port}"

            with sync_playwright() as p:
                browser = _launch_browser(p)
                sessions = {}
                try:
                    sessions["admin"] = _open_local_session(
                        browser, base_url, env.clock,
                        username=ADMIN_USER, password=ADMIN_PASS, secret=env.admin.secret,
                    )
                    sessions["staff"] = _open_local_session(
                        browser, base_url, env.clock,
                        username="w8_staff", password=USER_PASS, secret=staff.secret,
                    )
                    sessions["gd"] = _open_local_session(
                        browser, base_url, env.clock,
                        username="w8_gd", password=USER_PASS, secret=gd.secret,
                    )

                    p_adm = sessions["admin"][1]
                    p_stf = sessions["staff"][1]
                    p_gd = sessions["gd"][1]

                    # 1. Admin mở Quản lý quy trình -> Tạo quy trình 3 bước (normal -> sign -> normal)
                    p_adm.get_by_test_id("nav-wf-workflows").click()
                    p_adm.get_by_test_id("wf-create-btn").click()
                    p_adm.get_by_test_id("workflow-builder-view").wait_for(state="visible", timeout=10000)

                    p_adm.get_by_test_id("wf-builder-name").fill("Quy trình W8 E2E")
                    p_adm.get_by_test_id("wf-builder-slug").fill("qt-w8-e2e")
                    p_adm.get_by_test_id("wf-builder-category").fill("Kiểm thử W8")
                    p_adm.get_by_test_id("wf-builder-guide-input").fill("<b>Hướng dẫn W8</b>")
                    p_adm.get_by_test_id("wf-builder-guide-preview-toggle").click()
                    expect(p_adm.get_by_test_id("wf-builder-guide-preview")).to_contain_text("Hướng dẫn W8")

                    # Giai đoạn 0: normal -> w8_staff
                    p_adm.get_by_test_id("stage-name-0").fill("Tiếp nhận hồ sơ")
                    p_adm.get_by_test_id("stage-sla-0").fill("4")
                    p_adm.get_by_test_id("stage-member-0-w8_staff").check()

                    # Giai đoạn 1: sign -> w8_gd
                    p_adm.get_by_test_id("stage-add-btn").click()
                    p_adm.get_by_test_id("stage-name-1").fill("Giám đốc ký số")
                    p_adm.get_by_test_id("stage-type-1").select_option("sign")
                    p_adm.get_by_test_id("stage-sla-1").fill("2")
                    p_adm.get_by_test_id("stage-member-1-w8_gd").check()

                    # Giai đoạn 2: normal -> w8_staff
                    p_adm.get_by_test_id("stage-add-btn").click()
                    p_adm.get_by_test_id("stage-name-2").fill("Lưu sổ & hoàn tất")
                    p_adm.get_by_test_id("stage-sla-2").fill("1")
                    p_adm.get_by_test_id("stage-member-2-w8_staff").check()

                    # Thêm 2 trường form động & kiểm tra live preview
                    p_adm.get_by_test_id("field-add-btn").click()
                    p_adm.get_by_test_id("field-label-0").fill("Số văn bản")
                    p_adm.get_by_test_id("field-required-0").check()

                    p_adm.get_by_test_id("field-add-btn").click()
                    p_adm.get_by_test_id("field-label-1").fill("Tệp trình ký")
                    p_adm.get_by_test_id("field-type-1").select_option("file")
                    p_adm.get_by_test_id("field-required-1").check()
                    expect(p_adm.get_by_test_id("form-builder-preview")).to_contain_text("Số văn bản")
                    expect(p_adm.get_by_test_id("form-builder-preview")).to_contain_text("Tệp trình ký")

                    # Gán quyền creator cho w8_staff + lý do thất bại
                    p_adm.get_by_test_id("wf-role-user-select").select_option(str(staff.uid))
                    p_adm.get_by_test_id("wf-role-kind-select").select_option("creator")
                    p_adm.get_by_test_id("wf-role-add-btn").click()

                    p_adm.get_by_test_id("wf-reason-add-btn").click()
                    p_adm.get_by_test_id("wf-reason-input-0").fill("Thiếu hồ sơ gốc")

                    p_adm.get_by_test_id("wf-builder-save-active").click()
                    p_adm.get_by_test_id("wf-kanban-board").wait_for(state="visible", timeout=15000)
                    expect(p_adm.get_by_test_id("wf-kanban-board")).to_contain_text("Tiếp nhận hồ sơ")
                    expect(p_adm.get_by_test_id("wf-kanban-board")).to_contain_text("Giám đốc ký số")
                    wf1_id = int(p_adm.url.rstrip("/").split("/")[-1])

                    # 2. w8_staff tạo task, điền form động & hoàn thành bước 1 -> w8_gd ký PIN thật -> w8_staff hoàn tất
                    p_stf.get_by_test_id("nav-wf-workflows").click()
                    p_stf.get_by_test_id("wf-card-qt-w8-e2e").wait_for(state="visible", timeout=10000)
                    p_stf.get_by_test_id("wf-card-qt-w8-e2e").click()
                    p_stf.get_by_test_id("board-create-task-btn").click()
                    p_stf.get_by_test_id("create-task-modal").wait_for(state="visible")
                    p_stf.get_by_test_id("create-task-title").fill("Hồ sơ W8 cần ký")
                    # Lấy field IDs từ API qua UI detail không cần đoán: form input thứ tự render trong dyn-input-*
                    wf1_detail = env.admin.get(f"/api/workflows/{wf1_id}").json()
                    f_id_map = {f["label"]: f["id"] for f in wf1_detail["form_fields"]}
                    p_stf.get_by_test_id(f"dyn-input-{f_id_map['Số văn bản']}").fill("88/W8-E2E")
                    with p_stf.expect_response(lambda r: r.url.endswith("/api/files") and r.request.method == "POST"):
                        p_stf.get_by_test_id(f"dyn-input-{f_id_map['Tệp trình ký']}").set_input_files(
                            os.path.join(TEST_DATA, "sample.pdf")
                        )
                    p_stf.get_by_test_id("create-task-submit").click()
                    p_stf.get_by_test_id("task-detail-view").wait_for(state="visible", timeout=15000)
                    task1_id = int(p_stf.url.rstrip("/").split("/")[-1])

                    p_stf.get_by_test_id("complete-todo-result").fill("Chuyển ký")
                    p_stf.get_by_test_id("complete-todo-submit").click()
                    expect(p_stf.get_by_test_id("task-current-stage")).to_contain_text("Giám đốc ký số", timeout=15000)

                    # Giám đốc ký bước 2
                    p_gd.get_by_test_id("nav-wf-jobs").click()
                    p_gd.get_by_test_id(f"job-row-{task1_id}").wait_for(state="visible", timeout=10000)
                    p_gd.get_by_test_id(f"job-row-{task1_id}").click()
                    p_gd.get_by_test_id("task-sign-panel").wait_for(state="visible", timeout=15000)
                    p_gd.get_by_test_id("pdf-canvas").wait_for(state="visible", timeout=15000)
                    p_gd.get_by_test_id("wf-task-sign-level").select_option("B-B")
                    p_gd.get_by_test_id("wf-task-sign-reason").fill("Ký duyệt W8")
                    p_gd.get_by_test_id("wf-task-sign-btn").click()
                    p_gd.get_by_test_id("pin-modal-input").wait_for(state="visible")
                    p_gd.get_by_test_id("pin-modal-input").fill("268134")
                    with p_gd.expect_response(lambda r: r.url.endswith(f"/api/tasks/{task1_id}/sign") and r.request.method == "POST") as s_res:
                        p_gd.get_by_test_id("pin-modal-input").press("Enter")
                    assert s_res.value.status == 200
                    p_gd.get_by_test_id("pin-modal").wait_for(state="detached", timeout=15000)

                    # Staff hoàn tất bước 3
                    p_stf.reload(wait_until="networkidle")
                    expect(p_stf.get_by_test_id("task-current-stage")).to_contain_text("Lưu sổ & hoàn tất", timeout=15000)
                    p_stf.get_by_test_id("complete-todo-submit").click()
                    expect(p_stf.get_by_test_id("task-detail-status")).to_have_text("Hoàn thành", timeout=15000)

                    # 3. Admin mở Thư viện mẫu -> xem Công văn đi -> dùng mẫu Chấm công -> lưu -> tạo task đang chạy
                    p_adm.get_by_test_id("nav-wf-workflows").click()
                    p_adm.get_by_test_id("wf-open-templates-btn").click()
                    p_adm.get_by_test_id("template-gallery-modal").wait_for(state="visible")
                    p_adm.get_by_test_id("tpl-item-tpl-cong-van-di").click()
                    expect(p_adm.get_by_test_id("tpl-preview-pane")).to_contain_text("Giám đốc ký số ban hành")

                    p_adm.get_by_test_id("tpl-item-tpl-cham-cong").click()
                    p_adm.get_by_test_id("tpl-use-btn").click()
                    p_adm.get_by_test_id("workflow-builder-view").wait_for(state="visible", timeout=10000)
                    p_adm.get_by_test_id("wf-builder-name").fill("Chấm công W8 từ mẫu")
                    p_adm.get_by_test_id("stage-member-0-w8_staff").check()
                    p_adm.get_by_test_id("stage-member-1-w8_gd").check()
                    p_adm.get_by_test_id("stage-member-2-w8_gd").check()
                    p_adm.get_by_test_id("wf-builder-save-active").click()
                    p_adm.get_by_test_id("wf-kanban-board").wait_for(state="visible", timeout=15000)
                    wf2_id = int(p_adm.url.rstrip("/").split("/")[-1])

                    # Tạo 1 task đang processing ở bước 1 (w8_staff đang giữ todo doing)
                    wf2_detail = env.admin.get(f"/api/workflows/{wf2_id}").json()
                    f2_map = {f["label"]: f["id"] for f in wf2_detail["form_fields"]}
                    p_adm.get_by_test_id("board-create-task-btn").click()
                    p_adm.get_by_test_id("create-task-modal").wait_for(state="visible")
                    p_adm.get_by_test_id("create-task-title").fill("Chấm công đang chạy")
                    p_adm.get_by_test_id(f"dyn-input-{f2_map['Kỳ chấm công (tháng/năm)']}").fill("11/2026")
                    p_adm.get_by_test_id(f"dyn-input-{f2_map['Khối / Phòng ban']}").fill("Kỹ thuật")
                    p_adm.get_by_test_id("create-task-submit").click()
                    p_adm.get_by_test_id("task-detail-view").wait_for(state="visible", timeout=15000)

                    # 4. Thử đổi thứ tự stage khi đang có task processing -> hiển thị lỗi 409 từ server
                    p_adm.get_by_test_id("wf-topbar-back").click()
                    p_adm.get_by_test_id("wf-topbar-menu-btn").click()
                    p_adm.get_by_test_id("board-menu-edit").click()
                    p_adm.get_by_test_id("workflow-builder-view").wait_for(state="visible", timeout=10000)
                    p_adm.get_by_test_id("stage-down-0").click()
                    p_adm.get_by_test_id("wf-builder-save").click()
                    p_adm.get_by_test_id("wf-builder-error").wait_for(state="visible", timeout=10000)
                    expect(p_adm.get_by_test_id("wf-builder-error")).to_contain_text("không được đổi thứ tự", ignore_case=True)

                    # 5. Mở trang Thành viên -> thử gỡ thành viên có todo doing (409), thêm/gỡ role (200)
                    p_adm.get_by_test_id("wf-builder-open-people").click()
                    p_adm.get_by_test_id("workflow-people-view").wait_for(state="visible", timeout=10000)
                    s1_id = wf2_detail["stages"][0]["id"]
                    p_adm.get_by_test_id(f"people-remove-member-{s1_id}-{staff.uid}").click()
                    p_adm.get_by_test_id("wf-people-error").wait_for(state="visible", timeout=10000)
                    p_adm.get_by_test_id("people-role-user").select_option(str(gd.uid))
                    p_adm.get_by_test_id("people-role-kind").select_option("viewer")
                    p_adm.get_by_test_id("people-add-role-btn").click()
                    p_adm.get_by_test_id("wf-people-msg").wait_for(state="visible", timeout=10000)
                    p_adm.get_by_test_id(f"people-remove-role-{gd.uid}-viewer").click()

                    # 6. Quay lại Board -> Đóng & Mở lại quy trình qua menu ⋯
                    p_adm.get_by_test_id("wf-topbar-back").click()
                    p_adm.get_by_test_id("wf-kanban-board").wait_for(state="visible", timeout=10000)
                    p_adm.get_by_test_id("wf-topbar-menu-btn").click()
                    p_adm.get_by_test_id("board-menu-close").click()
                    p_adm.get_by_test_id("board-closed-banner").wait_for(state="visible", timeout=10000)
                    expect(p_adm.get_by_test_id("board-create-task-btn")).to_have_count(0)

                    p_adm.get_by_test_id("wf-topbar-menu-btn").click()
                    p_adm.get_by_test_id("board-menu-reopen").click()
                    p_adm.get_by_test_id("board-create-task-btn").wait_for(state="visible", timeout=10000)

                    for _k, (ctx_s, page_s, ext, errs) in sessions.items():
                        assert ext == [], f"External requests leaked: {ext}"
                        assert errs == [], f"Uncaught page errors: {errs}"
                        assert page_s.evaluate("() => localStorage.length + sessionStorage.length") == 0
                finally:
                    for ctx_s, *_ in sessions.values():
                        ctx_s.close()
                    browser.close()
        finally:
            server.should_exit = True
            t.join(timeout=5)
