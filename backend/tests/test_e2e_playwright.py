"""Kiểm thử end-to-end M5 bằng trình duyệt thật (Playwright Chromium):
- Chặt CSP, không tải CDN, không dùng localStorage/sessionStorage, không dangerouslySetInnerHTML.
- Luồng đầy đủ: đăng nhập + 2FA -> upload docx -> xem trước -> kéo thả -> 3 chế độ + nhận xét + preset -> ký -> tải về -> xác minh.
- Kiểm tra tọa độ (< 3pt): góc trên-trái, góc dưới-phải, trang xoay 90°, khổ A3 lệch gốc, khổ Letter.
"""
from __future__ import annotations

import io
import os
import subprocess
import tempfile

import httpx
import pyotp
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, NameObject, NumberObject

from conftest import ROOT, TEST_DATA, USER_PASS

WEB_URL = os.environ.get("WEB_LIVE_URL", "")
LIVE_ADMIN_USER = os.environ.get("LIVE_ADMIN_USER", "admin")
LIVE_ADMIN_PASS = os.environ.get("LIVE_ADMIN_PASS", "")


def _make_multi_geom_pdf() -> bytes:
    """Tạo PDF 5 trang: A4, A4, A4 Rotate=90, A3 CropBox/MediaBox lệch gốc, Letter."""
    w = PdfWriter()
    p1 = w.add_blank_page(595, 842)
    p2 = w.add_blank_page(595, 842)
    p3 = w.add_blank_page(595, 842)
    p3[NameObject("/Rotate")] = NumberObject(90)
    p4 = w.add_blank_page(842, 1191)
    p4[NameObject("/MediaBox")] = ArrayObject([NumberObject(50), NumberObject(40), NumberObject(892), NumberObject(1231)])
    p4[NameObject("/CropBox")] = ArrayObject([NumberObject(70), NumberObject(60), NumberObject(872), NumberObject(1211)])
    w.add_blank_page(612, 792)  # Letter
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _provision_live_user(username: str) -> int:
    """Dùng API live với tài khoản admin để đảm bảo có chứng thư carol + user `username` được gán carol."""
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
            r2 = c.post("/api/auth/totp/verify", json={"code": pyotp.TOTP(sec).now()}, headers={"X-CSRF-Token": csrf})
            assert r2.status_code == 200, r2.text
            csrf = r2.json()["csrf_token"]

        h = {"X-CSRF-Token": csrf}
        certs = c.get("/api/admin/certificates").json()
        carol = next((x for x in certs if x["key_ref"] == "carol"), None)
        if not carol:
            rc = c.post("/api/admin/certificates", json={"key_ref": "carol"}, headers=h)
            assert rc.status_code in (201, 409), rc.text
            certs = c.get("/api/admin/certificates").json()
            carol = next(x for x in certs if x["key_ref"] == "carol")

        users = c.get("/api/admin/users").json()
        u = next((x for x in users if x["username"] == username), None)
        if not u:
            ru = c.post("/api/admin/users", json={"username": username, "password": USER_PASS,
                                                  "display_name": "Lê Văn C", "role": "user"}, headers=h)
            assert ru.status_code == 201, ru.text
            u = ru.json()
        assert c.put(f"/api/admin/users/{u['id']}/certificates", json={"cert_ids": [carol["id"]]}, headers=h).status_code == 200
        return u["id"]


def _drag_sig_box(pw_page, target_x_ratio: float, target_y_ratio: float, resize_dx: int = 0, resize_dy: int = 0):
    """Kéo thả khung chữ ký trên pdf-stage và trả về tọa độ CSS pixel thực tế của sig-box so với pdf-stage."""
    stage = pw_page.get_by_test_id("pdf-stage")
    sig = pw_page.get_by_test_id("sig-box")
    sb = stage.bounding_box()
    bb = sig.bounding_box()
    assert sb and bb

    dest_x = sb["x"] + (sb["width"] - bb["width"]) * target_x_ratio + bb["width"] / 2
    dest_y = sb["y"] + (sb["height"] - bb["height"]) * target_y_ratio + bb["height"] / 2
    sx = bb["x"] + bb["width"] / 2
    sy = bb["y"] + bb["height"] / 2
    pw_page.mouse.move(sx, sy)
    pw_page.mouse.down()
    pw_page.mouse.move(dest_x, dest_y, steps=8)
    pw_page.mouse.up()

    if resize_dx or resize_dy:
        hb = pw_page.get_by_test_id("sig-resize").bounding_box()
        assert hb
        rx, ry = hb["x"] + hb["width"] / 2, hb["y"] + hb["height"] / 2
        pw_page.mouse.move(rx, ry)
        pw_page.mouse.down()
        pw_page.mouse.move(rx + resize_dx, ry + resize_dy, steps=6)
        pw_page.mouse.up()

    sb = stage.bounding_box()
    bb = sig.bounding_box()
    assert sb and bb
    return {
        "x": bb["x"] - sb["x"],
        "y": bb["y"] - sb["y"],
        "w": bb["width"],
        "h": bb["height"],
        "stage_w": sb["width"],
        "stage_h": sb["height"],
    }


def _rect_to_viewport_pt(pdf_bytes: bytes, page_index: int, rect_pdf: list[float]) -> tuple[float, float, float, float]:
    """Quy đổi /Rect [x1,y1,x2,y2] trong PDF về tọa độ trang hiển thị (pt, gốc trên-trái, đã tính CropBox + Rotate)."""
    p = PdfReader(io.BytesIO(pdf_bytes)).pages[page_index]
    cb = p.cropbox or p.mediabox
    cx1, cy1, cx2, cy2 = float(cb.left), float(cb.bottom), float(cb.right), float(cb.top)
    cw, ch = cx2 - cx1, cy2 - cy1
    rot = int(p.get("/Rotate", 0) or 0) % 360

    def pt_to_visual(x: float, y: float) -> tuple[float, float]:
        nx, ny = x - cx1, y - cy1
        if rot == 0:
            return nx, ch - ny
        if rot == 90:
            return ny, nx
        if rot == 180:
            return cw - nx, ny
        if rot == 270:
            return ch - ny, cw - nx
        raise AssertionError(f"Unsupported rotate {rot}")

    p1 = pt_to_visual(rect_pdf[0], rect_pdf[1])
    p2 = pt_to_visual(rect_pdf[2], rect_pdf[3])
    return (min(p1[0], p2[0]), min(p1[1], p2[1]), max(p1[0], p2[0]), max(p1[1], p2[1]))


def test_frontend_static_security_checks():
    import re
    src_dir = os.path.join(ROOT, "frontend", "src")
    for d, _, files in os.walk(src_dir):
        for fn in files:
            if not fn.endswith((".ts", ".tsx", ".css")):
                continue
            txt = open(os.path.join(d, fn), encoding="utf-8").read()
            code = re.sub(r"/\*[\s\S]*?\*/|//.*", "", txt)
            assert "dangerouslySetInnerHTML" not in code, fn
            assert "localStorage" not in code, fn
            assert "sessionStorage" not in code, fn
            assert "https://" not in code and "http://" not in code, fn


@pytest.mark.skipif(not WEB_URL, reason="Chưa cấu hình WEB_LIVE_URL")
def test_playwright_e2e_and_coordinates():
    from playwright.sync_api import sync_playwright

    _provision_live_user("e2euser")
    external_urls = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1366, "height": 960}, accept_downloads=True)
        page = context.new_page()

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
        csp = resp.headers.get("content-security-policy", "")
        assert "default-src 'self'" in csp and "worker-src 'self'" in csp and "object-src 'none'" in csp

        # 1. Đăng nhập + thiết lập 2FA lần đầu trên UI
        page.get_by_label("Tên đăng nhập").fill("e2euser")
        page.get_by_label("Mật khẩu").fill(USER_PASS)
        page.get_by_role("button", name="Đăng nhập").click()

        secret = page.get_by_test_id("totp-secret").inner_text().strip()
        page.get_by_label("Mã 6 chữ số từ ứng dụng").fill(pyotp.TOTP(secret).now())
        page.get_by_role("button", name="Xác nhận kích hoạt 2FA").click()
        page.get_by_role("button", name="Đã lưu, vào hệ thống").click()
        page.get_by_test_id("sign-btn").wait_for(state="visible")

        # Kiểm tra không lưu gì trong localStorage / sessionStorage
        assert page.evaluate("() => localStorage.length + sessionStorage.length") == 0

        # 2. Upload DOCX + xem trước + test preset + 3 chế độ + nhận xét + ký + tải về + xác minh
        page.get_by_test_id("upload-doc-input").set_input_files(os.path.join(TEST_DATA, "sample.docx"))
        page.get_by_test_id("sig-box").wait_for(state="visible")

        # Lưu preset và kiểm tra 3 chế độ
        page.get_by_test_id("reason-input").fill("Đã duyệt từ giao diện web")
        page.get_by_test_id("preset-name-input").fill("Duyệt chuẩn")
        page.get_by_test_id("save-preset-btn").click()

        page.get_by_test_id("mode-image").click()
        page.get_by_test_id("upload-image-input").set_input_files(os.path.join(TEST_DATA, "sig.png"))
        page.get_by_test_id("mode-text").click()
        page.get_by_test_id("mode-both").click()

        _drag_sig_box(page, 0.85, 0.85, resize_dx=20, resize_dy=10)
        page.get_by_test_id("sign-btn").click()
        dl_link = page.get_by_test_id("download-signed-link")
        dl_link.wait_for(state="visible", timeout=30000)

        with page.expect_download() as dl_info:
            dl_link.click()
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
            dl_info.value.save_as(tf.name)
            pdfsig_out = subprocess.run(["pdfsig", "-nocert", tf.name], capture_output=True, text=True).stdout
            assert "Signature is Valid" in pdfsig_out, pdfsig_out
            os.unlink(tf.name)

        # Chuyển sang trang xác minh ngay
        page.get_by_test_id("go-verify-btn").click()
        vr = page.get_by_test_id("verify-result")
        vr.wait_for(state="visible", timeout=20000)
        assert "HỢP LỆ" in vr.inner_text() and "Đã duyệt từ giao diện web" in vr.inner_text()

        # 3. Kiểm tra ĐÚNG TỌA ĐỘ (< 3pt) trên 5 hình học trang khác nhau
        page.get_by_test_id("tab-sign").click()
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as mf:
            mf.write(_make_multi_geom_pdf())
            multi_path = mf.name
        try:
            page.get_by_test_id("upload-doc-input").set_input_files(multi_path)
            page.get_by_test_id("sig-box").wait_for(state="visible")
        finally:
            os.unlink(multi_path)

        page.get_by_test_id("mode-text").click()
        cases = [
            (1, 0.02, 0.02, "top-left A4"),
            (2, 0.98, 0.98, "bottom-right A4"),
            (3, 0.85, 0.15, "rotate 90"),
            (4, 0.80, 0.80, "A3 offset CropBox"),
            (5, 0.10, 0.85, "Letter"),
        ]
        for pg, rx, ry, label in cases:
            # Đi tới trang `pg`
            for _ in range(pg - 1):
                page.get_by_role("button", name="Trang sau").click()
            page.wait_for_function(
                f"() => document.querySelector('[data-testid=\"page-indicator\"]')?.textContent?.includes('Trang {pg} /')"
            )
            px_box = _drag_sig_box(page, rx, ry)
            with page.expect_response(lambda res: res.url.endswith("/api/sign") and res.status == 200):
                with page.expect_response(lambda res: "/download" in res.url and res.status == 200):
                    page.get_by_test_id("sign-btn").click()
            page.get_by_text("Ký số thành công!").wait_for(state="visible", timeout=20000)
            page.get_by_test_id("sig-box").wait_for(state="visible", timeout=20000)
            # Đọc file vừa ký qua download link (chứa bản mới nhất có thêm 1 chữ ký)
            with page.expect_download() as dl_geom:
                page.get_by_test_id("download-signed-link").click()
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as gf:
                dl_geom.value.save_as(gf.name)
                signed_bytes = open(gf.name, "rb").read()
                os.unlink(gf.name)

            r = PdfReader(io.BytesIO(signed_bytes))
            # Chữ ký vừa thêm là phần tử cuối cùng trong /AcroForm /Fields
            sig_field = r.trailer["/Root"]["/AcroForm"]["/Fields"][-1].get_object()
            rect_pdf = [float(v) for v in sig_field["/Rect"]]

            # Quy đổi vị trí kéo thả trên stage (CSS px) sang pt trên trang hiển thị
            p_obj = r.pages[pg - 1]
            cb = p_obj.cropbox or p_obj.mediabox
            cw, ch = float(cb.right) - float(cb.left), float(cb.top) - float(cb.bottom)
            rot = int(p_obj.get("/Rotate", 0) or 0) % 360
            vis_w, vis_h = (ch, cw) if rot in (90, 270) else (cw, ch)

            exp_x1 = px_box["x"] * (vis_w / px_box["stage_w"])
            exp_y1 = px_box["y"] * (vis_h / px_box["stage_h"])
            exp_x2 = (px_box["x"] + px_box["w"]) * (vis_w / px_box["stage_w"])
            exp_y2 = (px_box["y"] + px_box["h"]) * (vis_h / px_box["stage_h"])

            act_x1, act_y1, act_x2, act_y2 = _rect_to_viewport_pt(signed_bytes, pg - 1, rect_pdf)
            for a, e in ((act_x1, exp_x1), (act_y1, exp_y1), (act_x2, exp_x2), (act_y2, exp_y2)):
                assert abs(a - e) < 3.0, f"{label}: sai số tọa độ {abs(a - e):.2f}pt >= 3pt"

        browser.close()
    assert external_urls == [], f"Phát hiện tải tài nguyên ngoài (CDN): {external_urls}"
