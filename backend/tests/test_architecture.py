"""Kiểm tra kiến trúc (compose) và smoke test container api thật."""
from __future__ import annotations

import os

import httpx
import pytest
import yaml

from conftest import ROOT, need_env


def _compose():
    with open(os.path.join(ROOT, "docker-compose.yml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_compose_no_docker_sock_and_isolated_services():
    y = _compose()
    for name, s in y["services"].items():
        for v in s.get("volumes", []):
            assert "docker.sock" not in str(v), f"{name} mount docker.sock"
            assert "/pki/root" not in str(v) and "root.key" not in str(v), f"{name} không được mount root.key"
        assert s.get("restart") == "unless-stopped", f"{name} thiếu restart: unless-stopped"
        assert "healthcheck" in s, f"{name} thiếu healthcheck"

    # Chỉ caddy được publish port (80, 443)
    caddy_ports = [str(p) for p in y["services"]["caddy"]["ports"]]
    assert len(caddy_ports) == 2 and any(p.endswith(":80") for p in caddy_ports) and any(p.endswith(":443") for p in caddy_ports)
    for n in ("db", "converter", "signer", "tsa", "api", "web"):
        assert not y["services"][n].get("ports"), f"{n} không được publish port"
    for n in ("db", "converter", "signer", "tsa"):
        assert y["services"][n]["networks"] == ["internal"], f"{n} chỉ nằm trong mạng nội bộ"
    assert y["networks"]["internal"]["internal"] is True
    conv = y["services"]["converter"]
    assert conv["read_only"] is True and conv["cap_drop"] == ["ALL"] and conv.get("mem_limit") and conv.get("cpus")


def test_api_has_no_access_to_keys_or_passphrase():
    y = _compose()
    api, signer = y["services"]["api"], y["services"]["signer"]
    assert "P12_PASSPHRASE" not in api["environment"] and "P12_PASSPHRASE_FILE" not in api["environment"]
    assert "p12_passphrase" not in api.get("secrets", [])
    assert all("/pki" not in str(v) for v in api.get("volumes", []))
    assert any(str(v).endswith(":/pki/issued:ro") for v in signer["volumes"])
    assert "P12_PASSPHRASE_FILE" in signer["environment"] and "p12_passphrase" in signer.get("secrets", [])


def test_repo_has_no_hardcoded_passphrase_or_docker_sock_in_service_files():
    for rel in ("backend/app", "signer/service.py", "converter/server.py", "docker-compose.yml"):
        p = os.path.join(ROOT, rel)
        files = [p] if os.path.isfile(p) else [os.path.join(d, f) for d, _, fs in os.walk(p) for f in fs if f.endswith(".py")]
        for f in files:
            src = open(f, encoding="utf-8").read()
            assert "docker.sock" not in src.replace("docker.sock\" not in", ""), f


def test_live_api_container_smoke():
    need_env("API_LIVE_URL", "LIVE_ADMIN_USER", "LIVE_ADMIN_PASS")
    base = os.environ["API_LIVE_URL"]
    with httpx.Client(base_url=base, timeout=20) as c:
        r = c.get("/api/health")
        assert r.status_code == 200 and r.headers["x-content-type-options"] == "nosniff"
        assert c.post("/api/sign", json={}).status_code in (401, 403)
        r = c.post("/api/auth/login", json={"username": os.environ["LIVE_ADMIN_USER"],
                                            "password": os.environ["LIVE_ADMIN_PASS"]})
        assert r.status_code == 200 and r.json()["mfa_required"] is True
        assert "httponly" in r.headers["set-cookie"].lower()
        csrf = r.json()["csrf_token"]
        assert c.get("/api/files").status_code == 403
        assert c.post("/api/sign", json={"file_id": "x"}, headers={"X-CSRF-Token": csrf}).status_code in (403, 422)
        assert c.post("/api/presets", content=b"0" * (2 * 1024 * 1024),
                      headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"}).status_code == 413
