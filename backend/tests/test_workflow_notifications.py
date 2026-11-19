"""Kiểm thử Milestone W3: Thông báo trong app, Email SMTP tùy chọn, Từ chối, Hủy, Timeline & cô lập docker-compose.test.yml."""
from __future__ import annotations

import base64
import io
import os
import smtplib
import threading

import pytest
import yaml
from pypdf import PdfWriter
from sqlalchemy import select

from conftest import ROOT


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
    assert uc.post(f"/api/documents/{doc_id}/reject", json={"reason": "  \n "}).status_code == 422

    # C từ chối với lý do bắt buộc -> luồng dừng
    rj = uc.post(f"/api/documents/{doc_id}/reject", json={"reason": "Sai số liệu phụ lục"})
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
    assert ua.post(f"/api/documents/{doc_id}/cancel").status_code == 409

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
    assert ub.post(f"/api/documents/{doc1}/cancel", json={"reason": "Không được"}).status_code == 403
    assert ue.post(f"/api/documents/{doc1}/cancel").status_code == 404

    # Người tạo hủy -> cancelled, B chuyển skipped và nhận notification
    rc = ua.post(f"/api/documents/{doc1}/cancel", json={"reason": "Cập nhật nội dung mới"})
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
    assert env_light.admin.post(f"/api/documents/{doc2}/cancel").status_code == 200

    fid3 = _upload_pdf(ua, "c3.pdf")
    doc3 = ua.post("/api/documents", json={"file_id": fid3, "title": "Đã hoàn tất", "assignee_ids": [ub.uid]}).json()["id"]
    assert ub.post(f"/api/documents/{doc3}/sign", json=_sign_body(cb, next_action="complete")).status_code == 200
    assert ua.post(f"/api/documents/{doc3}/cancel").status_code == 409


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

    # Giả lập cấu hình SMTP -> kiểm tra gửi qua smtplib.SMTP
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

        def starttls(self):
            sent.append({"starttls": True})

        def login(self, user, pw):
            sent.append({"login": (user, pw)})

        def send_message(self, msg):
            sent.append({"to": msg["To"], "subject": msg["Subject"]})

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
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
    assert any(x.get("starttls") for x in sent)
    assert any(x.get("to") == "signer.b@example.com" and "Test gửi mail" in x.get("subject", "") for x in sent)

    # Giả lập lỗi kết nối SMTP -> không ném exception, dispatch vẫn an toàn
    class BrokenSMTP:
        def __init__(self, *a, **kw):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(smtplib, "SMTP", BrokenSMTP)
    assert orig_send(st, "signer.b@example.com", "Test lỗi", "Body") is False
    dispatch_emails(st, [("signer.b@example.com", "Test lỗi", "Body")])


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
