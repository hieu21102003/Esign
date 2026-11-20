"""Làm sạch HTML do người dùng nhập (guide_html, description_html) bằng allowlist — không phụ thuộc thư viện ngoài.

Frontend sẽ hiển thị chuỗi này như HTML, nên server phải loại bỏ script, style, sự kiện on*, URL nguy hiểm.
"""
from __future__ import annotations

import html
from html.parser import HTMLParser
from urllib.parse import urlsplit

ALLOWED_TAGS = {
    "p", "br", "hr", "b", "i", "u", "s", "strong", "em", "ul", "ol", "li", "blockquote", "code", "pre",
    "h1", "h2", "h3", "h4", "span", "div", "a", "table", "thead", "tbody", "tr", "th", "td",
}
VOID_TAGS = {"br", "hr"}
DROP_WITH_CONTENT = {"script", "style", "iframe", "object", "embed", "noscript", "template", "textarea", "svg", "math", "title"}
SAFE_SCHEMES = {"http", "https", "mailto"}
MAX_HTML_LEN = 50_000


def _safe_href(value: str) -> str | None:
    v = "".join(ch for ch in (value or "").strip() if ord(ch) >= 32)
    if not v:
        return None
    try:
        parts = urlsplit(v)
    except ValueError:
        return None
    if parts.scheme.lower() in SAFE_SCHEMES:
        return v
    return None


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):  # noqa: ANN001
        if tag in DROP_WITH_CONTENT:
            self.skip_depth += 1
            return
        if self.skip_depth or tag not in ALLOWED_TAGS:
            return
        attr_txt = ""
        if tag == "a":
            href = next((v for k, v in attrs if k == "href"), None)
            safe = _safe_href(href or "")
            if safe:
                attr_txt = f' href="{html.escape(safe, quote=True)}" rel="noopener noreferrer" target="_blank"'
        elif tag in ("td", "th"):
            for k, v in attrs:
                if k in ("colspan", "rowspan") and (v or "").isdigit() and 1 <= int(v) <= 50:
                    attr_txt += f' {k}="{int(v)}"'
        self.out.append(f"<{tag}{attr_txt}>")
        if tag not in VOID_TAGS:
            self.stack.append(tag)

    def handle_endtag(self, tag):  # noqa: ANN001
        if tag in DROP_WITH_CONTENT:
            if self.skip_depth:
                self.skip_depth -= 1
            return
        if self.skip_depth or tag not in ALLOWED_TAGS or tag in VOID_TAGS:
            return
        if tag in self.stack:
            while self.stack:
                top = self.stack.pop()
                self.out.append(f"</{top}>")
                if top == tag:
                    break

    def handle_data(self, data):  # noqa: ANN001
        if not self.skip_depth:
            self.out.append(html.escape(data, quote=False))

    def close_all(self) -> str:
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        return "".join(self.out)


def sanitize_html(raw: str | None) -> str:
    """Trả HTML đã lọc theo allowlist. Chuỗi quá dài bị cắt ở tầng validate (không cắt ở đây)."""
    if not raw:
        return ""
    p = _Sanitizer()
    p.feed(raw)
    p.close()
    return p.close_all()
