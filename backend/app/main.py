"""FastAPI app: middleware bảo mật, giới hạn kích thước body, kiểm tra Origin."""
from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from . import routes_admin, routes_auth, routes_files
from .cleanup import CleanupThread
from .config import Settings
from .models import User, init_db, make_engine, make_session_factory
from .security import RateLimiter, SecretBox, check_password_policy, hash_password

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
UPLOAD_PATHS = ("/api/files", "/api/verify", "/api/signature-images")
SMALL_BODY = 1024 * 1024


def bootstrap_admin(settings: Settings, session_factory) -> None:
    """Tạo admin đầu tiên từ ADMIN_USERNAME/ADMIN_PASSWORD (env/secret) nếu DB chưa có user nào."""
    if not (settings.admin_username and settings.admin_password):
        return
    db = session_factory()
    try:
        if db.scalar(select(User.id).limit(1)) is not None:
            return
        err = check_password_policy(settings.admin_password, settings.min_password_length)
        if err:
            raise RuntimeError("ADMIN_PASSWORD không đạt chính sách: " + err)
        db.add(User(username=settings.admin_username.strip().lower(), display_name="Administrator",
                    password_hash=hash_password(settings.admin_password), role="admin"))
        db.commit()
    finally:
        db.close()


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    st = settings or Settings.from_env()
    os.makedirs(st.data_dir, mode=0o700, exist_ok=True)
    os.makedirs(st.upload_dir, mode=0o700, exist_ok=True)
    engine = make_engine(st.db_url)
    init_db(engine)
    sf = make_session_factory(engine)
    bootstrap_admin(st, sf)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        thread = None
        if st.cleanup_interval_seconds > 0:
            thread = CleanupThread(st, sf)
            thread.start()
        yield
        if thread:
            thread.stop()

    app = FastAPI(title="esign", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.settings = st
    app.state.session_factory = sf
    app.state.engine = engine
    app.state.limiter = RateLimiter()
    app.state.box = SecretBox(st.totp_enc_key)
    app.state.clock = time.time  # tiêm được trong test (TOTP)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if request.method in UNSAFE:
            # Chặn CSRF xuyên origin: Origin (nếu có) phải trùng host của request
            origin = request.headers.get("origin")
            if origin:
                host = request.headers.get("x-forwarded-host") if st.trust_proxy else None
                host = host or request.headers.get("host", "")
                if urlparse(origin).netloc != host:
                    return JSONResponse({"detail": "Origin không hợp lệ"}, status_code=403)
            # Giới hạn kích thước body ở tầng ứng dụng (reverse proxy cũng giới hạn 25MB)
            cl = request.headers.get("content-length")
            if cl is None:
                if request.headers.get("transfer-encoding"):
                    return JSONResponse({"detail": "Cần Content-Length"}, status_code=411)
            else:
                try:
                    n = int(cl)
                except ValueError:
                    return JSONResponse({"detail": "Content-Length không hợp lệ"}, status_code=400)
                limit = st.max_upload_bytes + SMALL_BODY if path in UPLOAD_PATHS else SMALL_BODY
                if n > limit:
                    return JSONResponse({"detail": "Body quá lớn"}, status_code=413)
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    app.include_router(routes_auth.router)
    app.include_router(routes_admin.router)
    app.include_router(routes_files.router)
    return app
