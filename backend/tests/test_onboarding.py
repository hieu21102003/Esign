"""Kiểm thử toàn diện luồng Onboarding bắt buộc 4 bước (chặn 403, OTP, xóa nền ảnh, PIN, CA, audit)."""
from __future__ import annotations

import base64
import io
import json
import smtplib

import pyotp
import pytest
from PIL import Image, ImageDraw
from pypdf import PdfWriter
from sqlalchemy import select

from conftest import TEST_SIGN_PIN, USER_PASS, build_env


def _make_pdf() -> bytes:
    w = PdfWriter()
    w.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _make_signature_jpeg_with_exif(*, width: int = 240, height: int = 120) -> bytes:
    """Tạo ảnh JPEG nền giấy hơi ngả trắng (244, 242, 236) kèm nét ký đậm và thẻ EXIF."""
    im = Image.new("RGB", (width, height), (244, 242, 236))
    dr = ImageDraw.Draw(im)
    dr.line([(36, 80), (90, 34), (135, 86), (200, 42)], fill=(15, 35, 130), width=5)
    exif = Image.Exif()
    exif[0x010F] = "SecretVendorCam"
    exif[0x0112] = 6  # Orientation=6 -> cần exif_transpose và xóa EXIF sau xử lý
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=92, exif=exif.tobytes())
    return buf.getvalue()


def _complete_step1_to_3(env, api, *, username: str, email: str, display_name: str = "Nguyễn Văn Test",
                         job_title: str = "Chuyên viên", department: str = "Phòng Pháp chế",
                         manager_name: str = "Trưởng phòng", manager_user_id=None,
                         use_email_otp: bool = False, mail_box: list | None = None):
    r1 = api.post("/api/onboarding/profile", json={
        "display_name": display_name,
        "job_title": job_title,
        "department": department,
        "manager_name": manager_name,
        "manager_user_id": manager_user_id,
        "email": email,
    })
    assert r1.status_code == 200, r1.text

    terms = api.get("/api/onboarding/terms").json()
    if use_email_otp:
        rs = api.post("/api/onboarding/terms/send-otp")
        assert rs.status_code == 200, rs.text
        otp = mail_box[-1]["otp"]
    else:
        env.clock.advance()
        otp = pyotp.TOTP(api.secret).at(env.clock.t)

    r2 = api.post("/api/onboarding/terms/accept", json={
        "terms_version": terms["version"],
        "scrolled_to_bottom": True,
        "agreed": True,
        "otp_code": otp,
    })
    assert r2.status_code == 200, r2.text

    jpeg_bytes = _make_signature_jpeg_with_exif()
    r3 = api.post(
        "/api/onboarding/signature-image/save",
        files={"file": ("sig.jpg", jpeg_bytes, "image/jpeg")},
        data={"remove_bg": "true", "sensitivity": "55", "rotate": "0"},
    )
    assert r3.status_code == 200, r3.text
    return terms, r3.json()


def test_onboarding_gate_blocks_403_and_resumes_saved_step(env_light):
    mgr = env_light.make_user("mgr_u", onboarded=True)
    u = env_light.make_user("new_u", pin=None, onboarded=False)

    me = u.get("/api/auth/me").json()
    assert me["mfa_verified"] is True
    assert me["onboarding_required"] is True
    assert me["terms_reaccept_only"] is False

    # Mọi API nghiệp vụ đều bị chặn 403 với X-Onboarding-Required: 1
    for method, url in (
        ("GET", "/api/files"),
        ("GET", "/api/documents"),
        ("GET", "/api/certificates"),
        ("GET", "/api/users/assignable"),
        ("GET", "/api/notifications"),
    ):
        resp = u.request(method, url)
        assert resp.status_code == 403, f"{url} -> {resp.status_code}"
        assert resp.headers.get("X-Onboarding-Required") == "1"
    assert u.post("/api/sign", json={"file_id": "x", "cert_id": 1, "box": [1, 2, 30, 30], "pin": TEST_SIGN_PIN}).status_code == 403

    # Bước 1 validation: thiếu trường -> 400, tự chọn mình làm quản lý -> 400
    assert u.post("/api/onboarding/profile", json={
        "display_name": "Ngô Thị Mới", "job_title": "", "department": "Kế toán",
        "manager_name": "Sếp", "email": "new@example.local",
    }).status_code == 400
    assert u.post("/api/onboarding/profile", json={
        "display_name": "Ngô Thị Mới", "job_title": "Kế toán", "department": "Tài chính",
        "manager_name": "Chính mình", "manager_user_id": u.uid, "email": "new@example.local",
    }).status_code == 400

    # Gợi ý quản lý chứa mgr (đã active) và không chứa bản thân
    mgrs = {m["username"]: m for m in u.get("/api/onboarding/managers").json()}
    assert "mgr_u" in mgrs and "new_u" not in mgrs

    # Lưu bước 1 với manager_user_id thật -> sang bước 2
    r_p = u.post("/api/onboarding/profile", json={
        "display_name": "Ngô Thị Mới",
        "job_title": "Kế toán viên",
        "department": "Phòng Tài chính",
        "manager_name": "Mgr_U",
        "manager_user_id": mgr.uid,
        "email": "New.User@Example.Local",
    })
    assert r_p.status_code == 200, r_p.text
    assert r_p.json()["current_step"] == 2
    assert r_p.json()["profile"]["email"] == "new.user@example.local"

    # Kiểm tra trùng email với user khác -> 409
    u2 = env_light.make_user("dup_u", pin=None, onboarded=False)
    r_dup = u2.post("/api/onboarding/profile", json={
        "display_name": "Trùng Email",
        "job_title": "Nhân viên",
        "department": "Hành chính",
        "manager_name": "Giám đốc (nhập tay)",
        "email": "NEW.USER@example.local",
    })
    assert r_dup.status_code == 409

    # Thoát ra đăng nhập lại -> tiếp tục đúng bước 2 và giữ thông tin đã lưu
    assert u.post("/api/auth/logout").status_code == 200
    u_re = env_light.login("new_u", USER_PASS, u.secret)
    st_re = u_re.get("/api/onboarding/status").json()
    assert st_re["current_step"] == 2
    assert st_re["profile"]["job_title"] == "Kế toán viên"
    assert st_re["profile"]["manager_user_id"] == mgr.uid


def test_onboarding_step2_terms_scroll_totp_evidence_and_reaccept(env_light):
    from app.models import TermsAcceptance

    u = env_light.make_user("terms_u", pin=None, onboarded=False)
    u.post("/api/onboarding/profile", json={
        "display_name": "Trần Văn Điều Khoản", "job_title": "Trưởng nhóm", "department": "Phòng Pháp chế",
        "manager_name": "Giám đốc", "email": "terms@example.local",
    })

    terms = u.get("/api/onboarding/terms").json()
    assert terms["version"] and len(terms["sha256"]) == 64 and "PHÁP CHẾ" in terms["content"].upper()

    env_light.clock.advance()
    good_totp = pyotp.TOTP(u.secret).at(env_light.clock.t)

    # Chưa cuộn hết / chưa tick đồng ý / sai version / sai TOTP đều bị chặn
    assert u.post("/api/onboarding/terms/accept", json={
        "terms_version": terms["version"], "scrolled_to_bottom": False, "agreed": True, "otp_code": good_totp,
    }).status_code == 400
    assert u.post("/api/onboarding/terms/accept", json={
        "terms_version": terms["version"], "scrolled_to_bottom": True, "agreed": False, "otp_code": good_totp,
    }).status_code == 400
    assert u.post("/api/onboarding/terms/accept", json={
        "terms_version": "0.0-old", "scrolled_to_bottom": True, "agreed": True, "otp_code": good_totp,
    }).status_code == 409
    assert u.post("/api/onboarding/terms/accept", json={
        "terms_version": terms["version"], "scrolled_to_bottom": True, "agreed": True, "otp_code": "000000",
    }).status_code == 401

    # Xác nhận hợp lệ kèm User-Agent -> lưu TermsAcceptance & chặn replay TOTP
    r_ok = u.post(
        "/api/onboarding/terms/accept",
        json={"terms_version": terms["version"], "scrolled_to_bottom": True, "agreed": True, "otp_code": good_totp},
        headers={"User-Agent": "TestAgent-Firefox/1.0"},
    )
    assert r_ok.status_code == 200, r_ok.text
    assert r_ok.json()["current_step"] == 3 and r_ok.json()["otp_reuse_valid"] is True

    # Replay đúng mã TOTP vừa dùng phải bị từ chối
    assert u.post("/api/onboarding/terms/accept", json={
        "terms_version": terms["version"], "scrolled_to_bottom": True, "agreed": True, "otp_code": good_totp,
    }).status_code == 401

    sf = env_light.app.state.session_factory
    db = sf()
    try:
        ev = db.scalar(select(TermsAcceptance).where(TermsAcceptance.user_id == u.uid))
        assert ev is not None
        assert ev.terms_version == terms["version"]
        assert ev.terms_sha256 == terms["sha256"]
        assert ev.otp_method == "totp"
        assert ev.user_agent == "TestAgent-Firefox/1.0"
        assert ev.ip
    finally:
        db.close()

    # Giả sử user đã hoàn tất onboarding, sau đó admin đổi terms_version -> bắt xác nhận lại (terms_reaccept_only)
    env_light.mark_onboarded(u, pin=TEST_SIGN_PIN)
    assert u.get("/api/files").status_code == 200
    env_light.app.state.settings.terms_version = "2.0-approved"

    assert u.get("/api/files").status_code == 403
    me2 = u.get("/api/auth/me").json()
    assert me2["onboarding_required"] is True and me2["terms_reaccept_only"] is True
    st2 = u.get("/api/onboarding/status").json()
    assert st2["current_step"] == 2 and st2["terms_reaccept_only"] is True

    env_light.clock.advance()
    totp2 = pyotp.TOTP(u.secret).at(env_light.clock.t)
    r_re = u.post("/api/onboarding/terms/accept", json={
        "terms_version": "2.0-approved", "scrolled_to_bottom": True, "agreed": True, "otp_code": totp2,
    })
    assert r_re.status_code == 200 and r_re.json()["onboarding_required"] is False
    assert u.get("/api/files").status_code == 200


def test_onboarding_email_otp_expiry_single_use_and_bruteforce(env_light, monkeypatch):
    import re
    import app.routes_onboarding as ro
    from app.models import User

    u = env_light.make_user("emailotp_u", pin=None, onboarded=False)
    # Tắt TOTP trên user để kiểm thử nhánh fallback OTP email (khi không có 2FA)
    sf = env_light.app.state.session_factory
    db = sf()
    try:
        row = db.get(User, u.uid)
        row.totp_enabled = False
        db.commit()
    finally:
        db.close()

    u.post("/api/onboarding/profile", json={
        "display_name": "Phạm Email OTP", "job_title": "Chuyên viên", "department": "Nhân sự",
        "manager_name": "Trưởng phòng", "email": "emailotp@example.local",
    })
    terms = u.get("/api/onboarding/terms").json()

    # Không có SMTP -> otp_method="none", gửi OTP báo 503
    assert u.get("/api/onboarding/status").json()["otp_method"] == "none"
    assert u.post("/api/onboarding/terms/send-otp").status_code == 503

    # Bật SMTP giả lập
    sent_mails: list[dict] = []

    def _fake_send(st, to_email, subject, body):  # noqa: ARG001
        m = re.search(r"\b(\d{6})\b", body)
        sent_mails.append({"to": to_email, "subject": subject, "body": body, "otp": m.group(1) if m else ""})
        return True

    monkeypatch.setattr(ro, "send_email_smtp", _fake_send)
    env_light.app.state.settings.smtp_host = "mail.internal.local"
    assert u.get("/api/onboarding/status").json()["otp_method"] == "email_otp"

    r_s1 = u.post("/api/onboarding/terms/send-otp")
    assert r_s1.status_code == 200 and len(sent_mails) == 1
    otp1 = sent_mails[-1]["otp"]
    assert len(otp1) == 6 and otp1 not in r_s1.text

    # Sai 5 lần -> khóa mã OTP hiện tại (423)
    for _ in range(4):
        assert u.post("/api/onboarding/terms/accept", json={
            "terms_version": terms["version"], "scrolled_to_bottom": True, "agreed": True, "otp_code": "000001",
        }).status_code == 401
    assert u.post("/api/onboarding/terms/accept", json={
        "terms_version": terms["version"], "scrolled_to_bottom": True, "agreed": True, "otp_code": "000001",
    }).status_code == 423
    # Dùng lại otp1 lúc này cũng không còn hiệu lực
    assert u.post("/api/onboarding/terms/accept", json={
        "terms_version": terms["version"], "scrolled_to_bottom": True, "agreed": True, "otp_code": otp1,
    }).status_code == 401

    # Xin mã OTP mới, cho trôi > 5 phút -> hết hạn (401)
    assert u.post("/api/onboarding/terms/send-otp").status_code == 200
    otp2 = sent_mails[-1]["otp"]
    env_light.clock.advance(5 * 60 + 2)
    assert u.post("/api/onboarding/terms/accept", json={
        "terms_version": terms["version"], "scrolled_to_bottom": True, "agreed": True, "otp_code": otp2,
    }).status_code == 401

    # Xin mã OTP thứ 3 và xác nhận trong hạn -> thành công, dùng lại -> 401
    assert u.post("/api/onboarding/terms/send-otp").status_code == 200
    otp3 = sent_mails[-1]["otp"]
    r_ok = u.post("/api/onboarding/terms/accept", json={
        "terms_version": terms["version"], "scrolled_to_bottom": True, "agreed": True, "otp_code": otp3,
    })
    assert r_ok.status_code == 200 and r_ok.json()["current_step"] == 3
    assert u.post("/api/onboarding/terms/accept", json={
        "terms_version": terms["version"], "scrolled_to_bottom": True, "agreed": True, "otp_code": otp3,
    }).status_code == 401

    audits = env_light.admin.get("/api/admin/audit?limit=100").json()
    dump = json.dumps(audits)
    assert otp1 not in dump and otp2 not in dump and otp3 not in dump


def test_onboarding_step3_signature_image_validation_bg_removal_and_retention(env_light):
    from datetime import timedelta
    from app.cleanup import purge_expired
    from app.models import StoredFile, utcnow

    u = env_light.make_user("sigimg_u", pin=None, onboarded=False)
    u.post("/api/onboarding/profile", json={
        "display_name": "Lê Ảnh Ký", "job_title": "Kỹ sư", "department": "Kỹ thuật",
        "manager_name": "Quản lý", "email": "sig@example.local",
    })
    terms = u.get("/api/onboarding/terms").json()
    env_light.clock.advance()
    u.post("/api/onboarding/terms/accept", json={
        "terms_version": terms["version"], "scrolled_to_bottom": True, "agreed": True,
        "otp_code": pyotp.TOTP(u.secret).at(env_light.clock.t),
    })

    # File giả mạo / GIF / quá dung lượng / trắng trơn đều bị từ chối
    assert u.post("/api/onboarding/signature-image/preview", files={
        "file": ("fake.png", b"not-a-png-payload", "image/png"),
    }).status_code == 400
    assert u.post("/api/onboarding/signature-image/preview", files={
        "file": ("too_big.png", b"\x89PNG" + b"0" * (2 * 1024 * 1024 + 10), "image/png"),
    }).status_code == 413
    blank = io.BytesIO()
    Image.new("RGB", (100, 50), (255, 255, 255)).save(blank, format="PNG")
    assert u.post("/api/onboarding/signature-image/preview", files={
        "file": ("blank.png", blank.getvalue(), "image/png"),
    }, data={"remove_bg": "true", "sensitivity": "55"}).status_code == 400

    # Preview JPEG có EXIF + nền ngả trắng -> PNG RGBA trong suốt, mất EXIF, xoay 90°, CHƯA lưu vào files
    jpeg = _make_signature_jpeg_with_exif()
    rp = u.post(
        "/api/onboarding/signature-image/preview",
        files={"file": ("sig.jpg", jpeg, "image/jpeg")},
        data={"remove_bg": "true", "sensitivity": "60", "rotate": "90"},
    )
    assert rp.status_code == 200, rp.text
    pj = rp.json()
    assert pj["preview_data_url"].startswith("data:image/png;base64,")
    png_out = base64.b64decode(pj["preview_data_url"].split(",", 1)[1])
    im_out = Image.open(io.BytesIO(png_out))
    assert im_out.format == "PNG" and im_out.mode == "RGBA"
    assert len(im_out.getexif()) == 0
    # Nền góc trong suốt (alpha=0) và có pixel nét chữ rõ (alpha>200); kích thước cắt sát nhỏ hơn gốc sau xoay
    assert im_out.getpixel((0, 0))[3] == 0
    assert im_out.getchannel("A").getextrema()[1] >= 200

    # Tắt xóa nền -> giữ nền đục
    rp_opaque = u.post(
        "/api/onboarding/signature-image/preview",
        files={"file": ("sig.jpg", jpeg, "image/jpeg")},
        data={"remove_bg": "false", "sensitivity": "55", "rotate": "0"},
    )
    im_opaque = Image.open(io.BytesIO(base64.b64decode(rp_opaque.json()["preview_data_url"].split(",", 1)[1])))
    assert im_opaque.getpixel((0, 0))[3] == 255

    # Preview KHÔNG lưu gì vào DB `files`
    sf = env_light.app.state.session_factory
    db = sf()
    try:
        assert db.scalars(select(StoredFile).where(StoredFile.owner_id == u.uid)).all() == []
    finally:
        db.close()

    # Bấm "Dùng ảnh này" (/save) -> lưu file và chuyển step=4
    rs = u.post(
        "/api/onboarding/signature-image/save",
        files={"file": ("sig.jpg", jpeg, "image/jpeg")},
        data={"remove_bg": "true", "sensitivity": "60", "rotate": "0"},
    )
    assert rs.status_code == 200, rs.text
    fid = rs.json()["file_id"]
    assert rs.json()["status"]["current_step"] == 4
    assert u.get("/api/onboarding/signature-image").status_code == 200

    # Ảnh chữ ký mặc định không bị purge_expired xóa sau 7 ngày
    db = sf()
    try:
        db.get(StoredFile, fid).expires_at = utcnow() - timedelta(days=30)
        db.commit()
    finally:
        db.close()
    assert purge_expired(env_light.app.state.settings, sf) == 0


def test_onboarding_step4_atomic_pin_reuse_no_dup_cert_email_and_admin_reset(tmp_path, monkeypatch):
    import app.routes_onboarding as ro
    from app.models import Certificate, User, UserCertificate

    env = build_env(tmp_path, with_services=False, ca_url="http://ca:9001", ca_token="tok")

    # 1. User mới chưa có PIN, chưa có cert; giả lập CA lỗi trước -> KHÔNG được lưu nửa vời
    u1 = env.make_user("onb_u1", pin=None, onboarded=False)
    terms, _ = _complete_step1_to_3(
        env, u1, username="onb_u1", email="onb1@example.local",
        display_name="Vũ Minh Khôi", job_title="Trưởng phòng", department="Phòng Kế hoạch",
    )

    monkeypatch.setattr(ro, "ca_issue", lambda st, kref, cn, days=365: (_ for _ in ()).throw(
        __import__("fastapi").HTTPException(503, "CA tạm thời bảo trì")
    ))
    r_ca_err = u1.post("/api/onboarding/complete", json={"new_pin": "371946"})
    assert r_ca_err.status_code == 503
    me_half = u1.get("/api/auth/me").json()
    assert me_half["onboarding_required"] is True and me_half["has_signing_pin"] is False

    # 2. Bật CA + SMTP giả lập; PIN yếu bị chặn đúng luật cũ; sau đó hoàn tất bằng cửa sổ OTP Bước 2 (15 phút)
    ca_calls: list[dict] = []

    def _fake_ca_issue(st, key_ref, cn, days=365):  # noqa: ARG001
        ca_calls.append({"key_ref": key_ref, "cn": cn, "days": days})
        return {"key_ref": key_ref, "subject": cn, "serial": f"cc{len(ca_calls):04d}", "not_after": "2028-11-19T00:00:00+00:00"}

    monkeypatch.setattr(ro, "ca_issue", _fake_ca_issue)
    sent_emails: list[dict] = []
    monkeypatch.setattr(ro, "dispatch_emails", lambda st, items: sent_emails.extend(
        [{"to": e, "subject": s, "body": b} for e, s, b in items]
    ))

    # PIN yếu bị từ chối theo luật hiện có (400), không lộ giá trị PIN
    r_weak = u1.post("/api/onboarding/complete", json={"new_pin": "123456"})
    assert r_weak.status_code == 400 and "123456" not in r_weak.text

    r_done = u1.post("/api/onboarding/complete", json={"new_pin": "371946"})
    assert r_done.status_code == 200, r_done.text
    dj = r_done.json()
    assert dj["ok"] is True and dj["pin_created"] is True and dj["cert_issued"] is True
    assert len(ca_calls) == 1 and ca_calls[0]["cn"] == "Vũ Minh Khôi"
    assert len(sent_emails) == 1
    assert "cc0001" in sent_emails[0]["body"] and "2028-11-19" in sent_emails[0]["body"]
    assert "371946" not in sent_emails[0]["body"]

    # Đã mở khóa đầy đủ + profile hiển thị trong assignable + giữ được ảnh chữ ký mặc định
    assert u1.get("/api/files").status_code == 200
    me_ok = u1.get("/api/auth/me").json()
    assert me_ok["job_title"] == "Trưởng phòng" and me_ok["department"] == "Phòng Kế hoạch"
    assert u1.get(f"/api/signature-images/{me_ok['signature_image_file_id']}").status_code == 200
    assignable = {x["username"]: x for x in u1.get("/api/users/assignable").json()}
    assert assignable["onb_u1"]["job_title"] == "Trưởng phòng" and assignable["onb_u1"]["department"] == "Phòng Kế hoạch"

    # 3. Nếu quá cửa sổ tái sử dụng OTP (> 15 phút) khi đặt PIN ở Bước 4 -> phải nhập lại TOTP mới
    u2 = env.make_user("onb_u2", pin=None, onboarded=False)
    _complete_step1_to_3(env, u2, username="onb_u2", email="onb2@example.local")
    env.clock.advance(16 * 60)
    r_exp = u2.post("/api/onboarding/complete", json={"new_pin": "482615"})
    assert r_exp.status_code == 401 and r_exp.headers.get("X-Onboarding-Otp-Required") == "1"
    env.clock.advance()
    r_fresh = u2.post("/api/onboarding/complete", json={
        "new_pin": "482615", "otp_code": pyotp.TOTP(u2.secret).at(env.clock.t),
    })
    assert r_fresh.status_code == 200 and len(ca_calls) == 2

    # 4. User cũ đã có cert + PIN nhưng chưa onboarding -> bỏ qua đặt PIN & không cấp cert trùng!
    u3 = env.make_user("legacy_u3", pin="593728", onboarded=True)
    sf = env.app.state.session_factory
    db = sf()
    try:
        cert = Certificate(key_ref="legacy_cert_u3", subject="Legacy U3", serial="dd9999", not_after="2030-01-01T00:00:00+00:00")
        db.add(cert)
        db.flush()
        db.add(UserCertificate(user_id=u3.uid, cert_id=cert.id))
        row3 = db.get(User, u3.uid)
        row3.onboarding_completed_at = None
        row3.accepted_terms_version = None
        row3.onboarding_step = 1
        db.commit()
    finally:
        db.close()

    _complete_step1_to_3(env, u3, username="legacy_u3", email="u3@example.local")
    ca_before = len(ca_calls)
    r_u3 = u3.post("/api/onboarding/complete", json={})  # không truyền new_pin vẫn hoàn tất
    assert r_u3.status_code == 200, r_u3.text
    assert r_u3.json()["pin_created"] is False and r_u3.json()["cert_issued"] is False
    assert r_u3.json()["certificate"]["key_ref"] == "legacy_cert_u3"
    assert len(ca_calls) == ca_before  # không cấp trùng cert

    # 5. Admin reset onboarding & reset PIN
    assert u1.post(f"/api/admin/users/{u3.uid}/reset-onboarding").status_code == 403
    assert env.admin.post(f"/api/admin/users/{u3.uid}/reset-onboarding").status_code == 200
    assert u3.get("/api/files").status_code == 403
    assert u3.get("/api/auth/me").json()["onboarding_required"] is True

    assert env.admin.post(f"/api/admin/users/{u1.uid}/reset-pin").status_code == 200
    assert u1.get("/api/auth/me").json()["has_signing_pin"] is False

    # 6. Audit logs đầy đủ và không rò PIN
    audits = env.admin.get("/api/admin/audit?limit=200").json()
    acts = {a["action"] for a in audits}
    assert {
        "onboarding_step1_profile", "onboarding_terms_accepted", "onboarding_step3_signature",
        "onboarding_completed", "admin_reset_onboarding", "admin_reset_pin",
    } <= acts
    dump = json.dumps(audits)
    for secret_pin in ("371946", "482615", "593728"):
        assert secret_pin not in dump
