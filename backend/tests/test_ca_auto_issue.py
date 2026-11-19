"""Kiểm thử cấp & thu hồi chứng thư tự động qua service `ca` nội bộ (Phương án 2)."""
from __future__ import annotations

import importlib.util
import io
import os
import threading
from http.server import ThreadingHTTPServer

import httpx
import pytest
from pypdf import PdfWriter

from conftest import ROOT, USER_PASS, build_env


def _load_ca_module():
    path = os.path.join(ROOT, "ca", "service.py")
    spec = importlib.util.spec_from_file_location("esign_ca_service", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_pdf() -> bytes:
    w = PdfWriter()
    w.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_ca_service_validators_and_auth():
    ca = _load_ca_module()

    assert ca.validate_key_ref("nguyen_van-a1") == "nguyen_van-a1"
    for bad_ref in ("", "a", "../evil", "a b", "ten.co.dau", "á"):
        with pytest.raises(ca.CAError):
            ca.validate_key_ref(bad_ref)

    assert ca.validate_cn("Nguyễn Văn B") == "Nguyễn Văn B"
    for bad_cn in ("", "   ", "Fake/O=EvilCA", "A\\B", "A+B", "A\nB"):
        with pytest.raises(ca.CAError):
            ca.validate_cn(bad_cn)

    assert ca.validate_days(365) == 365
    for bad_days in (0, -1, 2000, "abc"):
        with pytest.raises(ca.CAError):
            ca.validate_days(bad_days)

    old_tok = ca.CA_TOKEN
    ca.CA_TOKEN = "secret-ca-tok"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), ca.Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        with httpx.Client(base_url=base, timeout=5) as c:
            assert c.get("/health").status_code == 200
            assert c.post("/issue", json={"key_ref": "u1", "common_name": "U1"}).status_code == 401
            assert c.post(
                "/issue",
                json={"key_ref": "u1", "common_name": "U1"},
                headers={"X-CA-Token": "wrong"},
            ).status_code == 401
            assert c.post(
                "/issue",
                json={"key_ref": "u1", "common_name": "Bad/CN"},
                headers={"X-CA-Token": "secret-ca-tok"},
            ).status_code == 400
    finally:
        srv.shutdown()
        srv.server_close()
        ca.CA_TOKEN = old_tok


def test_admin_auto_issue_on_user_create_and_existing_user_and_revoke(tmp_path, monkeypatch):
    # 1. Khi chưa cấu hình CA -> ca/status enabled=False; tạo user bật issue_cert trả 503 và không lưu user rở
    env_no_ca = build_env(tmp_path / "no_ca", with_services=False, ca_url="")
    assert env_no_ca.admin.get("/api/admin/ca/status").json() == {"enabled": False, "healthy": False}
    r_fail = env_no_ca.admin.post("/api/admin/users", json={
        "username": "staff1", "password": USER_PASS, "display_name": "Nhân viên 1", "issue_cert": True,
    })
    assert r_fail.status_code == 503
    assert all(u["username"] != "staff1" for u in env_no_ca.admin.get("/api/admin/users").json())

    # 2. Khi cấu hình CA (giả lập service ca HTTP client)
    import app.routes_admin as ra

    issued_calls = []
    revoked_calls = []

    def _fake_issue(st, key_ref: str, common_name: str, days: int = 365):
        issued_calls.append({"key_ref": key_ref, "cn": common_name, "days": days})
        return {
            "key_ref": key_ref,
            "subject": common_name,
            "serial": f"aa{len(issued_calls):04d}",
            "not_after": "2028-01-01T00:00:00+00:00",
        }

    def _fake_revoke(st, key_ref: str):
        revoked_calls.append(key_ref)
        return {"ok": True, "key_ref": key_ref, "revoked": True}

    monkeypatch.setattr(ra, "ca_health", lambda st: True)
    monkeypatch.setattr(ra, "ca_issue", _fake_issue)
    monkeypatch.setattr(ra, "ca_revoke", _fake_revoke)

    env = build_env(tmp_path / "with_ca", with_services=False, ca_url="http://ca:9001", ca_token="tok")
    assert env.admin.get("/api/admin/ca/status").json() == {"enabled": True, "healthy": True}

    # Tạo user kèm issue_cert=True -> tự cấp + gán luôn
    r_u = env.admin.post("/api/admin/users", json={
        "username": "nguyenvana",
        "password": USER_PASS,
        "display_name": "Nguyễn Văn A",
        "email": "nva@example.local",
        "issue_cert": True,
        "cert_days": 730,
    })
    assert r_u.status_code == 201, r_u.text
    du = r_u.json()
    assert du["cert_refs"] == ["nguyenvana"]
    assert du["certificate"]["key_ref"] == "nguyenvana"
    assert du["certificate"]["subject"] == "Nguyễn Văn A"
    assert issued_calls[-1] == {"key_ref": "nguyenvana", "cn": "Nguyễn Văn A", "days": 730}

    # User thường không được gọi các API quản trị CA
    u_api = env.enroll("nguyenvana", USER_PASS)
    assert u_api.get("/api/admin/ca/status").status_code == 403
    assert u_api.post(f"/api/admin/users/{du['id']}/issue-certificate", json={}).status_code == 403
    assert u_api.post(f"/api/admin/certificates/{du['certificate']['id']}/revoke").status_code == 403

    # User nhận cert dùng được ngay ở API /certificates và hiện has_cert=True trong /users/assignable
    my_certs = u_api.get("/api/certificates").json()
    assert [c["id"] for c in my_certs] == [du["certificate"]["id"]]
    assignable = {x["username"]: x for x in u_api.get("/api/users/assignable").json()}
    assert assignable["nguyenvana"]["has_cert"] is True

    # Cấp thêm cert mới cho user đã tồn tại -> tự sinh key_ref không trùng (-2)
    r_add = env.admin.post(f"/api/admin/users/{du['id']}/issue-certificate", json={"days": 365})
    assert r_add.status_code == 201, r_add.text
    assert r_add.json()["certificate"]["key_ref"] == "nguyenvana-2"
    users_map = {x["username"]: x for x in env.admin.get("/api/admin/users").json()}
    assert users_map["nguyenvana"]["cert_refs"] == ["nguyenvana", "nguyenvana-2"]

    # Tên CN có ký tự inject OpenSSL phải bị từ chối 400
    assert env.admin.post(
        f"/api/admin/users/{du['id']}/issue-certificate",
        json={"common_name": "Evil/O=Fake"},
    ).status_code == 400

    # Thu hồi chứng thư đầu tiên
    cid1 = du["certificate"]["id"]
    r_rev = env.admin.post(f"/api/admin/certificates/{cid1}/revoke")
    assert r_rev.status_code == 200 and r_rev.json()["active"] is False
    assert revoked_calls == ["nguyenvana"]

    # Audit log ghi nhận đầy đủ cert_issue, cert_assign, cert_revoke
    acts = [a["action"] for a in env.admin.get("/api/admin/audit?limit=50").json()]
    assert "cert_issue" in acts and "cert_assign" in acts and "cert_revoke" in acts


@pytest.mark.skipif(
    not (os.environ.get("CA_URL") and os.environ.get("SIGNER_URL")),
    reason="Cần service `ca` và `signer` thật trong Docker Compose",
)
def test_ca_and_signer_live_e2e_issue_sign_revoke(tmp_path):
    env = build_env(
        tmp_path,
        with_services=True,
        ca_url=os.environ["CA_URL"],
        ca_token=os.environ["CA_TOKEN"],
    )
    assert env.admin.get("/api/admin/ca/status").json() == {"enabled": True, "healthy": True}

    # Tạo user mới và tự động cấp chứng thư thật qua container `ca`
    r_create = env.admin.post("/api/admin/users", json={
        "username": "ca_e2e_u1",
        "password": USER_PASS,
        "display_name": "Hoàng Minh Tuấn",
        "issue_cert": True,
        "cert_days": 365,
    })
    assert r_create.status_code == 201, r_create.text
    cert = r_create.json()["certificate"]

    u = env.enroll("ca_e2e_u1", USER_PASS)
    fid = u.post("/api/files", files={"file": ("auto.pdf", _make_pdf(), "application/pdf")}).json()["id"]
    rs = u.post("/api/sign", json={
        "file_id": fid, "cert_id": cert["id"], "mode": "text", "page": 1,
        "box": [40, 40, 260, 110], "reason": "Ký bằng cert tự động cấp",
    })
    assert rs.status_code == 200, rs.text
    out_id = rs.json()["output_file"]["id"]
    ver1 = u.post(f"/api/files/{out_id}/verify").json()
    assert ver1["all_ok"] is True and "Hoàng Minh Tuấn" in ver1["signatures"][0]["signer"]

    # Thu hồi cert qua container `ca` -> verify thất bại và ký mới bị chặn
    assert env.admin.post(f"/api/admin/certificates/{cert['id']}/revoke").status_code == 200
    ver2 = u.post(f"/api/files/{out_id}/verify").json()
    assert ver2["all_ok"] is False
    rs_blocked = u.post("/api/sign", json={
        "file_id": fid, "cert_id": cert["id"], "mode": "text", "page": 1, "box": [40, 40, 260, 110],
    })
    assert rs_blocked.status_code in (400, 403)
