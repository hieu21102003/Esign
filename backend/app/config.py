"""Cấu hình backend. Mọi bí mật lấy từ biến môi trường hoặc file secret (<TÊN>_FILE), không hard-code."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import quote_plus


def read_secret(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    if v:
        return v
    f = os.environ.get(name + "_FILE")
    if f:
        with open(f, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    return default


def read_secret_file_first(name: str, default: str = "", *, optional: bool = False) -> str:
    """Ưu tiên đọc bí mật từ biến <TÊN>_FILE; chỉ fallback sang biến <TÊN> khi file trống hoặc thiếu (nếu optional=True)."""
    f = (os.environ.get(name + "_FILE") or "").strip()
    if f:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                val = fh.read().rstrip("\r\n")
            if val:
                return val
        except FileNotFoundError:
            if not optional:
                raise
    v = (os.environ.get(name) or "").rstrip("\r\n")
    return v if v else default


def _rate(name: str, default: str) -> tuple:
    n, w = os.environ.get(name, default).split("/")
    return int(n), int(w)


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    data_dir: str = "/data"
    database_url: str = ""
    converter_url: str = "http://converter:8001"
    signer_url: str = "http://signer:9000"
    signer_token: str = ""
    totp_enc_key: str = ""
    max_upload_bytes: int = 25 * 1024 * 1024
    max_image_bytes: int = 2 * 1024 * 1024
    max_pages: int = 500
    retention_days: int = 7
    workflow_retention_days: int = 0  # 0 = giữ vô thời hạn file luồng ký; >0 = xóa N ngày sau khi kết thúc
    session_ttl_minutes: int = 480
    cookie_secure: bool = True
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    pin_max_failed_attempts: int = 5
    pin_fail_window_minutes: int = 10
    pin_lockout_minutes: int = 15
    min_password_length: int = 10
    login_rate: tuple = (10, 60)      # (số lần, cửa sổ giây) theo IP và theo username
    sign_rate: tuple = (10, 60)       # theo user
    upload_rate: tuple = (20, 60)     # theo user
    verify_rate: tuple = (30, 60)     # theo user/IP
    smtp_test_rate: tuple = (5, 60)   # giới hạn gửi email thử theo admin user
    trust_proxy: bool = False         # chỉ bật khi sau reverse proxy tin cậy (Caddy)
    trusted_proxies: list[str] = field(default_factory=list)
    cleanup_interval_seconds: int = 3600
    admin_username: str = ""
    admin_password: str = ""
    totp_issuer: str = "esign"
    signer_timeout: int = 120
    converter_timeout: int = 70
    ca_url: str = ""
    ca_token: str = ""
    ca_timeout: int = 30
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""
    smtp_from: str = ""
    smtp_tls: bool = True
    app_base_url: str = ""
    terms_file: str = ""
    terms_version: str = ""
    onboarding_otp_ttl_minutes: int = 5
    onboarding_otp_reuse_minutes: int = 15
    onboarding_otp_max_attempts: int = 5

    @property
    def upload_dir(self) -> str:
        return os.path.join(self.data_dir, "files")

    @property
    def db_url(self) -> str:
        return self.database_url or "sqlite:///" + os.path.join(self.data_dir, "esign.db").replace("\\", "/")

    @classmethod
    def from_env(cls) -> "Settings":
        db_url = os.environ.get("DATABASE_URL", "")
        if not db_url and os.environ.get("DB_HOST"):
            db_pw = quote_plus(read_secret("DB_PASSWORD"))
            db_user = quote_plus(os.environ.get("DB_USER", "esign"))
            db_host = os.environ["DB_HOST"]
            db_port = os.environ.get("DB_PORT", "5432")
            db_name = os.environ.get("DB_NAME", "esign")
            db_url = f"postgresql+psycopg://{db_user}:{db_pw}@{db_host}:{db_port}/{db_name}"
        proxies = [x.strip() for x in os.environ.get("TRUSTED_PROXIES", "").split(",") if x.strip()]
        return cls(
            data_dir=os.environ.get("DATA_DIR", "/data"),
            database_url=db_url,
            converter_url=os.environ.get("CONVERTER_URL", "http://converter:8001"),
            signer_url=os.environ.get("SIGNER_URL", "http://signer:9000"),
            signer_token=read_secret("SIGNER_TOKEN"),
            totp_enc_key=read_secret("TOTP_ENC_KEY"),
            max_upload_bytes=int(os.environ.get("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024))),
            retention_days=int(os.environ.get("RETENTION_DAYS", "7")),
            workflow_retention_days=int(os.environ.get("WORKFLOW_RETENTION_DAYS", "0")),
            session_ttl_minutes=int(os.environ.get("SESSION_TTL_MINUTES", "480")),
            cookie_secure=_bool("COOKIE_SECURE", True),
            max_failed_logins=int(os.environ.get("MAX_FAILED_LOGINS", "5")),
            lockout_minutes=int(os.environ.get("LOCKOUT_MINUTES", "15")),
            login_rate=_rate("RATE_LOGIN", "10/60"),
            sign_rate=_rate("RATE_SIGN", "10/60"),
            upload_rate=_rate("RATE_UPLOAD", "20/60"),
            verify_rate=_rate("RATE_VERIFY", "30/60"),
            smtp_test_rate=_rate("RATE_SMTP_TEST", "5/60"),
            trust_proxy=_bool("TRUST_PROXY", False),
            trusted_proxies=proxies,
            cleanup_interval_seconds=int(os.environ.get("CLEANUP_INTERVAL_SECONDS", "3600")),
            admin_username=os.environ.get("ADMIN_USERNAME", ""),
            admin_password=read_secret("ADMIN_PASSWORD"),
            totp_issuer=os.environ.get("TOTP_ISSUER", "esign"),
            ca_url=os.environ.get("CA_URL", "").strip(),
            ca_token=read_secret("CA_TOKEN"),
            smtp_host=os.environ.get("SMTP_HOST", "").strip(),
            smtp_port=int(os.environ.get("SMTP_PORT", "587")),
            smtp_user=os.environ.get("SMTP_USER", "").strip(),
            smtp_pass=read_secret_file_first("SMTP_PASS", optional=True),
            smtp_from=os.environ.get("SMTP_FROM", "").strip(),
            smtp_tls=_bool("SMTP_TLS", True),
            app_base_url=os.environ.get("APP_BASE_URL", "").strip(),
            terms_file=os.environ.get("TERMS_FILE", "").strip(),
            terms_version=os.environ.get("TERMS_VERSION", "").strip(),
        )
