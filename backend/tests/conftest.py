"""Fixture dùng chung cho test M4: app thật + signer/converter thật (qua HTTP), DB SQLite tạm."""
from __future__ import annotations

import os
import sys
import time

import pyotp
import pytest
from fastapi.testclient import TestClient

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
for p in (os.path.join(ROOT, "backend"), os.path.join(ROOT, "signer"), HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

TEST_DATA = os.path.join(ROOT, "test-data")
ADMIN_USER, ADMIN_PASS = "admin", "Adm1n-Pass-Strong"
USER_PASS = "Str0ng-Passw0rd-Ok"
TEST_SIGN_PIN = "258419"


def need_env(*names):
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        pytest.fail("Thiếu biến môi trường cho test M4: " + ", ".join(missing), pytrace=False)


class Clock:
    """Đồng hồ giả cho TOTP: mỗi lần xác thực tiến thêm 1 bước để không trùng mã (chống replay)."""

    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t

    def advance(self, secs=31):
        self.t += secs


class Api:
    """TestClient + CSRF header tự động. Mỗi Api có cookie jar riêng (= một trình duyệt)."""

    def __init__(self, app):
        self.app = app
        self.c = TestClient(app, base_url="http://testserver")
        self.csrf = None
        self.secret = None
        self.recovery = []

    def _h(self, kw):
        h = dict(kw.pop("headers", {}) or {})
        if self.csrf:
            h["X-CSRF-Token"] = self.csrf
        return h

    def get(self, url, **kw):
        return self.c.get(url, headers=self._h(kw), **kw)

    def post(self, url, **kw):
        return self.c.post(url, headers=self._h(kw), **kw)

    def put(self, url, **kw):
        return self.c.put(url, headers=self._h(kw), **kw)

    def delete(self, url, **kw):
        return self.c.delete(url, headers=self._h(kw), **kw)

    def request(self, method, url, **kw):
        return self.c.request(method, url, headers=self._h(kw), **kw)


class Env:
    def __init__(self, app, clock):
        self.app, self.clock = app, clock
        self.admin = None
        self.certs = {}

    def login_password(self, username, password):
        api = Api(self.app)
        r = api.post("/api/auth/login", json={"username": username, "password": password})
        if r.status_code == 200:
            api.csrf = r.json()["csrf_token"]
        return api, r

    def enroll(self, username, password):
        """Đăng nhập lần đầu: đặt TOTP + xác nhận (nhận mã khôi phục)."""
        api, r = self.login_password(username, password)
        assert r.status_code == 200, r.text
        assert r.json()["totp_enrolled"] is False
        api.secret = api.post("/api/auth/totp/setup").json()["secret"]
        self.clock.advance()
        r = api.post("/api/auth/totp/confirm", json={"code": pyotp.TOTP(api.secret).at(self.clock.t)})
        assert r.status_code == 200, r.text
        api.csrf, api.recovery = r.json()["csrf_token"], r.json()["recovery_codes"]
        return api

    def login(self, username, password, secret):
        api, r = self.login_password(username, password)
        assert r.status_code == 200, r.text
        self.clock.advance()
        r = api.post("/api/auth/totp/verify", json={"code": pyotp.TOTP(secret).at(self.clock.t)})
        assert r.status_code == 200, r.text
        api.csrf, api.secret = r.json()["csrf_token"], secret
        return api

    def set_pin(self, api, pin: str = TEST_SIGN_PIN, *, use_recovery: bool = False):
        payload = {"new_pin": pin}
        if use_recovery:
            payload["recovery_code"] = api.recovery.pop()
        else:
            self.clock.advance()
            payload["totp_code"] = pyotp.TOTP(api.secret).at(self.clock.t)
        r = api.post("/api/auth/signing-pin", json=payload)
        assert r.status_code == 200, r.text
        api.pin = pin
        return r.json()

    def mark_onboarded(
        self,
        api_or_username,
        *,
        job_title: str = "Chuyên viên",
        department: str = "Phòng Kỹ thuật",
        manager_name: str = "Quản lý Mẫu",
        set_email: bool = False,
        pin: str | None = None,
    ):
        from app.models import User, utcnow
        from app.terms import load_terms

        uname = api_or_username if isinstance(api_or_username, str) else getattr(api_or_username, "_username", None)
        sf = self.app.state.session_factory
        db = sf()
        try:
            u = db.scalar(select_user(uname)) if False else None
            u = db.query(User).filter(User.username == uname).one()
            if not u.job_title:
                u.job_title = job_title
            if not u.department:
                u.department = department
            if not u.manager_name:
                u.manager_name = manager_name
            if set_email and not u.email:
                u.email = f"{uname}@example.local"
            u.accepted_terms_version = load_terms(self.app.state.settings)["version"]
            u.onboarding_step = 4
            if u.onboarding_completed_at is None:
                u.onboarding_completed_at = utcnow()
            db.commit()
        finally:
            db.close()
        if not isinstance(api_or_username, str) and pin is not None:
            self.set_pin(api_or_username, pin)

    def make_user(
        self,
        username,
        role="user",
        cert_keys=(),
        pin: str | None = TEST_SIGN_PIN,
        onboarded: bool = True,
    ):
        r = self.admin.post("/api/admin/users", json={"username": username, "password": USER_PASS,
                                                      "display_name": username.title(), "role": role})
        assert r.status_code == 201, r.text
        uid = r.json()["id"]
        if cert_keys:
            ids = [self.certs[k] for k in cert_keys]
            assert self.admin.put(f"/api/admin/users/{uid}/certificates", json={"cert_ids": ids}).status_code == 200
        api = self.enroll(username, USER_PASS)
        api.uid = uid
        api._username = username
        if onboarded:
            self.mark_onboarded(api, pin=pin)
        else:
            api.pin = None
        return api

    def register_certs(self, *key_refs):
        for k in key_refs:
            r = self.admin.post("/api/admin/certificates", json={"key_ref": k})
            assert r.status_code == 201, r.text
            self.certs[k] = r.json()["id"]


def build_env(tmp_path, with_services=True, **over):
    if with_services:
        need_env("SIGNER_URL", "SIGNER_TOKEN", "CONVERTER_URL", "TOTP_ENC_KEY")
    from cryptography.fernet import Fernet
    base = dict(
        data_dir=str(tmp_path), signer_url=os.environ.get("SIGNER_URL", "http://signer:9000"),
        converter_url=os.environ.get("CONVERTER_URL", "http://converter:8001"),
        signer_token=os.environ.get("SIGNER_TOKEN", "x"),
        totp_enc_key=os.environ.get("TOTP_ENC_KEY") or Fernet.generate_key().decode(),
        cookie_secure=False, cleanup_interval_seconds=0,
        admin_username=ADMIN_USER, admin_password=ADMIN_PASS,
        login_rate=(1000, 60), sign_rate=(1000, 60), upload_rate=(1000, 60), verify_rate=(1000, 60),
    )
    base.update(over)
    app = create_app(Settings(**base))
    clock = Clock()
    app.state.clock = clock
    env = Env(app, clock)
    env.admin = env.enroll(ADMIN_USER, ADMIN_PASS)
    env.admin._username = ADMIN_USER
    env.mark_onboarded(env.admin, job_title="Quản trị hệ thống", department="CNTT", set_email=False, pin=TEST_SIGN_PIN)
    return env


@pytest.fixture
def env(tmp_path):
    e = build_env(tmp_path)
    e.register_certs("carol", "dave")
    return e


@pytest.fixture
def env_light(tmp_path):
    """Không cần signer/converter (test thuần xác thực)."""
    return build_env(tmp_path, with_services=False)


def read(name):
    with open(os.path.join(TEST_DATA, name), "rb") as f:
        return f.read()
