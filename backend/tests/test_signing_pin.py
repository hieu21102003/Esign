"""Kiểm thử xác thực PIN ký 6 chữ số: chính sách PIN yếu, TOTP khi đặt/đổi, brute-force lockout, audit."""
from __future__ import annotations

import base64
import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pyotp
import pytest
from pypdf import PdfWriter
from sqlalchemy import select, text

from conftest import TEST_SIGN_PIN, USER_PASS


def _make_pdf() -> bytes:
    w = PdfWriter()
    w.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _upload_pdf(api, name: str = "pin-doc.pdf") -> str:
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
                key_ref=key_ref, subject=key_ref.upper(), serial=f"30{user_id}",
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


def _patch_all_signers(monkeypatch):
    import app.routes_files as rf
    import app.routes_workflow as rw

    calls: list[dict] = []

    def _fake_sign(st, payload):  # noqa: ARG001
        calls.append({"key_ref": payload["key_ref"], "reason": payload.get("reason")})
        pdf = base64.b64decode(payload["pdf_b64"])
        return pdf + f"\n%ESIGN-SIG:{payload['key_ref']}\n".encode("utf-8")

    monkeypatch.setattr(rf, "signer_sign", _fake_sign)
    monkeypatch.setattr(rw, "signer_sign", _fake_sign)
    monkeypatch.setattr(rw, "signer_verify", lambda st, pdf: [])
    return calls


def _sign_file_body(fid: str, cid: int, pin=TEST_SIGN_PIN) -> dict:
    b = {
        "file_id": fid, "cert_id": cid, "mode": "text", "page": 1,
        "box": [40, 40, 240, 100], "reason": "Ký file",
    }
    if pin is not None:
        b["pin"] = pin
    return b


def _sign_doc_body(cid: int, pin=TEST_SIGN_PIN, **kw) -> dict:
    b = {
        "cert_id": cid, "mode": "text", "page": 1, "box": [40, 40, 240, 100],
        "reason": "Ký luồng", "show": ["name", "date", "reason"], "level": "B-T",
    }
    if pin is not None:
        b["pin"] = pin
    b.update(kw)
    return b


def test_signing_pin_policy_rejects_weak_patterns_and_password_match(env_light):
    from app.security import check_signing_pin_policy, hash_password

    for bad in (
        None, "", "12345", "1234567", "12a456", "000000", "111111", "999999",
        "123456", "012345", "654321", "987654", "789012", "210987",
        "121212", "123123", "112233", "123321", "654456", "159753", "147258",
    ):
        assert check_signing_pin_policy(bad) is not None, f"Should reject weak pin: {bad!r}"

    # PIN trùng mật khẩu đăng nhập phải bị từ chối
    same_pw_hash = hash_password("258419")
    assert check_signing_pin_policy("258419", same_pw_hash) is not None
    assert check_signing_pin_policy("258419", hash_password(USER_PASS)) is None

    # Qua API: từ chối PIN yếu (400), không lộ ngược giá trị PIN yếu, không đốt mã TOTP để thử lại ngay được
    u = env_light.make_user("policy_u", pin=None)
    env_light.clock.advance()
    code = pyotp.TOTP(u.secret).at(env_light.clock.t)
    r_weak = u.post("/api/auth/signing-pin", json={"new_pin": "123456", "totp_code": code})
    assert r_weak.status_code == 400
    assert "123456" not in r_weak.text

    # Dùng lại đúng mã TOTP đó cho PIN mạnh phải thành công
    r_ok = u.post("/api/auth/signing-pin", json={"new_pin": "371946", "totp_code": code})
    assert r_ok.status_code == 200 and r_ok.json()["has_signing_pin"] is True


def test_set_and_change_pin_requires_totp_or_recovery_and_hashes_argon2id(env_light, monkeypatch):
    _patch_all_signers(monkeypatch)
    u = env_light.make_user("pin_setup_u", pin=None)
    cid = _assign_cert_db(env_light, u.uid, "cert_setup")
    fid = _upload_pdf(u, "test.pdf")

    assert u.get("/api/auth/me").json()["has_signing_pin"] is False
    assert u.get("/api/auth/signing-pin").json()["has_signing_pin"] is False

    # Không có phiên sau 2FA bị 403
    no_mfa, _ = env_light.login_password("pin_setup_u", USER_PASS)
    assert no_mfa.post("/api/auth/signing-pin", json={"new_pin": "371946", "totp_code": "123456"}).status_code == 403

    # Thiếu hoặc truyền cả 2 mã -> 400; sai mã TOTP -> 401
    assert u.post("/api/auth/signing-pin", json={"new_pin": "371946"}).status_code == 400
    assert u.post("/api/auth/signing-pin", json={
        "new_pin": "371946", "totp_code": "111111", "recovery_code": u.recovery[0],
    }).status_code == 400
    assert u.post("/api/auth/signing-pin", json={"new_pin": "371946", "totp_code": "000000"}).status_code == 401
    assert u.get("/api/auth/me").json()["has_signing_pin"] is False

    # Đặt PIN lần đầu bằng TOTP hợp lệ -> thành công; replay đúng TOTP đó phải bị 401
    env_light.clock.advance()
    good_totp = pyotp.TOTP(u.secret).at(env_light.clock.t)
    r_set = u.post("/api/auth/signing-pin", json={"new_pin": "371946", "totp_code": good_totp})
    assert r_set.status_code == 200 and r_set.json()["has_signing_pin"] is True
    assert u.post("/api/auth/signing-pin", json={"new_pin": "482615", "totp_code": good_totp}).status_code == 401

    # DB lưu hash Argon2id, tuyệt đối không lưu bản rõ
    with env_light.app.state.engine.connect() as con:
        pin_hash = con.execute(
            text("SELECT signing_pin_hash FROM users WHERE username='pin_setup_u'")
        ).scalar_one()
    assert pin_hash.startswith("$argon2id$") and "371946" not in pin_hash

    # Đổi PIN bằng recovery code dùng 1 lần
    rec = u.recovery[0]
    r_chg = u.post("/api/auth/signing-pin", json={"new_pin": "482615", "recovery_code": rec})
    assert r_chg.status_code == 200
    # Recovery code đã dùng không dùng lại được
    assert u.post("/api/auth/signing-pin", json={"new_pin": "593728", "recovery_code": rec}).status_code == 401

    # PIN cũ bị từ chối (401), PIN mới ký thành công (200)
    assert u.post("/api/sign", json=_sign_file_body(fid, cid, pin="371946")).status_code == 401
    r_sign = u.post("/api/sign", json=_sign_file_body(fid, cid, pin="482615"))
    assert r_sign.status_code == 200, r_sign.text

    # Audit log có đủ pin_set, pin_change, pin_change_denied và không rò bản rõ PIN
    audits = env_light.admin.get("/api/admin/audit?limit=100").json()
    acts = {a["action"] for a in audits}
    assert {"pin_set", "pin_change", "pin_change_denied"} <= acts
    dump = json.dumps(audits)
    for pin_secret in ("371946", "482615", "593728"):
        assert pin_secret not in dump


def test_signing_pin_enforced_across_sign_workflow_reject_and_cancel(env_light, monkeypatch):
    from app.models import Document, SignJob

    signer_calls = _patch_all_signers(monkeypatch)
    creator_nopin = env_light.make_user("pin_creator", pin=None)
    signer_nopin = env_light.make_user("pin_signer", pin=None)
    rejecter_ok = env_light.make_user("pin_rejecter", pin="739184")

    c_sig = _assign_cert_db(env_light, signer_nopin.uid, "cert_nopin")
    _assign_cert_db(env_light, rejecter_ok.uid, "cert_rejok")

    # 1. User chưa đặt PIN -> ký file lẻ bị 403 pin_not_set, không gọi signer hay tạo SignJob
    fid1 = _upload_pdf(signer_nopin, "solo.pdf")
    r_no = signer_nopin.post("/api/sign", json=_sign_file_body(fid1, c_sig, pin=None))
    assert r_no.status_code == 403 and r_no.headers.get("X-Pin-Error") == "pin_not_set"
    assert signer_calls == []

    # 2. Luồng ký: user chưa đặt PIN không ký và không từ chối được; creator chưa đặt PIN không hủy được
    fid_wf = _upload_pdf(creator_nopin, "wf.pdf")
    doc_id = creator_nopin.post("/api/documents", json={
        "file_id": fid_wf, "title": "Luồng kiểm tra PIN", "assignee_ids": [signer_nopin.uid, rejecter_ok.uid], "mode": "all",
    }).json()["id"]

    r_wf_nopin = signer_nopin.post(f"/api/documents/{doc_id}/sign", json=_sign_doc_body(c_sig, pin=None))
    assert r_wf_nopin.status_code == 403 and r_wf_nopin.headers.get("X-Pin-Error") == "pin_not_set"
    r_can_nopin = creator_nopin.post(f"/api/documents/{doc_id}/cancel", json={"reason": "Thử hủy"})
    assert r_can_nopin.status_code == 403 and r_can_nopin.headers.get("X-Pin-Error") == "pin_not_set"

    # Đặt PIN cho signer & creator -> kiểm tra thiếu PIN (400), sai PIN (401) không làm thay đổi gì
    env_light.set_pin(signer_nopin, "619382")
    env_light.set_pin(creator_nopin, "847261")

    r_missing = signer_nopin.post(f"/api/documents/{doc_id}/sign", json=_sign_doc_body(c_sig, pin=None))
    assert r_missing.status_code == 400 and r_missing.headers.get("X-Pin-Error") == "pin_required"
    r_wrong = signer_nopin.post(f"/api/documents/{doc_id}/sign", json=_sign_doc_body(c_sig, pin="999998"))
    assert r_wrong.status_code == 401 and r_wrong.headers.get("X-Pin-Error") == "pin_invalid"
    assert r_wrong.headers.get("X-Pin-Remaining") == "4"
    assert signer_calls == []

    sf = env_light.app.state.session_factory
    db = sf()
    try:
        assert db.scalars(select(SignJob)).all() == []
        assert db.get(Document, doc_id).status == "in_progress"
    finally:
        db.close()

    # Reject thiếu/sai PIN cũng bị chặn
    assert rejecter_ok.post(f"/api/documents/{doc_id}/reject", json={"reason": "Thiếu trang"}).status_code == 400
    assert rejecter_ok.post(f"/api/documents/{doc_id}/reject", json={"reason": "Thiếu trang", "pin": "111999"}).status_code == 401
    assert signer_nopin.get(f"/api/documents/{doc_id}").json()["status"] == "in_progress"

    # Reject với đúng PIN của rejecter -> thành công
    assert rejecter_ok.post(f"/api/documents/{doc_id}/reject", json={"reason": "Thiếu trang", "pin": "739184"}).status_code == 200

    # Tạo luồng 2 để test sign OK + cancel với PIN đúng + kiểm tra params_json không chứa PIN
    fid_wf2 = _upload_pdf(creator_nopin, "wf2.pdf")
    doc2_id = creator_nopin.post("/api/documents", json={
        "file_id": fid_wf2, "title": "Luồng hủy với PIN", "assignee_ids": [signer_nopin.uid],
    }).json()["id"]
    r_ok = signer_nopin.post(f"/api/documents/{doc2_id}/sign", json=_sign_doc_body(c_sig, pin="619382"))
    assert r_ok.status_code == 200
    assert len(signer_calls) == 1
    assert creator_nopin.post(f"/api/documents/{doc2_id}/cancel", json={"reason": "Hủy có PIN", "pin": "847261"}).status_code == 200

    db = sf()
    try:
        jobs = db.scalars(select(SignJob)).all()
        assert len(jobs) == 1 and "619382" not in jobs[0].params_json
    finally:
        db.close()


def test_signing_pin_bruteforce_window_lockout_auto_expiry_and_admin_unlock(env_light, monkeypatch):
    _patch_all_signers(monkeypatch)
    u = env_light.make_user("brute_u", pin="395174")
    cid = _assign_cert_db(env_light, u.uid, "cert_brute")
    fid = _upload_pdf(u, "brute.pdf")

    # 4 lần sai -> báo rõ số lần còn lại (4 -> 3 -> 2 -> 1), chưa khóa
    for expected_rem in ("4", "3", "2", "1"):
        r = u.post("/api/sign", json=_sign_file_body(fid, cid, pin="909090"))
        assert r.status_code == 401
        assert r.headers.get("X-Pin-Remaining") == expected_rem

    # Qua cửa sổ 10 phút -> lịch sử sai cũ hết hạn, sai thêm 1 lần quay lại còn 4 lần
    env_light.clock.advance(10 * 60 + 2)
    r_after_win = u.post("/api/sign", json=_sign_file_body(fid, cid, pin="909090"))
    assert r_after_win.status_code == 401
    assert r_after_win.headers.get("X-Pin-Remaining") == "4"

    # Thêm 4 lần sai nữa trong 10 phút (tổng 5 lần) -> khóa 15 phút (423)
    for _ in range(3):
        assert u.post("/api/sign", json=_sign_file_body(fid, cid, pin="909090")).status_code == 401
    r_lock = u.post("/api/sign", json=_sign_file_body(fid, cid, pin="909090"))
    assert r_lock.status_code == 423 and r_lock.headers.get("X-Pin-Error") == "pin_locked"
    assert int(r_lock.headers.get("Retry-After", "0")) > 0

    # Ngay cả PIN đúng cũng bị chặn khi đang khóa
    r_good_while_locked = u.post("/api/sign", json=_sign_file_body(fid, cid, pin="395174"))
    assert r_good_while_locked.status_code == 423 and r_good_while_locked.headers.get("X-Pin-Error") == "pin_locked"
    assert u.get("/api/auth/me").json()["pin_locked"] is True

    # Admin thấy trạng thái khóa và mở khóa sớm bằng /unlock-pin
    users_admin = {x["username"]: x for x in env_light.admin.get("/api/admin/users").json()}
    assert users_admin["brute_u"]["pin_locked"] is True
    assert u.post(f"/api/admin/users/{u.uid}/unlock-pin").status_code == 403  # user thường bị 403
    assert env_light.admin.post(f"/api/admin/users/{u.uid}/unlock-pin").status_code == 200
    assert u.post("/api/sign", json=_sign_file_body(fid, cid, pin="395174")).status_code == 200

    # Khóa lại 5 lần sai và kiểm tra tự mở sau 15 phút
    for _ in range(4):
        assert u.post("/api/sign", json=_sign_file_body(fid, cid, pin="909090")).status_code == 401
    assert u.post("/api/sign", json=_sign_file_body(fid, cid, pin="909090")).status_code == 423
    env_light.clock.advance(15 * 60 + 2)
    assert u.get("/api/auth/me").json()["pin_locked"] is False
    assert u.post("/api/sign", json=_sign_file_body(fid, cid, pin="395174")).status_code == 200

    audits = env_light.admin.get("/api/admin/audit?limit=100").json()
    acts = {a["action"] for a in audits}
    assert {"pin_verify_failed", "pin_locked", "pin_verify_locked", "pin_unlock", "pin_verify_ok"} <= acts
    dump = json.dumps(audits)
    assert "395174" not in dump and "909090" not in dump


def test_parallel_workflow_signing_with_individual_user_pins(env_light, monkeypatch):
    _patch_all_signers(monkeypatch)
    ua = env_light.make_user("par_pin_a", pin="258419")
    ub = env_light.make_user("par_pin_b", pin="371946")
    uc = env_light.make_user("par_pin_c", pin="482615")
    cb = _assign_cert_db(env_light, ub.uid, "cert_ppb")
    cc = _assign_cert_db(env_light, uc.uid, "cert_ppc")

    fid = _upload_pdf(ua, "par.pdf")
    doc_id = ua.post("/api/documents", json={
        "file_id": fid, "title": "Song song PIN riêng", "assignee_ids": [ub.uid, uc.uid], "mode": "all",
    }).json()["id"]

    # Dùng nhầm PIN của người kia phải bị từ chối
    assert ub.post(f"/api/documents/{doc_id}/sign", json=_sign_doc_body(cb, pin="482615")).status_code == 401

    ub_t = env_light.login("par_pin_b", USER_PASS, ub.secret)
    uc_t = env_light.login("par_pin_c", USER_PASS, uc.secret)
    barrier = threading.Barrier(2)

    def _run(client, cid, pin, box):
        barrier.wait(timeout=5)
        return client.post(f"/api/documents/{doc_id}/sign", json=_sign_doc_body(cid, pin=pin, box=box))

    with ThreadPoolExecutor(max_workers=2) as pool:
        fb = pool.submit(_run, ub_t, cb, "371946", [40, 40, 240, 100])
        fc = pool.submit(_run, uc_t, cc, "482615", [300, 40, 500, 100])
        rb, rc = fb.result(timeout=15), fc.result(timeout=15)

    assert rb.status_code == 200 and rc.status_code == 200
    assert ua.get(f"/api/documents/{doc_id}").json()["steps"][0]["status"] == "completed"
