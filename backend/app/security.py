"""Băm mật khẩu, TOTP, mã khôi phục, mã hóa secret, rate limit."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Optional

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet

_ph = PasswordHasher()  # argon2id mặc định
DUMMY_HASH = _ph.hash("khong-phai-mat-khau-that")  # để đồng đều thời gian khi user không tồn tại


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(pw_hash: str, password: str) -> bool:
    try:
        return _ph.verify(pw_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def check_password_policy(password: str, min_len: int) -> Optional[str]:
    if len(password) < min_len:
        return f"Mật khẩu tối thiểu {min_len} ký tự"
    if len(password) > 256:
        return "Mật khẩu quá dài"
    if password.lower() == password or password.upper() == password or not re.search(r"\d", password):
        return "Mật khẩu cần có chữ hoa, chữ thường và chữ số"
    return None


class SecretBox:
    """Mã hóa secret TOTP khi lưu (Fernet); khóa lấy từ env/secret, không nằm trong DB."""

    def __init__(self, key: str):
        if not key:
            raise RuntimeError("Thiếu TOTP_ENC_KEY (Fernet key, tạo bằng Fernet.generate_key())")
        self._f = Fernet(key.encode() if isinstance(key, str) else key)
        self._pepper = hashlib.sha256(b"esign-recovery|" + key.encode()).digest()

    def encrypt(self, s: str) -> str:
        return self._f.encrypt(s.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self._f.decrypt(token.encode()).decode()

    def recovery_hash(self, code: str) -> str:
        return hmac.new(self._pepper, normalize_recovery(code).encode(), hashlib.sha256).hexdigest()


def normalize_recovery(code: str) -> str:
    return re.sub(r"[\s-]", "", code).lower()


def new_recovery_codes(n: int = 10) -> list:
    out = []
    for _ in range(n):
        h = secrets.token_hex(5)  # 40 bit
        out.append(f"{h[:5]}-{h[5:]}")
    return out


def totp_new_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, username: str, issuer: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=issuer)


def totp_check(secret: str, code: str, now: float, last_step: int) -> Optional[int]:
    """Trả về time-step hợp lệ (±1 bước) hoặc None. Từ chối step <= last_step (chống replay)."""
    code = re.sub(r"\s", "", code or "")
    if not re.fullmatch(r"\d{6}", code):
        return None
    t = pyotp.TOTP(secret)
    cur = int(now // 30)
    for step in (cur, cur - 1, cur + 1):
        if step <= last_step:
            continue
        if hmac.compare_digest(t.at(step * 30), code):
            return step
    return None


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_id(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class RateLimiter:
    """Cửa sổ trượt trong bộ nhớ (chạy 1 worker; nếu scale nhiều worker cần Redis)."""

    def __init__(self):
        self._d = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window: int) -> Optional[int]:
        """Trả về số giây cần chờ nếu vượt giới hạn, ngược lại None (và ghi nhận 1 lần gọi)."""
        now = time.monotonic()
        with self._lock:
            dq = self._d[key]
            while dq and dq[0] <= now - window:
                dq.popleft()
            if len(dq) >= limit:
                return max(1, int(dq[0] + window - now) + 1)
            dq.append(now)
            if len(self._d) > 50000:  # dọn khóa rỗng
                for k in [k for k, v in self._d.items() if not v]:
                    del self._d[k]
            return None
