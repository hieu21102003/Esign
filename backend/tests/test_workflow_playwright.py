"""Kiểm thử Playwright E2E cho giao diện Luồng ký tài liệu (chạy với web/api/signer trong Docker Compose)."""
from __future__ import annotations

import os
import time

import httpx
import pyotp
import pytest

from conftest import TEST_DATA, USER_PASS

WEB_URL = os.environ.get("WEB_LIVE_URL", "")
LIVE_ADMIN_USER = os.environ.get("LIVE_ADMIN_USER", "admin")
LIVE_ADMIN_PASS = os.environ.get("LIVE_ADMIN_PASS", "")


def _provision_workflow_users() -> dict[str, dict]:
    """Dùng API admin để bảo đảm có 4 chứng thư không thu hồi (bob, carol, dave, frank) và 4 user tương ứng."""
    mapping = [
        ("pw_wf_a", "Nguyễn Văn A", "bob"),
        ("pw_wf_b", "Trần Thị B", "carol"),
        ("pw_wf_c", "Lê Văn C", "dave"),
        ("pw_wf_d", "Đặng Thị D", "frank"),
    ]
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
        by_ref = {x["key_ref"]: x for x in certs}
        for _, _, ref in mapping:
            if ref not in by_ref:
                rc = c.post("/api/admin/certificates", json={"key_ref": ref}, headers=h)
                assert rc.status_code in (201, 409), rc.text
        certs = c.get("/api/admin/certificates").json()
        by_ref = {x["key_ref"]: x for x in certs}

        users = c.get("/api/admin/users").json()
        by_user = {u["username"]: u for u in users}
        out: dict[str, dict] = {}
        for uname, dname, ref in mapping:
            u = by_user.get(uname)
            if not u:
                ru = c.post(
                    "/api/admin/users",
                    json={"username": uname, "password": USER_PASS, "display_name": dname, "role": "user"},
                    headers=h,
                )
                assert ru.status_code == 201, ru.text
                u = ru.json()
            assert c.put(
                f"/api/admin/users/{u['id']}/certificates",
                json={"cert_ids": [by_ref[ref]["id"]]},
                headers=h,
            ).status_code == 200
            out[uname] = {"id": u["id"], "username": uname, "display_name": dname, "cert_ref": ref}
        return out


def _open_authed_page(browser, username: str, pin: str):
    ctx = browser.new_context(viewport={"width": 1366, "height": 960}, accept_downloads=True)
    page = ctx.new_page()
    external_urls: list[str] = []

    def intercept(route):
        url = route.request.url
        if not (url.startswith(WEB_URL) or url.startswith("data:") or url.startswith("blob:")):
            external_urls.append(url)
            route.abort()
        else:
            route.continue_()

    page.route("**/*", intercept)
    resp = page.goto(WEB_URL, wait_until="networkidle")
    assert resp is not None and resp.status == 200

    page.get_by_label("Tên đăng nhập").fill(username)
    page.get_by_label("Mật khẩu").fill(USER_PASS)
    page.get_by_role("button", name="Đăng nhập").click()

    secret = page.get_by_test_id("totp-secret").inner_text().strip()
    page.get_by_label("Mã 6 chữ số từ ứng dụng").fill(pyotp.TOTP(secret).now())
    page.get_by_role("button", name="Xác nhận kích hoạt 2FA").click()
    page.get_by_role("button", name="Đã lưu, vào hệ thống").click()
    page.get_by_test_id("tab-workflow").wait_for(state="visible")

    page.get_by_test_id("tab-account").click()
    for offset in (30, 60, 0):
        page.get_by_test_id("account-pin-new").fill(pin)
        page.get_by_test_id("account-pin-confirm").fill(pin)
        page.get_by_test_id("account-pin-mfa").fill(pyotp.TOTP(secret).at(time.time() + offset))
        with page.expect_response(lambda r: r.url.endswith("/api/auth/signing-pin") and r.request.method == "POST") as pr:
            page.get_by_test_id("account-pin-submit").click()
        if pr.value.status == 200:
            break
    page.get_by_test_id("pin-settings-ok").wait_for(state="visible")
    page.get_by_test_id("tab-workflow").click()
    page.get_by_test_id("wf-create-btn").wait_for(state="visible")
    assert page.evaluate("() => localStorage.length + sessionStorage.length") == 0
    return ctx, page, external_urls


@pytest.fixture(scope="module")
def wf_browser_sessions():
    if not WEB_URL:
        pytest.skip("Chưa cấu hình WEB_LIVE_URL")
    from playwright.sync_api import sync_playwright

    _provision_workflow_users()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        sessions = {}
        try:
            for key, uname, pin in (
                ("a", "pw_wf_a", "258419"),
                ("b", "pw_wf_b", "371946"),
                ("c", "pw_wf_c", "482615"),
                ("d", "pw_wf_d", "593728"),
            ):
                ctx, page, ext = _open_authed_page(browser, uname, pin)
                sessions[key] = {"ctx": ctx, "page": page, "ext": ext, "username": uname, "pin": pin}
            yield sessions
            for s in sessions.values():
                assert s["ext"] == [], f"External requests leaked: {s['ext']}"
                assert s["page"].evaluate("() => localStorage.length + sessionStorage.length") == 0
        finally:
            for s in sessions.values():
                s["ctx"].close()
            browser.close()


def _go_workflow_list(page):
    page.get_by_test_id("tab-workflow").click()
    page.get_by_test_id("wf-create-btn").wait_for(state="visible")


def _create_workflow_in_ui(page, title: str, assignees: list[str], mode: str = "all"):
    _go_workflow_list(page)
    page.get_by_test_id("wf-create-btn").click()
    page.get_by_test_id("wf-create-form").wait_for(state="visible")
    page.get_by_test_id("wf-title-input").fill(title)
    with page.expect_response(lambda r: r.url.endswith("/api/files") and r.request.method == "POST"):
        page.get_by_test_id("wf-upload-input").set_input_files(os.path.join(TEST_DATA, "sample.pdf"))
    for uname in assignees:
        page.get_by_test_id(f"wf-assignee-check-{uname}").check()
    page.get_by_test_id("wf-mode-select").select_option(mode)
    with page.expect_response(lambda r: r.url.endswith("/api/documents") and r.request.method == "POST"):
        page.get_by_test_id("wf-submit-create").click()
    page.get_by_test_id("workflow-timeline").wait_for(state="visible")


def _open_from_bell(page, title: str):
    page.reload(wait_until="networkidle")
    page.get_by_test_id("notif-badge").wait_for(state="visible")
    page.get_by_test_id("notif-bell").click()
    page.get_by_test_id("notif-dropdown").locator("button.notif-item", has_text=title).first.click()
    page.get_by_test_id("workflow-timeline").wait_for(state="visible")


def _sign_in_ui(page, pin: str, remark: str, *, post_action: str | None = None):
    page.get_by_test_id("wf-sign-panel").wait_for(state="visible")
    page.get_by_test_id("pdf-canvas").wait_for(state="visible")
    page.get_by_test_id("wf-reason-input").fill(remark)
    if post_action is not None:
        page.get_by_test_id("wf-post-sign-action").select_option(post_action)
    page.get_by_test_id("wf-sign-submit").click()
    page.get_by_test_id("pin-modal-input").wait_for(state="visible")
    page.get_by_test_id("pin-modal-input").fill(pin)
    with page.expect_response(lambda r: "/api/documents/" in r.url and r.url.endswith("/sign") and r.request.method == "POST") as resp_info:
        page.get_by_test_id("pin-modal-input").press("Enter")
    assert resp_info.value.status == 200
    page.get_by_test_id("pin-modal").wait_for(state="detached")


@pytest.mark.skipif(not WEB_URL, reason="Chưa cấu hình WEB_LIVE_URL")
def test_pw_workflow_main_all_mode_and_timeline(wf_browser_sessions):
    from playwright.sync_api import expect

    pa = wf_browser_sessions["a"]["page"]
    pb = wf_browser_sessions["b"]["page"]
    pc = wf_browser_sessions["c"]["page"]
    pd = wf_browser_sessions["d"]["page"]
    pin_b = wf_browser_sessions["b"]["pin"]
    pin_c = wf_browser_sessions["c"]["pin"]
    pin_d = wf_browser_sessions["d"]["pin"]

    title = "PW Luồng Chính ALL"
    # A tạo tài liệu và assign B + C (mode=all)
    _create_workflow_in_ui(pa, title, ["pw_wf_b", "pw_wf_c"], mode="all")

    # B mở qua chuông thông báo và ký
    _open_from_bell(pb, title)
    _sign_in_ui(pb, pin_b, "B đã đồng ý")
    expect(pb.get_by_test_id("wf-overall-status")).to_have_text("Đang ký", timeout=15000)
    expect(pb.get_by_test_id("timeline-assignee-pw_wf_b")).to_contain_text("Đã ký", timeout=15000)

    # C mở từ danh sách Chờ tôi ký, ký xong assign tiếp cho D
    _go_workflow_list(pc)
    pc.get_by_test_id("wf-scope-pending").click()
    pc.locator("tr", has_text=title).get_by_role("button", name="Chi tiết").click()
    pc.get_by_test_id("workflow-timeline").wait_for(state="visible")
    _sign_in_ui(pc, pin_c, "C đã duyệt")

    pc.get_by_test_id("wf-next-step-panel").wait_for(state="visible", timeout=15000)
    pc.get_by_test_id("wf-next-assignee-pw_wf_d").check()
    with pc.expect_response(lambda r: r.url.endswith("/assign") and r.request.method == "POST") as page_resp:
        pc.get_by_test_id("wf-assign-next-btn").click()
    assert page_resp.value.status == 200
    expect(pc.get_by_test_id("timeline-step-2")).to_be_visible(timeout=15000)

    # D mở qua chuông thông báo, ký và hoàn tất
    _open_from_bell(pd, title)
    _sign_in_ui(pd, pin_d, "D chốt ban hành", post_action="complete")
    expect(pd.get_by_test_id("wf-overall-status")).to_have_text("Hoàn tất", timeout=15000)

    # Kiểm tra Timeline hiển thị đủ bước, người ký, nhận xét, chế độ ALL
    expect(pd.get_by_test_id("timeline-step-1-mode")).to_contain_text("Tất cả phải ký", timeout=15000)
    expect(pd.get_by_test_id("timeline-assignee-pw_wf_b")).to_contain_text("Đã ký", timeout=15000)
    expect(pd.get_by_test_id("timeline-assignee-pw_wf_b")).to_contain_text("B đã đồng ý", timeout=15000)
    expect(pd.get_by_test_id("timeline-assignee-pw_wf_c")).to_contain_text("Đã ký", timeout=15000)
    expect(pd.get_by_test_id("timeline-assignee-pw_wf_d")).to_contain_text("Đã ký", timeout=15000)

    # Nút xác minh chữ ký trả đủ 3 chữ ký hợp lệ
    with pd.expect_response(lambda r: r.url.endswith("/verify") and r.request.method == "POST"):
        pd.get_by_test_id("wf-verify-btn").click()
    expect(pd.get_by_test_id("wf-verify-results")).to_contain_text("Hợp lệ (3 chữ ký", timeout=15000)


@pytest.mark.skipif(not WEB_URL, reason="Chưa cấu hình WEB_LIVE_URL")
def test_pw_workflow_midflow_reject(wf_browser_sessions):
    from playwright.sync_api import expect

    pa = wf_browser_sessions["a"]["page"]
    pb = wf_browser_sessions["b"]["page"]
    pc = wf_browser_sessions["c"]["page"]
    pin_b = wf_browser_sessions["b"]["pin"]
    pin_c = wf_browser_sessions["c"]["pin"]

    title = "PW Kịch bản Từ chối"
    _create_workflow_in_ui(pa, title, ["pw_wf_b", "pw_wf_c"], mode="all")

    _open_from_bell(pb, title)
    _sign_in_ui(pb, pin_b, "B đồng ý phần đầu")
    expect(pb.get_by_test_id("timeline-assignee-pw_wf_b")).to_contain_text("Đã ký", timeout=15000)

    _open_from_bell(pc, title)
    pc.get_by_test_id("wf-reject-toggle").click()
    pc.get_by_test_id("wf-reject-reason").fill("Thiếu phụ lục tài chính")
    pc.get_by_test_id("wf-reject-confirm").click()
    pc.get_by_test_id("pin-modal-input").wait_for(state="visible")
    pc.get_by_test_id("pin-modal-input").fill(pin_c)
    with pc.expect_response(lambda r: r.url.endswith("/reject") and r.request.method == "POST") as rj_resp:
        pc.get_by_test_id("pin-modal-input").press("Enter")
    assert rj_resp.value.status == 200
    pc.get_by_test_id("pin-modal").wait_for(state="detached")

    expect(pc.get_by_test_id("wf-overall-status")).to_have_text("Bị từ chối", timeout=15000)
    expect(pc.get_by_test_id("wf-sign-panel")).to_have_count(0, timeout=15000)
    expect(pc.get_by_test_id("timeline-assignee-pw_wf_c")).to_contain_text("Từ chối", timeout=15000)
    expect(pc.get_by_test_id("timeline-assignee-pw_wf_c")).to_contain_text("Thiếu phụ lục tài chính", timeout=15000)

    # A nhận thông báo bị từ chối và xem được trạng thái trên UI
    _open_from_bell(pa, title)
    expect(pa.get_by_test_id("wf-overall-status")).to_have_text("Bị từ chối", timeout=15000)


@pytest.mark.skipif(not WEB_URL, reason="Chưa cấu hình WEB_LIVE_URL")
def test_pw_workflow_any_mode(wf_browser_sessions):
    from playwright.sync_api import expect

    pa = wf_browser_sessions["a"]["page"]
    pb = wf_browser_sessions["b"]["page"]
    pc = wf_browser_sessions["c"]["page"]
    pin_b = wf_browser_sessions["b"]["pin"]

    title = "PW Kịch bản ANY"
    _create_workflow_in_ui(pa, title, ["pw_wf_b", "pw_wf_c"], mode="any")

    _open_from_bell(pb, title)
    _sign_in_ui(pb, pin_b, "B ký chốt chế độ ANY", post_action="complete")
    expect(pb.get_by_test_id("wf-overall-status")).to_have_text("Hoàn tất", timeout=15000)
    expect(pb.get_by_test_id("timeline-step-1-mode")).to_contain_text("Chỉ cần 1 người ký", timeout=15000)
    expect(pb.get_by_test_id("timeline-assignee-pw_wf_b")).to_contain_text("Đã ký", timeout=15000)
    expect(pb.get_by_test_id("timeline-assignee-pw_wf_c")).to_contain_text("Đã bỏ qua", timeout=15000)

    # C mở lại tài liệu đã hoàn tất ở mode ANY -> không thể ký
    _go_workflow_list(pc)
    pc.get_by_test_id("wf-scope-participated").click()
    pc.locator("tr", has_text=title).get_by_role("button", name="Chi tiết").click()
    pc.get_by_test_id("workflow-timeline").wait_for(state="visible")
    expect(pc.get_by_test_id("wf-sign-panel")).to_have_count(0, timeout=15000)
