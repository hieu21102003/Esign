"""Kiểm thử Milestone W3: Thông báo trong app, Email SMTP tùy chọn, Từ chối, Hủy, Timeline & cô lập docker-compose.test.yml."""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import smtplib
import socket
import ssl
import threading

import pytest
import yaml
from pypdf import PdfWriter
from sqlalchemy import select

from conftest import ROOT, TEST_SIGN_PIN, build_env


def _make_pdf() -> bytes:
    w = PdfWriter()
    w.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _upload_pdf(api, name: str = "doc.pdf") -> str:
    r = api.post("/api/files", files={"file": (name, _make_pdf(), "application/pdf")})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _assign_cert_db(env, user_id: int, key_ref: str) -> int:
    from app.models import Certificate, UserCertificate

    sf = env.app.state.session_factory
    db = sf()
    try:
        cert = db.scalar(select(Certificate).where(Certificate.key_ref == key_ref))
        if cert is None:
            cert = Certificate(
                key_ref=key_ref, subject=key_ref.upper(), serial=f"20{user_id}",
                not_after="2030-01-01T00:00:00+00:00", active=True,
            )
            db.add(cert)
            db.flush()
        if db.get(UserCertificate, (user_id, cert.id)) is None:
            db.add(UserCertificate(user_id=user_id, cert_id=cert.id))
        db.commit()
        return cert.id
    finally:
        db.close()


def _sign_body(cert_id: int, **kw) -> dict:
    body = {
        "cert_id": cert_id, "mode": "text", "page": 1, "box": [40, 40, 240, 100],
        "reason": "Đồng ý", "show": ["name", "date", "reason"], "level": "B-T",
        "pin": TEST_SIGN_PIN,
    }
    body.update(kw)
    return body


def _patch_fake_signer(monkeypatch):
    import app.routes_workflow as rw

    def _fake_sign(st, payload):  # noqa: ARG001
        pdf = base64.b64decode(payload["pdf_b64"])
        return pdf + f"\n%ESIGN-SIG:{payload['key_ref']}:{payload.get('reason') or '-'}\n".encode("utf-8")

    monkeypatch.setattr(rw, "signer_sign", _fake_sign)
    monkeypatch.setattr(rw, "signer_verify", lambda st, pdf: [])


def test_w3_notifications_assign_complete_and_read_controls(env_light, monkeypatch):
    _patch_fake_signer(monkeypatch)
    ua = env_light.make_user("notif_a")
    ub = env_light.make_user("notif_b")
    uc = env_light.make_user("notif_c")
    cb = _assign_cert_db(env_light, ub.uid, "cert_nb")
    cc = _assign_cert_db(env_light, uc.uid, "cert_nc")

    fid = _upload_pdf(ua, "thong-bao.pdf")
    doc_id = ua.post("/api/documents", json={
        "file_id": fid, "title": "Công văn số 10", "assignee_ids": [ub.uid],
    }).json()["id"]

    # B nhận 1 notification assigned
    assert ub.get("/api/notifications/unread-count").json()["unread_count"] == 1
    nb = ub.get("/api/notifications").json()
    assert nb["unread_count"] == 1 and len(nb["items"]) == 1
    nid = nb["items"][0]["id"]
    assert nb["items"][0]["kind"] == "assigned" and nb["items"][0]["document_id"] == doc_id
    assert nb["items"][0]["is_read"] is False

    # C không thể đánh dấu notification của B (IDOR)
    assert uc.post(f"/api/notifications/{nid}/read").status_code == 404

    # B đánh dấu đã đọc 1 thông báo
    mr = ub.post(f"/api/notifications/{nid}/read").json()
    assert mr["ok"] is True and mr["unread_count"] == 0
    assert ub.get("/api/notifications?unread_only=true").json()["items"] == []

    # B ký và assign cho C
    assert ub.post(f"/api/documents/{doc_id}/sign", json=_sign_body(
        cb, next_action="assign", next_assignee_ids=[uc.uid],
    )).status_code == 200
    assert uc.get("/api/notifications/unread-count").json()["unread_count"] == 1

    # C ký và hoàn tất -> thông báo cho A (creator) & B (người đã ký)
    assert uc.post(f"/api/documents/{doc_id}/sign", json=_sign_body(cc, next_action="complete")).status_code == 200
    na = ua.get("/api/notifications").json()
    assert na["unread_count"] == 1 and na["items"][0]["kind"] == "completed"
    nb2 = ub.get("/api/notifications").json()
    assert nb2["unread_count"] == 1 and nb2["items"][0]["kind"] == "completed"

    # Read-all
    ra = ua.post("/api/notifications/read-all").json()
    assert ra["ok"] is True and ra["marked"] >= 1 and ra["unread_count"] == 0
    assert ua.get("/api/notifications/unread-count").json()["unread_count"] == 0


def test_w3_reject_stops_workflow_notifies_and_populates_timeline(env_light, monkeypatch):
    _patch_fake_signer(monkeypatch)
    ua = env_light.make_user("rej_a")
    ub = env_light.make_user("rej_b")
    uc = env_light.make_user("rej_c")
    ue = env_light.make_user("rej_outsider")
    cb = _assign_cert_db(env_light, ub.uid, "cert_rb")
    cc = _assign_cert_db(env_light, uc.uid, "cert_rc")

    fid = _upload_pdf(ua, "reject.pdf")
    doc_id = ua.post("/api/documents", json={
        "file_id": fid, "title": "Hợp đồng kiểm tra từ chối", "assignee_ids": [ub.uid, uc.uid], "mode": "all",
    }).json()["id"]

    # B ký trước
    assert ub.post(f"/api/documents/{doc_id}/sign", json=_sign_body(cb)).status_code == 200

    # Lý do từ chối rỗng -> 422
    assert uc.post(f"/api/documents/{doc_id}/reject", json={"reason": "  \n ", "pin": TEST_SIGN_PIN}).status_code == 422

    # C từ chối với lý do bắt buộc -> luồng dừng
    rj = uc.post(f"/api/documents/{doc_id}/reject", json={"reason": "Sai số liệu phụ lục", "pin": TEST_SIGN_PIN})
    assert rj.status_code == 200, rj.text
    doc = rj.json()
    assert doc["status"] == "rejected" and doc["rejected_at"] is not None
    assert doc["steps"][0]["status"] == "rejected"
    by_uid = {a["user"]["id"]: a for a in doc["steps"][0]["assignees"]}
    assert by_uid[ub.uid]["status"] == "signed"
    assert by_uid[uc.uid]["status"] == "rejected" and by_uid[uc.uid]["remark"] == "Sai số liệu phụ lục"

    # Không thể ký, assign, hoàn tất hoặc hủy sau khi bị từ chối
    assert ub.post(f"/api/documents/{doc_id}/sign", json=_sign_body(cb)).status_code == 409
    assert uc.post(f"/api/documents/{doc_id}/assign", json={"assignee_ids": [ub.uid]}).status_code == 409
    assert uc.post(f"/api/documents/{doc_id}/complete").status_code == 409
    assert ua.post(f"/api/documents/{doc_id}/cancel", json={"pin": TEST_SIGN_PIN}).status_code == 409

    # A & B nhận thông báo từ chối
    na_kinds = [n["kind"] for n in ua.get("/api/notifications").json()["items"]]
    nb_items = ub.get("/api/notifications").json()["items"]
    assert "rejected" in na_kinds
    assert any(n["kind"] == "rejected" and "Sai số liệu phụ lục" in n["message"] for n in nb_items)

    # Mở xem tài liệu ghi nhận doc_view; ?track=0 không ghi thêm doc_view
    assert ua.get(f"/api/documents/{doc_id}").status_code == 200
    tl1 = ua.get(f"/api/documents/{doc_id}/timeline").json()
    assert ua.get(f"/api/documents/{doc_id}?track=0").status_code == 200
    tl2 = ua.get(f"/api/documents/{doc_id}/timeline").json()
    assert len(tl2["events"]) == len(tl1["events"])

    actions = [e["action"] for e in tl1["events"]]
    for expected in ("doc_create", "doc_assign", "doc_sign", "doc_reject", "doc_view"):
        assert expected in actions
    assert ue.get(f"/api/documents/{doc_id}/timeline").status_code == 404


def test_w3_cancel_permissions_and_notifications(env_light, monkeypatch):
    _patch_fake_signer(monkeypatch)
    ua = env_light.make_user("can_a")
    ub = env_light.make_user("can_b")
    ue = env_light.make_user("can_e")
    cb = _assign_cert_db(env_light, ub.uid, "cert_can_b")

    fid1 = _upload_pdf(ua, "c1.pdf")
    doc1 = ua.post("/api/documents", json={"file_id": fid1, "title": "Hủy luồng 1", "assignee_ids": [ub.uid]}).json()["id"]

    # Người được assign không phải người tạo không thể hủy (403); người ngoài -> 404
    assert ub.post(f"/api/documents/{doc1}/cancel", json={"reason": "Không được", "pin": TEST_SIGN_PIN}).status_code == 403
    assert ue.post(f"/api/documents/{doc1}/cancel", json={"pin": TEST_SIGN_PIN}).status_code == 404

    # Người tạo hủy -> cancelled, B chuyển skipped và nhận notification
    rc = ua.post(f"/api/documents/{doc1}/cancel", json={"reason": "Cập nhật nội dung mới", "pin": TEST_SIGN_PIN})
    assert rc.status_code == 200, rc.text
    d1 = rc.json()
    assert d1["status"] == "cancelled" and d1["cancel_reason"] == "Cập nhật nội dung mới"
    assert d1["steps"][0]["status"] == "cancelled"
    assert d1["steps"][0]["assignees"][0]["status"] == "skipped"
    assert ub.post(f"/api/documents/{doc1}/sign", json=_sign_body(cb)).status_code == 409
    assert any(n["kind"] == "cancelled" for n in ub.get("/api/notifications").json()["items"])

    # Admin hủy được luồng của người khác; luồng đã hoàn tất không thể hủy
    fid2 = _upload_pdf(ua, "c2.pdf")
    doc2 = ua.post("/api/documents", json={"file_id": fid2, "title": "Hủy bởi admin", "assignee_ids": [ub.uid]}).json()["id"]
    assert env_light.admin.post(f"/api/documents/{doc2}/cancel", json={"pin": TEST_SIGN_PIN}).status_code == 200

    fid3 = _upload_pdf(ua, "c3.pdf")
    doc3 = ua.post("/api/documents", json={"file_id": fid3, "title": "Đã hoàn tất", "assignee_ids": [ub.uid]}).json()["id"]
    assert ub.post(f"/api/documents/{doc3}/sign", json=_sign_body(cb, next_action="complete")).status_code == 200
    assert ua.post(f"/api/documents/{doc3}/cancel", json={"pin": TEST_SIGN_PIN}).status_code == 409


def test_w3_optional_smtp_noop_and_mocked_send_and_failure_safety(env_light, monkeypatch):
    from app.notifications import dispatch_emails, send_email_smtp

    ua = env_light.make_user("smtp_a")
    ub = env_light.make_user("smtp_b")
    st = env_light.app.state.settings

    # Quản lý email user qua admin API
    assert env_light.admin.put(f"/api/admin/users/{ub.uid}/email", json={"email": "khong-hop-le"}).status_code == 400
    r_em = env_light.admin.put(f"/api/admin/users/{ub.uid}/email", json={"email": "  Signer.B@Example.COM  "})
    assert r_em.status_code == 200 and r_em.json()["email"] == "signer.b@example.com"

    # SMTP chưa cấu hình -> trả False êm đẹp, không lỗi
    assert st.smtp_host == ""
    assert send_email_smtp(st, "signer.b@example.com", "Chủ đề", "Nội dung") is False

    # Giả lập cấu hình SMTP -> kiểm tra gửi qua smtplib.SMTP & SSLContext nghiêm ngặt
    sent: list[dict] = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=10):
            sent.append({"host": host, "port": port, "timeout": timeout})

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def ehlo(self):
            sent.append({"ehlo": True})

        def starttls(self, *, context=None):
            sent.append({"starttls": True, "context": context})

        def login(self, user, pw):
            sent.append({"login": (user, pw)})

        def send_message(self, msg):
            sent.append({"to": msg["To"], "subject": msg["Subject"], "body": msg.get_content()})

    class FakeSMTPSSL(FakeSMTP):
        def __init__(self, host, port, timeout=10, context=None):
            sent.append({"host": host, "port": port, "timeout": timeout, "ssl_context": context})

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTPSSL)
    st.smtp_host = "mail.internal.local"
    st.smtp_port = 587
    st.smtp_user = "bot@internal.local"
    st.smtp_pass = "SuperSecretMailPw!"
    st.smtp_from = "esign@internal.local"
    st.smtp_tls = True

    done_evt = threading.Event()
    orig_send = send_email_smtp

    def _wrapped(s, to_e, subj, body):
        ok = orig_send(s, to_e, subj, body)
        done_evt.set()
        return ok

    import app.notifications as notif_mod
    monkeypatch.setattr(notif_mod, "send_email_smtp", _wrapped)

    fid = _upload_pdf(ua, "mail.pdf")
    r_doc = ua.post("/api/documents", json={"file_id": fid, "title": "Test gửi mail", "assignee_ids": [ub.uid]})
    assert r_doc.status_code == 201
    assert done_evt.wait(timeout=3)
    stls = next(x for x in sent if x.get("starttls"))
    ctx = stls["context"]
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True
    assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2
    msg_item = next(x for x in sent if "subject" in x)
    assert msg_item["to"] == "signer.b@example.com"
    assert msg_item["subject"] == "[Ký số] Bạn được giao ký tài liệu: Test gửi mail"
    assert "/documents/" not in msg_item["body"] and "Mở tài liệu:" not in msg_item["body"]

    # Kiểm tra cổng 465 SMTP_SSL cũng truyền SSLContext kiểm tra chứng chỉ đầy đủ
    sent.clear()
    st.smtp_port = 465
    assert orig_send(st, "signer.b@example.com", "[Ký số] SSL 465", "Body 465") is True
    ssl_item = next(x for x in sent if "ssl_context" in x)
    ctx_465 = ssl_item["ssl_context"]
    assert isinstance(ctx_465, ssl.SSLContext)
    assert ctx_465.verify_mode == ssl.CERT_REQUIRED and ctx_465.check_hostname is True
    assert ctx_465.minimum_version >= ssl.TLSVersion.TLSv1_2
    st.smtp_port = 587

    # Giả lập lỗi kết nối SMTP -> không ném exception, dispatch vẫn an toàn
    class BrokenSMTP:
        def __init__(self, *a, **kw):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(smtplib, "SMTP", BrokenSMTP)
    assert orig_send(st, "signer.b@example.com", "Test lỗi", "Body") is False
    dispatch_emails(st, [("signer.b@example.com", "Test lỗi", "Body")])


def test_smtp_secret_file_priority_and_app_base_url(tmp_path, monkeypatch):
    from app.config import Settings
    import app.routes_workflow as rw

    # 1. Kiểm tra SMTP_PASS_FILE ưu tiên hơn SMTP_PASS và fallback khi rỗng/thiếu
    sec_file = tmp_path / "smtp_pass"
    sec_file.write_text("FileSecretPw!\r\n", encoding="utf-8")
    monkeypatch.setenv("SMTP_PASS_FILE", str(sec_file))
    monkeypatch.setenv("SMTP_PASS", "EnvFallbackPw!")
    monkeypatch.setenv("APP_BASE_URL", "https://esign.smart-rail.vn/")
    loaded = Settings.from_env()
    assert loaded.smtp_pass == "FileSecretPw!"
    assert loaded.app_base_url == "https://esign.smart-rail.vn/"

    sec_file.write_text("", encoding="utf-8")
    assert Settings.from_env().smtp_pass == "EnvFallbackPw!"

    monkeypatch.setenv("SMTP_PASS_FILE", str(tmp_path / "missing_secret"))
    assert Settings.from_env().smtp_pass == "EnvFallbackPw!"

    monkeypatch.delenv("SMTP_PASS", raising=False)
    assert Settings.from_env().smtp_pass == ""

    # 2. APP_BASE_URL cấu hình -> kèm link không token, bỏ qua spoofed Host header; để trống/sai -> không kèm link
    env = build_env(tmp_path / "app_url", with_services=False)
    _patch_fake_signer(monkeypatch)
    ua = env.make_user("url_a")
    ub = env.make_user("url_b")
    cb = _assign_cert_db(env, ub.uid, "cert_url_b")
    assert env.admin.put(f"/api/admin/users/{ua.uid}/email", json={"email": "url.a@smart-rail.vn"}).status_code == 200
    assert env.admin.put(f"/api/admin/users/{ub.uid}/email", json={"email": "url.b@smart-rail.vn"}).status_code == 200

    st = env.app.state.settings
    st.smtp_host = "gir05.nhanhoa.com"
    st.smtp_port = 587
    st.smtp_user = "info@smart-rail.vn"
    st.smtp_pass = "MailSecret123"
    st.smtp_from = "info@smart-rail.vn"
    st.smtp_tls = True
    st.app_base_url = "  https://esign.smart-rail.vn/  "

    captured: list[tuple[str, str, str]] = []
    monkeypatch.setattr(rw, "dispatch_emails", lambda _st, items: captured.extend(items))

    fid1 = _upload_pdf(ua, "hop-dong-bao-tri.pdf")
    r1 = ua.post(
        "/api/documents",
        json={"file_id": fid1, "title": "Hợp đồng bảo trì", "assignee_ids": [ub.uid]},
        headers={"Host": "evil.attacker.example"},
    )
    assert r1.status_code == 201, r1.text
    doc1 = r1.json()["id"]
    to_e, subj, body = captured[-1]
    assert to_e == "url.b@smart-rail.vn"
    assert subj == "[Ký số] Bạn được giao ký tài liệu: Hợp đồng bảo trì"
    assert f"https://esign.smart-rail.vn/documents/{doc1}" in body
    assert "evil.attacker.example" not in body and "?token=" not in body

    r1_sign = ub.post(f"/api/documents/{doc1}/sign", json=_sign_body(cb, next_action="complete"))
    assert r1_sign.status_code == 200
    to_e, subj, body = captured[-1]
    assert subj == "[Ký số] Tài liệu đã hoàn tất: Hợp đồng bảo trì"
    assert f"https://esign.smart-rail.vn/documents/{doc1}" in body

    fid2 = _upload_pdf(ua, "tu-choi.pdf")
    doc2 = ua.post("/api/documents", json={"file_id": fid2, "title": "Phiếu trình", "assignee_ids": [ub.uid]}).json()["id"]
    assert ub.post(f"/api/documents/{doc2}/reject", json={"reason": "Thiếu phụ lục", "pin": TEST_SIGN_PIN}).status_code == 200
    assert captured[-1][1] == "[Ký số] Tài liệu bị từ chối: Phiếu trình"
    assert f"https://esign.smart-rail.vn/documents/{doc2}" in captured[-1][2]

    fid3 = _upload_pdf(ua, "huy-luong.pdf")
    doc3 = ua.post("/api/documents", json={"file_id": fid3, "title": "Biên bản", "assignee_ids": [ub.uid]}).json()["id"]
    assert ua.post(f"/api/documents/{doc3}/cancel", json={"reason": "Sửa số liệu", "pin": TEST_SIGN_PIN}).status_code == 200
    assert captured[-1][1] == "[Ký số] Luồng ký đã bị hủy: Biên bản"
    assert f"https://esign.smart-rail.vn/documents/{doc3}" in captured[-1][2]

    # APP_BASE_URL trống hoặc không hợp lệ -> gửi mail bình thường, không chèn link
    for bad_url in ("", "ftp://bad.local", "https://user:pw@esign.smart-rail.vn", "https://esign.smart-rail.vn?x=1"):
        captured.clear()
        st.app_base_url = bad_url
        fid4 = _upload_pdf(ua, "no-link.pdf")
        r4 = ua.post("/api/documents", json={"file_id": fid4, "title": "Tài liệu không link", "assignee_ids": [ub.uid]})
        assert r4.status_code == 201
        assert captured[-1][1] == "[Ký số] Bạn được giao ký tài liệu: Tài liệu không link"
        assert "/documents/" not in captured[-1][2] and "Mở tài liệu:" not in captured[-1][2]


def test_admin_smtp_status_test_button_errors_ratelimit_and_audit(tmp_path, monkeypatch, caplog):
    from conftest import Api

    env = build_env(tmp_path / "smtp_admin", with_services=False, smtp_test_rate=(50, 60))
    u1 = env.make_user("smtp_norm_u1")
    anon = Api(env.app)

    # Phân quyền: chưa đăng nhập (401), user thường (403)
    assert anon.get("/api/admin/smtp/status").status_code == 401
    assert anon.post("/api/admin/smtp/test", json={"to_email": "check@smart-rail.vn"}).status_code == 401
    assert u1.get("/api/admin/smtp/status").status_code == 403
    assert u1.post("/api/admin/smtp/test", json={"to_email": "check@smart-rail.vn"}).status_code == 403

    # Trạng thái khi chưa bật SMTP & gửi thử khi chưa cấu hình
    st0 = env.admin.get("/api/admin/smtp/status").json()
    assert st0["enabled"] is False and st0["tls_mode"] == "none" and st0["password_set"] is False
    assert "smtp_pass" not in st0 and "password" not in st0
    assert env.admin.post("/api/admin/smtp/test", json={"to_email": "not-an-email"}).status_code == 400
    assert env.admin.post("/api/admin/smtp/test", json={"to_email": "   "}).status_code == 400
    r_unconf = env.admin.post("/api/admin/smtp/test", json={"to_email": "admin.test@smart-rail.vn"})
    assert r_unconf.status_code == 200 and r_unconf.json()["ok"] is False and r_unconf.json()["error_code"] == "not_configured"

    # Bật cấu hình SMTP giống Nhân Hòa (gir05.nhanhoa.com:587 STARTTLS)
    st = env.app.state.settings
    secret_pw = "TopSecretNhanHoa#2026"
    st.smtp_host = "gir05.nhanhoa.com"
    st.smtp_port = 587
    st.smtp_user = "info@smart-rail.vn"
    st.smtp_pass = secret_pw
    st.smtp_from = "info@smart-rail.vn"
    st.smtp_tls = True
    st.app_base_url = "https://esign.smart-rail.vn"

    r_st = env.admin.get("/api/admin/smtp/status")
    assert r_st.status_code == 200 and secret_pw not in r_st.text
    st1 = r_st.json()
    assert st1["enabled"] is True and st1["host"] == "gir05.nhanhoa.com" and st1["port"] == 587
    assert st1["tls_mode"] == "STARTTLS" and st1["from_email"] == "info@smart-rail.vn"
    assert st1["password_set"] is True and st1["app_base_url_configured"] is True

    # Kiểm tra phân loại từng nhóm lỗi bằng mock + đảm bảo KHÔNG lộ mật khẩu qua response/audit/log
    def _mock_smtp_factory(*, init_exc=None, starttls_exc=None, login_exc=None, send_exc=None, rec=None):
        class _MockSMTP:
            def __init__(self, host, port, timeout=10):
                if init_exc:
                    raise init_exc
                if rec is not None:
                    rec["init"] = (host, port, timeout)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def ehlo(self):
                pass

            def starttls(self, *, context=None):
                if starttls_exc:
                    raise starttls_exc
                if rec is not None:
                    rec["context"] = context

            def login(self, user, pw):
                if login_exc:
                    raise login_exc
                if rec is not None:
                    rec["login"] = (user, pw)

            def send_message(self, msg):
                if send_exc:
                    raise send_exc
                if rec is not None:
                    rec["to"] = msg["To"]
                    rec["subject"] = msg["Subject"]
                    rec["body"] = msg.get_content()

        return _MockSMTP

    error_cases = [
        ("timeout", dict(init_exc=TimeoutError("timed out")), "Hết thời gian chờ"),
        ("tls_error", dict(starttls_exc=ssl.SSLCertVerificationError(1, "hostname mismatch mail.smart-rail.vn")), "tên host phải trùng tên trong chứng chỉ"),
        ("tls_error", dict(starttls_exc=ssl.SSLError("wrong version number")), "tên host phải trùng tên trong chứng chỉ"),
        ("tls_error", dict(starttls_exc=smtplib.SMTPNotSupportedError("STARTTLS not supported")), "tên host phải trùng tên trong chứng chỉ"),
        ("auth_error", dict(login_exc=smtplib.SMTPAuthenticationError(535, f"5.7.8 Auth failed {secret_pw}".encode())), "sai tài khoản hoặc mật khẩu"),
        ("sender_refused", dict(send_exc=smtplib.SMTPSenderRefused(550, b"sender rejected", "info@smart-rail.vn")), "từ chối địa chỉ người gửi"),
        ("recipient_refused", dict(send_exc=smtplib.SMTPRecipientsRefused({"bad@smart-rail.vn": (550, b"unknown user")})), "từ chối địa chỉ người nhận"),
        ("rejected", dict(send_exc=smtplib.SMTPDataError(554, b"5.7.1 Relay access denied")), "từ chối gửi thư"),
        ("connection_error", dict(init_exc=ConnectionRefusedError("Connection refused")), "Không kết nối được"),
        ("connection_error", dict(init_exc=socket.gaierror("Name or service not known")), "Không kết nối được"),
        ("connection_error", dict(init_exc=smtplib.SMTPConnectError(421, b"Service not available")), "Không kết nối được"),
    ]

    with caplog.at_level(logging.WARNING, logger="esign.notifications"):
        for idx, (exp_code, exc_kw, exp_hint) in enumerate(error_cases):
            caplog.clear()
            monkeypatch.setattr(smtplib, "SMTP", _mock_smtp_factory(**exc_kw))
            r_err = env.admin.post("/api/admin/smtp/test", json={"to_email": f"qa{idx}@smart-rail.vn"})
            assert r_err.status_code == 200, r_err.text
            assert secret_pw not in r_err.text
            body = r_err.json()
            assert body["ok"] is False and body["error_code"] == exp_code
            assert exp_hint in body["message"], f"{exp_code}: {body['message']}"
            assert all(secret_pw not in rec.getMessage() for rec in caplog.records)

    # Mock gửi thành công -> kiểm tra SSLContext strict, nội dung không lộ mật khẩu, và ghi audit
    rec: dict = {}
    monkeypatch.setattr(smtplib, "SMTP", _mock_smtp_factory(rec=rec))
    r_ok = env.admin.post("/api/admin/smtp/test", json={"to_email": "Recipient@Smart-Rail.vn"})
    assert r_ok.status_code == 200 and r_ok.json()["ok"] is True and secret_pw not in r_ok.text
    assert rec["init"] == ("gir05.nhanhoa.com", 587, 10)
    assert isinstance(rec["context"], ssl.SSLContext)
    assert rec["context"].verify_mode == ssl.CERT_REQUIRED and rec["context"].check_hostname is True
    assert rec["context"].minimum_version >= ssl.TLSVersion.TLSv1_2
    assert rec["to"] == "recipient@smart-rail.vn"
    assert "[Ký số]" in rec["subject"] and secret_pw not in rec["body"]

    audits = env.admin.get("/api/admin/audit?action=smtp_test&limit=20").json()
    assert len(audits) == 1 + len(error_cases) + 1
    assert audits[0]["username"] == "admin" and audits[0]["status"] == "ok"
    assert json.loads(audits[0]["metadata_json"]) == {"to_email": "recipient@smart-rail.vn", "ok": True, "error_code": None}
    assert all(secret_pw not in (a.get("detail") or "") and secret_pw not in (a.get("metadata_json") or "") for a in audits)

    # Giới hạn tần suất mặc định 5 lần/phút -> lần thứ 6 bị 429 kèm Retry-After
    env_rl = build_env(tmp_path / "smtp_rl", with_services=False)
    for i in range(5):
        r_rl = env_rl.admin.post("/api/admin/smtp/test", json={"to_email": f"rl{i}@smart-rail.vn"})
        assert r_rl.status_code == 200
    r_429 = env_rl.admin.post("/api/admin/smtp/test", json={"to_email": "rl5@smart-rail.vn"})
    assert r_429.status_code == 429 and "retry-after" in {k.lower(): v for k, v in r_429.headers.items()}


def test_w3_docker_compose_test_isolation():
    path = os.path.join(ROOT, "docker-compose.test.yml")
    raw = open(path, encoding="utf-8").read()
    assert "${" not in raw, "docker-compose.test.yml không được phụ thuộc biến .env (${...})"
    data = yaml.safe_load(raw)
    assert data.get("name") == "esign-test"
    assert "secrets" not in data
    assert not data["services"]["caddy"].get("ports")
    db_vols = [str(v) for v in data["services"]["db"].get("volumes", [])]
    assert not any("pg-data" in v for v in db_vols)
    assert any("/var/lib/postgresql/data" in str(t) for t in data["services"]["db"].get("tmpfs", []))
    api_vols = [str(v) for v in data["services"]["api"].get("volumes", [])]
    assert not any("api-data" in v for v in api_vols)
    assert any("/data" in str(t) for t in data["services"]["api"].get("tmpfs", []))
    assert "TOTP_ENC_KEY" in str(data["services"]["api"].get("entrypoint", ""))
