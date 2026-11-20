"""Kiểm thử Playwright E2E cho Milestone W7: Workflow Engine UI, form động, ký số PIN, fail có lý do, followers_only và responsive."""
from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

import httpx
import pyotp
import pytest
import uvicorn
from starlette.responses import FileResponse, JSONResponse

from conftest import ADMIN_PASS, ADMIN_USER, ROOT, TEST_DATA, TEST_SIGN_PIN, USER_PASS, build_env
from test_workflow_playwright import _open_authed_page
from test_workflow_signing import _local_real_signer

WEB_URL = os.environ.get("WEB_LIVE_URL", "")
LIVE_ADMIN_USER = os.environ.get("LIVE_ADMIN_USER", ADMIN_USER)
LIVE_ADMIN_PASS = os.environ.get("LIVE_ADMIN_PASS", "")
FRONTEND_DIST = Path(ROOT) / "frontend" / "dist"
FONT_FILE = Path(ROOT) / "signer" / "fonts" / "NotoSans-Regular.ttf"
CSP_HEADER = (
    "default-src 'self'; script-src 'self'; worker-src 'self'; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
    "font-src 'self'; connect-src 'self'; object-src 'none'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


def _launch_browser(p):
    try:
        return p.chromium.launch(headless=True)
    except Exception:
        for ch in ("msedge", "chrome"):
            try:
                return p.chromium.launch(channel=ch, headless=True)
            except Exception:
                continue
        raise


def _make_spa_asgi(api_app):
    """Bọc app FastAPI với static bundle frontend/dist để chạy Playwright không cần Docker."""
    dist_resolved = FRONTEND_DIST.resolve()
    index_html = dist_resolved / "index.html"

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        path = scope.get("path") or "/"
        if path.startswith("/api/"):
            await api_app(scope, receive, send)
            return

        if path == "/fonts/NotoSans-Regular.ttf" and FONT_FILE.is_file():
            resp = FileResponse(str(FONT_FILE), media_type="font/ttf")
            await resp(scope, receive, send)
            return

        rel = path.lstrip("/")
        if rel:
            candidate = (dist_resolved / rel).resolve()
            if str(candidate).startswith(str(dist_resolved)) and candidate.is_file():
                media = "application/javascript" if candidate.suffix in (".js", ".mjs") else None
                resp = FileResponse(
                    str(candidate),
                    media_type=media,
                    headers={"X-Content-Type-Options": "nosniff"},
                )
                await resp(scope, receive, send)
                return

        if not index_html.is_file():
            resp = JSONResponse({"detail": "Chưa build frontend/dist"}, status_code=500)
            await resp(scope, receive, send)
            return

        resp = FileResponse(
            str(index_html),
            media_type="text/html; charset=utf-8",
            headers={
                "Content-Security-Policy": CSP_HEADER,
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "no-referrer",
            },
        )
        await resp(scope, receive, send)

    return app


def _wait_for_port(host: str, port: int, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"Server {host}:{port} không phản hồi đúng hạn")


def _open_local_session(browser, base_url: str, clock, *, username: str, password: str, secret: str):
    ctx = browser.new_context(viewport={"width": 1366, "height": 960}, accept_downloads=True)
    page = ctx.new_page()
    external_urls: list[str] = []
    page_errors: list[str] = []

    def intercept(route):
        url = route.request.url
        if not (url.startswith(base_url) or url.startswith("data:") or url.startswith("blob:")):
            external_urls.append(url)
            route.abort()
        else:
            route.continue_()

    page.on("pageerror", lambda exc: page_errors.append(str(exc)))
    page.route("**/*", intercept)
    resp = page.goto(base_url, wait_until="networkidle")
    assert resp is not None and resp.status == 200

    page.get_by_label("Tên đăng nhập").fill(username)
    page.get_by_label("Mật khẩu").fill(password)
    page.get_by_role("button", name="Đăng nhập").click()

    page.get_by_label("Mã TOTP 6 chữ số").wait_for(state="visible")
    clock.advance()
    page.get_by_label("Mã TOTP 6 chữ số").fill(pyotp.TOTP(secret).at(clock.t))
    page.get_by_role("button", name="Xác thực").click()
    page.get_by_test_id("wf-sidebar").wait_for(state="visible", timeout=15000)
    assert page.evaluate("() => localStorage.length + sessionStorage.length") == 0
    return ctx, page, external_urls, page_errors


def _provision_live_w7():
    """Tạo user + quy trình QT công văn đến W7 trên môi trường Docker Compose live."""
    with httpx.Client(base_url=WEB_URL, timeout=20) as c:
        r = c.post("/api/auth/login", json={"username": LIVE_ADMIN_USER, "password": LIVE_ADMIN_PASS})
        assert r.status_code == 200, r.text
        csrf = r.json()["csrf_token"]
        totp_cache = os.path.join(TEST_DATA, ".live_admin_totp")
        if not r.json()["totp_enrolled"]:
            sec = c.post("/api/auth/totp/setup", headers={"X-CSRF-Token": csrf}).json()["secret"]
            r2 = c.post("/api/auth/totp/confirm", json={"code": pyotp.TOTP(sec).now()}, headers={"X-CSRF-Token": csrf})
            assert r2.status_code == 200, r2.text
            csrf = r2.json()["csrf_token"]
            open(totp_cache, "w", encoding="utf-8").write(sec)
        else:
            sec = os.environ.get("LIVE_ADMIN_TOTP_SECRET") or open(totp_cache, encoding="utf-8").read().strip()
            totp = pyotp.TOTP(sec)
            r2 = c.post("/api/auth/totp/verify", json={"code": totp.now()}, headers={"X-CSRF-Token": csrf})
            if r2.status_code == 401:
                r2 = c.post(
                    "/api/auth/totp/verify",
                    json={"code": totp.at(time.time() + 30)},
                    headers={"X-CSRF-Token": csrf},
                )
            assert r2.status_code == 200, r2.text
            csrf = r2.json()["csrf_token"]

        h = {"X-CSRF-Token": csrf}
        certs = c.get("/api/admin/certificates").json()
        bob = next((x for x in certs if x["key_ref"] == "bob"), None)
        if not bob:
            rc = c.post("/api/admin/certificates", json={"key_ref": "bob"}, headers=h)
            assert rc.status_code in (201, 409), rc.text
            bob = next(x for x in c.get("/api/admin/certificates").json() if x["key_ref"] == "bob")

        users = c.get("/api/admin/users").json()
        by_user = {u["username"]: u for u in users}
        specs = [
            ("w7_vt", "W7 Văn thư", False),
            ("w7_gd", "W7 Giám đốc", True),
            ("w7_bm1", "W7 Bộ môn 1", False),
            ("w7_bm2", "W7 Bộ môn 2", False),
            ("w7_viewer", "W7 Người xem", False),
        ]
        uids: dict[str, int] = {}
        for uname, dname, need_cert in specs:
            u = by_user.get(uname)
            if not u:
                ru = c.post(
                    "/api/admin/users",
                    json={"username": uname, "password": USER_PASS, "display_name": dname, "role": "user"},
                    headers=h,
                )
                assert ru.status_code == 201, ru.text
                u = ru.json()
            if need_cert:
                assert c.put(
                    f"/api/admin/users/{u['id']}/certificates",
                    json={"cert_ids": [bob["id"]]},
                    headers=h,
                ).status_code == 200
            uids[uname] = u["id"]

        slug = f"qt-cv-w7-{int(time.time())}"
        rwf = c.post(
            "/api/workflows",
            headers=h,
            json={
                "name": "QT công văn đến (W7 E2E)",
                "slug": slug,
                "category": "Văn thư",
                "visibility": "followers_only",
                "stages": [
                    {"name": "Văn thư tiếp nhận", "type": "normal", "sla_hours": 8, "assign_mode": "any", "member_ids": [uids["w7_vt"]]},
                    {"name": "Giám đốc cho ý kiến", "type": "approve", "sla_hours": 2, "assign_mode": "any", "member_ids": [uids["w7_gd"]]},
                    {"name": "Bộ môn thực hiện", "type": "normal", "sla_hours": 24, "assign_mode": "all", "member_ids": [uids["w7_bm1"], uids["w7_bm2"]]},
                    {"name": "Giám đốc ký duyệt", "type": "sign", "sla_hours": 1, "assign_mode": "any", "member_ids": [uids["w7_gd"]]},
                    {"name": "Văn thư phát hành", "type": "normal", "sla_hours": 1, "assign_mode": "any", "member_ids": [uids["w7_vt"]]},
                ],
                "form_fields": [
                    {"label": "Tên công văn", "type": "text", "required": True, "placeholder": "Nhập tên"},
                    {"label": "Đơn vị gửi", "type": "text", "required": True, "placeholder": "Nhập đơn vị"},
                    {"label": "Trích yếu", "type": "textarea", "required": True, "placeholder": "Tóm tắt"},
                    {"label": "Thời hạn trả lời", "type": "date", "required": True},
                    {"label": "Scan văn bản", "type": "file", "required": True},
                ],
                "roles": [
                    {"user_id": uids["w7_vt"], "role": "creator"},
                    {"user_id": uids["w7_viewer"], "role": "viewer"},
                ],
                "failure_reasons": [
                    {"label": "Sai thẩm quyền / chuyển nhầm đơn vị"},
                    {"label": "Hồ sơ chưa đầy đủ / không hợp lệ"},
                ],
            },
        )
        assert rwf.status_code == 201, rwf.text
        wf_info = rwf.json()
        return slug, {f["label"]: f["id"] for f in wf_info["form_fields"]}


def _run_w7_ui_e2e(p_vt, p_gd, p_bm1, p_bm2, p_out, *, base_url: str, f_by_label: dict[str, int], wf_slug: str, sign_level: str):
    from playwright.sync_api import expect

    # ----- 1. VT mở Quản lý quy trình -> vào Board QT công văn đến -----
    p_vt.get_by_test_id("nav-wf-workflows").click()
    p_vt.get_by_test_id(f"wf-card-{wf_slug}").wait_for(state="visible", timeout=10000)
    p_vt.get_by_test_id(f"wf-card-{wf_slug}").click()
    p_vt.get_by_test_id("wf-kanban-board").wait_for(state="visible", timeout=10000)

    # Mở CreateTaskModal & kiểm tra validate trường bắt buộc phía client chặn submit
    p_vt.get_by_test_id("board-create-task-btn").click()
    p_vt.get_by_test_id("create-task-modal").wait_for(state="visible")
    p_vt.get_by_test_id("create-task-title").fill("Công văn E2E W7")
    p_vt.get_by_test_id("create-task-submit").click()
    p_vt.get_by_test_id("create-task-error").wait_for(state="visible")
    assert p_vt.get_by_test_id(f"dyn-err-{f_by_label['Tên công văn']}").is_visible()

    # Điền đầy đủ 5 trường động + nhãn + upload PDF
    p_vt.get_by_test_id("create-task-labels").fill("khẩn")
    p_vt.get_by_test_id("create-task-labels").press("Enter")
    p_vt.get_by_test_id(f"dyn-input-{f_by_label['Tên công văn']}").fill("Triển khai ký số W7")
    p_vt.get_by_test_id(f"dyn-input-{f_by_label['Đơn vị gửi']}").fill("Văn phòng Trung ương")
    p_vt.get_by_test_id(f"dyn-input-{f_by_label['Trích yếu']}").fill("Tài liệu test luồng E2E trình duyệt")
    p_vt.get_by_test_id(f"dyn-input-{f_by_label['Thời hạn trả lời']}").fill("2026-12-20")
    with p_vt.expect_response(lambda r: r.url.endswith("/api/files") and r.request.method == "POST"):
        p_vt.get_by_test_id(f"dyn-input-{f_by_label['Scan văn bản']}").set_input_files(
            os.path.join(TEST_DATA, "sample.pdf")
        )

    p_vt.get_by_test_id("create-task-submit").click()
    p_vt.get_by_test_id("task-detail-view").wait_for(state="visible", timeout=15000)
    main_task_id = int(p_vt.url.rstrip("/").split("/")[-1])

    # Stage 1 (normal): Văn thư hoàn thành
    expect(p_vt.get_by_test_id("task-current-stage")).to_contain_text("Văn thư tiếp nhận")
    p_vt.get_by_test_id("complete-todo-result").fill("Đã tiếp nhận và đánh số")
    p_vt.get_by_test_id("complete-todo-submit").click()
    expect(p_vt.get_by_test_id("task-current-stage")).to_contain_text("Giám đốc cho ý kiến", timeout=15000)

    # Stage 2 (approve): Giám đốc vào Nhiệm vụ của tôi -> Phê duyệt
    p_gd.get_by_test_id("nav-wf-jobs").click()
    p_gd.get_by_test_id(f"job-row-{main_task_id}").wait_for(state="visible", timeout=10000)
    p_gd.get_by_test_id(f"job-row-{main_task_id}").click()
    p_gd.get_by_test_id("task-approve-panel").wait_for(state="visible")
    p_gd.get_by_test_id("approve-note-input").fill("Giao bộ môn thực hiện")
    p_gd.get_by_test_id("approve-submit-btn").click()
    expect(p_gd.get_by_test_id("task-current-stage")).to_contain_text("Bộ môn thực hiện", timeout=15000)

    # Stage 3 (normal, all): BM1 & BM2 mở qua Công việc của tôi -> Hoàn thành cả 2
    for p_bm in (p_bm1, p_bm2):
        p_bm.get_by_test_id("nav-wf-todos").click()
        p_bm.locator("tr", has_text="Công văn E2E W7").first.wait_for(state="visible", timeout=10000)
        p_bm.locator("tr", has_text="Công văn E2E W7").first.click()
        p_bm.get_by_test_id("complete-todo-submit").wait_for(state="visible", timeout=10000)
        p_bm.get_by_test_id("complete-todo-result").fill("Đã xử lý xong phần bộ môn")
        p_bm.get_by_test_id("complete-todo-submit").click()
        p_bm.get_by_test_id("task-detail-msg").wait_for(state="visible", timeout=15000)

    # Stage 4 (sign): Giám đốc mở từ Chuông thông báo -> kiểm tra KHÔNG có nút hoàn thành tay -> Ký bằng PIN
    p_gd.reload(wait_until="networkidle")
    p_gd.get_by_test_id("notif-badge").wait_for(state="visible", timeout=10000)
    p_gd.get_by_test_id("notif-bell").click()
    p_gd.get_by_test_id("notif-dropdown").locator("button.notif-item", has_text="Cần ký tài liệu").first.click()
    p_gd.get_by_test_id("task-sign-panel").wait_for(state="visible", timeout=15000)
    expect(p_gd.get_by_test_id("complete-todo-submit")).to_have_count(0)
    p_gd.get_by_test_id("pdf-canvas").wait_for(state="visible", timeout=15000)
    p_gd.get_by_test_id("wf-task-sign-level").select_option(sign_level)
    p_gd.get_by_test_id("wf-task-sign-reason").fill("Giám đốc ký duyệt W7")
    p_gd.get_by_test_id("wf-task-sign-btn").click()
    p_gd.get_by_test_id("pin-modal-input").wait_for(state="visible")
    p_gd.get_by_test_id("pin-modal-input").fill("268134")
    with p_gd.expect_response(lambda r: r.url.endswith(f"/api/tasks/{main_task_id}/sign") and r.request.method == "POST") as sign_resp:
        p_gd.get_by_test_id("pin-modal-input").press("Enter")
    assert sign_resp.value.status == 200
    p_gd.get_by_test_id("pin-modal").wait_for(state="detached", timeout=15000)

    # Stage 5 (normal): Văn thư phát hành -> Hoàn thành & kiểm tra xác minh PDF + Timeline hợp nhất
    p_vt.reload(wait_until="networkidle")
    expect(p_vt.get_by_test_id("task-current-stage")).to_contain_text("Văn thư phát hành", timeout=15000)
    p_vt.get_by_test_id("complete-todo-submit").click()
    expect(p_vt.get_by_test_id("task-detail-status")).to_have_text("Hoàn thành", timeout=15000)

    p_vt.get_by_test_id("task-tab-document").click()
    p_vt.get_by_test_id("task-doc-verify").click()
    expect(p_vt.get_by_test_id("task-doc-verify-result")).to_contain_text("1 chữ ký hợp lệ", timeout=15000)

    p_vt.get_by_test_id("task-tab-history").click()
    expect(p_vt.get_by_test_id("activity-timeline")).to_contain_text("Ký số tài liệu")
    expect(p_vt.get_by_test_id("activity-timeline")).to_contain_text("Nhiệm vụ hoàn thành")

    # Lưu trữ & Khôi phục từ Board menu ...
    p_vt.get_by_test_id("task-archive-btn").click()
    p_vt.get_by_test_id("task-restore-btn").wait_for(state="visible", timeout=10000)
    p_vt.get_by_test_id(f"nav-wf-board-{wf_slug}").click()
    p_vt.get_by_test_id("wf-topbar-menu-btn").click()
    p_vt.get_by_test_id("board-menu-archive").click()
    p_vt.get_by_test_id(f"archive-restore-{main_task_id}").wait_for(state="visible", timeout=10000)
    p_vt.get_by_test_id(f"archive-restore-{main_task_id}").click()
    expect(p_vt.get_by_test_id(f"archive-restore-{main_task_id}")).to_have_count(0, timeout=10000)

    # ----- 2. Ca thất bại có lý do -----
    p_vt.get_by_test_id(f"nav-wf-board-{wf_slug}").click()
    p_vt.get_by_test_id("board-create-task-btn").click()
    p_vt.get_by_test_id("create-task-title").fill("Công văn Bị Từ Chối W7")
    p_vt.get_by_test_id(f"dyn-input-{f_by_label['Tên công văn']}").fill("Đề xuất chưa đạt")
    p_vt.get_by_test_id(f"dyn-input-{f_by_label['Đơn vị gửi']}").fill("Phòng Hành chính")
    p_vt.get_by_test_id(f"dyn-input-{f_by_label['Trích yếu']}").fill("Thiếu phụ lục")
    p_vt.get_by_test_id(f"dyn-input-{f_by_label['Thời hạn trả lời']}").fill("2026-12-25")
    with p_vt.expect_response(lambda r: r.url.endswith("/api/files") and r.request.method == "POST"):
        p_vt.get_by_test_id(f"dyn-input-{f_by_label['Scan văn bản']}").set_input_files(
            os.path.join(TEST_DATA, "sample.pdf")
        )
    p_vt.get_by_test_id("create-task-submit").click()
    p_vt.get_by_test_id("complete-todo-submit").wait_for(state="visible", timeout=15000)
    fail_task_id = int(p_vt.url.rstrip("/").split("/")[-1])
    p_vt.get_by_test_id("toggle-fail-btn").click()
    p_vt.get_by_test_id("fail-reason-select").select_option(label="Hồ sơ chưa đầy đủ / không hợp lệ")
    p_vt.get_by_test_id("fail-note-input").fill("Thiếu chữ ký trưởng đơn vị")
    p_vt.get_by_test_id("fail-submit-btn").click()
    expect(p_vt.get_by_test_id("task-detail-status")).to_have_text("Thất bại", timeout=15000)
    expect(p_vt.get_by_test_id("task-failure-banner")).to_contain_text("Hồ sơ chưa đầy đủ / không hợp lệ")

    # Board hiển thị task vào 2 cột Hoàn thành / Thất bại + FilterDrawer trên /jobs lưu URL query
    p_vt.get_by_test_id(f"nav-wf-board-{wf_slug}").click()
    expect(p_vt.get_by_test_id("board-col-completed")).to_contain_text("Công văn E2E W7", timeout=10000)
    expect(p_vt.get_by_test_id("board-col-failed")).to_contain_text("Công văn Bị Từ Chối W7", timeout=10000)

    p_vt.get_by_test_id("nav-wf-jobs").click()
    p_vt.get_by_test_id("jobs-role-created").click()
    p_vt.get_by_test_id("wf-open-filter-btn").click()
    p_vt.get_by_test_id("filter-q").fill("E2E W7")
    p_vt.get_by_test_id("filter-apply-btn").click()
    expect(p_vt.get_by_test_id("wf-open-filter-btn")).to_contain_text("Bộ lọc (1)")
    assert "q=E2E" in p_vt.url
    p_vt.reload(wait_until="networkidle")
    expect(p_vt.get_by_test_id(f"job-row-{main_task_id}")).to_be_visible()
    assert p_vt.locator(f"[data-testid='job-row-{fail_task_id}']").count() == 0

    # ----- 3. Ca không có quyền xem task followers_only -----
    p_out.get_by_test_id("nav-wf-jobs").click()
    expect(p_out.get_by_test_id(f"job-row-{main_task_id}")).to_have_count(0)
    p_out.goto(f"{base_url}/tasks/{main_task_id}", wait_until="networkidle")
    p_out.get_by_test_id("task-not-accessible").wait_for(state="visible", timeout=10000)

    # ----- 4. Responsive laptop & mobile hẹp -----
    p_vt.set_viewport_size({"width": 390, "height": 844})
    p_vt.get_by_test_id("mobile-nav-toggle").wait_for(state="visible")
    p_vt.get_by_test_id("mobile-nav-toggle").click()
    expect(p_vt.get_by_test_id("wf-sidebar")).to_be_visible()
    p_vt.get_by_test_id("nav-wf-workflows").click()
    p_vt.get_by_test_id("workflows-list-view").wait_for(state="visible")
    assert p_vt.evaluate("() => document.documentElement.scrollWidth <= 410")


@pytest.mark.skipif(
    not WEB_URL and not (FRONTEND_DIST / "index.html").is_file(),
    reason="Cần WEB_LIVE_URL hoặc frontend/dist đã build",
)
def test_w7_wfengine_playwright_e2e(tmp_path):
    """Playwright E2E cho W7: chạy cả chế độ live (Docker Compose) và local SPA + pyHanko thật."""
    from playwright.sync_api import sync_playwright

    if WEB_URL:
        wf_slug, f_by_label = _provision_live_w7()
        with sync_playwright() as p:
            browser = _launch_browser(p)
            sessions = {}
            try:
                for key, uname, dname, jt, dept, pin in (
                    ("vt", "w7_vt", "W7 Văn thư", "Văn thư", "Văn phòng", TEST_SIGN_PIN),
                    ("gd", "w7_gd", "W7 Giám đốc", "Giám đốc", "Ban Giám đốc", "268134"),
                    ("bm1", "w7_bm1", "W7 Bộ môn 1", "Chuyên viên", "Bộ môn 1", TEST_SIGN_PIN),
                    ("bm2", "w7_bm2", "W7 Bộ môn 2", "Chuyên viên", "Bộ môn 2", TEST_SIGN_PIN),
                    ("out", "w7_viewer", "W7 Người xem", "Khách", "Phòng khác", TEST_SIGN_PIN),
                ):
                    ctx_s, page_s, ext = _open_authed_page(browser, uname, dname, jt, dept, pin)
                    sessions[key] = (ctx_s, page_s, ext, [])

                _run_w7_ui_e2e(
                    sessions["vt"][1], sessions["gd"][1], sessions["bm1"][1],
                    sessions["bm2"][1], sessions["out"][1],
                    base_url=WEB_URL.rstrip("/"), f_by_label=f_by_label,
                    wf_slug=wf_slug, sign_level="B-T",
                )
                for ctx_s, page_s, ext, _ in sessions.values():
                    assert ext == [], f"External requests leaked: {ext}"
                    assert page_s.evaluate("() => localStorage.length + sessionStorage.length") == 0
            finally:
                for ctx_s, *_ in sessions.values():
                    ctx_s.close()
                browser.close()
        return

    from app.models import User
    from app.seed_workflows import seed_sample_workflows

    with _local_real_signer(tmp_path) as (signer_url, tok, _pub_dir):
        env = build_env(tmp_path / "app", with_services=False, signer_url=signer_url, signer_token=tok)
        env.register_certs("bob")
        vt = env.make_user("w7_vt")
        gd = env.make_user("w7_gd", cert_keys=["bob"], pin="268134")
        bm1 = env.make_user("w7_bm1")
        bm2 = env.make_user("w7_bm2")
        outsider = env.make_user("w7_viewer")

        sf = env.app.state.session_factory
        db = sf()
        try:
            admin_u = db.query(User).filter(User.username == ADMIN_USER).one()
            seeded = seed_sample_workflows(
                db,
                admin_u,
                member_ids_by_slug={
                    "qt-cong-van-den": [[vt.uid], [gd.uid], [bm1.uid, bm2.uid], [gd.uid], [vt.uid]],
                },
                extra_roles=[(vt.uid, "creator"), (outsider.uid, "viewer")],
            )
            wf_id = seeded["workflows"]["qt-cong-van-den"]
        finally:
            db.close()

        wf_info = env.admin.get(f"/api/workflows/{wf_id}").json()
        f_by_label = {f["label"]: f["id"] for f in wf_info["form_fields"]}

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
                    for key, uname, secret in (
                        ("vt", "w7_vt", vt.secret),
                        ("gd", "w7_gd", gd.secret),
                        ("bm1", "w7_bm1", bm1.secret),
                        ("bm2", "w7_bm2", bm2.secret),
                        ("out", "w7_viewer", outsider.secret),
                    ):
                        sessions[key] = _open_local_session(
                            browser, base_url, env.clock,
                            username=uname, password=USER_PASS, secret=secret,
                        )

                    _run_w7_ui_e2e(
                        sessions["vt"][1], sessions["gd"][1], sessions["bm1"][1],
                        sessions["bm2"][1], sessions["out"][1],
                        base_url=base_url, f_by_label=f_by_label,
                        wf_slug="qt-cong-van-den", sign_level="B-B",
                    )

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
