"""Đọc file điều khoản sử dụng chữ ký số nội bộ và tính SHA-256 + version."""
from __future__ import annotations

import hashlib
import os


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    text = raw.replace("\r\n", "\n")
    meta: dict[str, str] = {}
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            header = text[4:end]
            body = text[end + 5:].lstrip("\n")
            for line in header.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip().lower()] = v.strip()
    return meta, body


def load_terms(settings=None) -> dict[str, str]:
    default_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "terms_agreement.md")
    path = (getattr(settings, "terms_file", "") or "").strip() or default_path
    with open(path, "r", encoding="utf-8") as fh:
        raw = fh.read()
    meta, content = _parse_frontmatter(raw)
    override_ver = (getattr(settings, "terms_version", "") or "").strip()
    version = override_ver or meta.get("version") or "1.0-draft"
    title = meta.get("title") or "Điều khoản cam kết sử dụng chữ ký số nội bộ"
    sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return {
        "version": version,
        "title": title,
        "content": content,
        "sha256": sha256,
    }
