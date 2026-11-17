"""Xác thực, 2FA, khóa tài khoản, rate limit, CSRF, phân quyền admin (không cần signer/converter)."""
from __future__ import annotations

import pyotp
from sqlalchemy import text

from conftest import ADMIN_PASS, ADMIN_USER, USER_PASS, build_env


def test_password_hashed_argon2id_and_totp_secret_encrypted(env_light):
    u = env_light.make_user("alice")
    with env_light.app.state.engine.connect() as con:
        row = con.execute(text("SELECT password_hash, totp_secret_enc FROM users WHERE username='alice'")).one()
    assert row[0].startswith("$argon2id$")
    assert USER_PASS not in row[0]
    assert u.secret not in row[1] and row[1].startswith("gAAAA")  # token Fernet, không phải secret thô


def test_cookie_httponly_samesite_and_no_token_in_body(env_light):
    api, r = env_light.login_password(ADMIN_USER, ADMIN_PASS)
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "samesite=strict" in sc
    assert "esign_sid" not in r.text  # token phiên không nằm trong JSON (không có gì để cất vào localStorage)


def test_sign_and_files_denied_without_2fa(env_light):
    """Đã nhập đúng mật khẩu nhưng chưa qua 2FA -> mọi API nghiệp vụ đều bị chặn."""
    env_light.make_user("alice")
    api, r = env_light.login_password("alice", USER_PASS)
    assert r.status_code == 200 and r.json()["mfa_required"] is True
    assert api.get("/api/auth/me").json()["mfa_verified"] is False
    body = {"file_id": "00000000-0000-4000-8000-000000000000", "cert_id": 1, "box": [10, 10, 200, 80]}
    assert api.post("/api/sign", json=body).status_code == 403
    assert api.get("/api/files").status_code == 403
    assert api.post("/api/files", files={"file": ("a.pdf", b"%PDF-1.4")}).status_code == 403
    assert api.get("/api/certificates").status_code == 403
    # Không có phiên -> 401
    anon = type(api)(env_light.app)
    assert anon.post("/api/sign", json=body).status_code == 401


def test_wrong_totp_rejected_then_ok(env_light):
    env_light.make_user("alice")
    api, _ = env_light.login_password("alice", USER_PASS)
    assert api.post("/api/auth/totp/verify", json={"code": "000000"}).status_code == 401
    assert api.get("/api/auth/me").json()["mfa_verified"] is False


def test_totp_replay_rejected(env_light):
    u = env_light.make_user("alice")
    env_light.clock.advance()
    code = pyotp.TOTP(u.secret).at(env_light.clock.t)
    api, _ = env_light.login_password("alice", USER_PASS)
    assert api.post("/api/auth/totp/verify", json={"code": code}).status_code == 200
    api2, _ = env_light.login_password("alice", USER_PASS)  # dùng lại đúng mã đó
    assert api2.post("/api/auth/totp/verify", json={"code": code}).status_code == 401


def test_recovery_code_single_use(env_light):
    u = env_light.make_user("alice")
    assert len(u.recovery) == 10
    rc = u.recovery[0]
    api, _ = env_light.login_password("alice", USER_PASS)
    assert api.post("/api/auth/totp/verify", json={"recovery_code": rc}).status_code == 200
    assert api.get("/api/files").status_code == 200
    api2, _ = env_light.login_password("alice", USER_PASS)
    assert api2.post("/api/auth/totp/verify", json={"recovery_code": rc}).status_code == 401  # đã dùng
    assert api2.get("/api/files").status_code == 403


def test_lockout_after_repeated_failures_and_admin_unlock(env_light):
    u = env_light.make_user("alice")
    for _ in range(5):
        _, r = env_light.login_password("alice", "sai-mat-khau-X1")
        assert r.status_code == 401
    _, r = env_light.login_password("alice", USER_PASS)  # đúng mật khẩu nhưng đang bị khóa
    assert r.status_code == 423 and "Retry-After" in r.headers
    assert env_light.admin.post(f"/api/admin/users/{u.uid}/unlock").status_code == 200
    _, r = env_light.login_password("alice", USER_PASS)
    assert r.status_code == 200


def test_lockout_also_applies_to_totp_guessing(env_light):
    env_light.make_user("alice")
    api, _ = env_light.login_password("alice", USER_PASS)
    for _ in range(5):
        assert api.post("/api/auth/totp/verify", json={"code": "123456"}).status_code in (401, 423)
    assert api.post("/api/auth/totp/verify", json={"code": "123456"}).status_code == 423


def test_login_rate_limit(tmp_path):
    # build_env đã dùng 1 lượt login của admin từ cùng IP testclient
    e = build_env(tmp_path, with_services=False, login_rate=(4, 60), max_failed_logins=1000)
    codes = [e.login_password("nobody", "x")[1].status_code for _ in range(5)]
    assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]


def test_csrf_required_and_wrong_token_rejected(env_light):
    u = env_light.make_user("bob")
    good = u.csrf
    u.csrf = None
    assert u.post("/api/presets", json={"name": "x", "config": {}}).status_code == 403
    u.csrf = "sai-token"
    assert u.post("/api/presets", json={"name": "x", "config": {}}).status_code == 403
    u.csrf = good
    assert u.post("/api/presets", json={"name": "x", "config": {"mode": "text"}}).status_code == 201


def test_foreign_origin_rejected(env_light):
    u = env_light.make_user("alice")
    r = u.post("/api/presets", json={"name": "x", "config": {}}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_only_admin_manages_users(env_light):
    u = env_light.make_user("alice")
    body = {"username": "mallory", "password": USER_PASS}
    assert u.post("/api/admin/users", json=body).status_code == 403
    assert u.get("/api/admin/users").status_code == 403
    assert u.get("/api/admin/audit").status_code == 403
    assert u.post(f"/api/admin/users/{u.uid}/disable").status_code == 403
    assert env_light.admin.post("/api/admin/users", json=body).status_code == 201


def test_weak_password_rejected(env_light):
    r = env_light.admin.post("/api/admin/users", json={"username": "weak", "password": "short"})
    assert r.status_code == 400


def test_disable_user_blocks_login_and_kills_sessions(env_light):
    u = env_light.make_user("alice")
    assert u.get("/api/files").status_code == 200
    assert env_light.admin.post(f"/api/admin/users/{u.uid}/disable").status_code == 200
    assert u.get("/api/files").status_code == 401  # phiên cũ bị hủy
    _, r = env_light.login_password("alice", USER_PASS)
    assert r.status_code == 401
    assert env_light.admin.post(f"/api/admin/users/{u.uid}/enable").status_code == 200
    _, r = env_light.login_password("alice", USER_PASS)
    assert r.status_code == 200


def test_logout_invalidates_session(env_light):
    u = env_light.make_user("alice")
    assert u.post("/api/auth/logout").status_code == 200
    assert u.get("/api/files").status_code == 401


def test_session_rotates_after_mfa(env_light):
    api, r = env_light.login_password(ADMIN_USER, ADMIN_PASS)
    before = api.c.cookies.get("esign_sid")
    env_light.clock.advance()
    api.post("/api/auth/totp/verify", json={"code": pyotp.TOTP(env_light.admin.secret).at(env_light.clock.t)})
    assert api.c.cookies.get("esign_sid") != before


def test_sign_rate_limit(tmp_path):
    e = build_env(tmp_path, with_services=False, sign_rate=(2, 60))
    u = e.make_user("alice")
    body = {"file_id": "00000000-0000-4000-8000-000000000000", "cert_id": 1, "box": [10, 10, 200, 80]}
    codes = [u.post("/api/sign", json=body).status_code for _ in range(4)]
    assert codes[:2] == [404, 404] and codes[2:] == [429, 429]
