#!/usr/bin/env python3
"""Kiểm tra bảo mật cho module converter (chặn giả mạo magic bytes, XXE, external link, Zip Slip, path traversal, macro)."""
from __future__ import annotations

import os
import tempfile
import zipfile

from worker import validate_office_file


def _expect_rejected(label: str, fn) -> None:
    try:
        fn()
    except ValueError:
        return
    raise AssertionError(f"Không chặn được vector tấn công: {label}")


def _write_zip(path: str, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


def main() -> int:
    base_ct = b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
    with tempfile.TemporaryDirectory(prefix="esign_sec_") as d:
        fake_docx = os.path.join(d, "fake.docx")
        with open(fake_docx, "wb") as f:
            f.write(b"%PDF-1.7\nnot a docx")
        _expect_rejected("magic bytes giả .docx", lambda: validate_office_file(fake_docx))

        fake_doc = os.path.join(d, "fake.doc")
        with open(fake_doc, "wb") as f:
            f.write(b"plain text disguised as doc")
        _expect_rejected("magic bytes giả .doc", lambda: validate_office_file(fake_doc))

        xxe_docx = os.path.join(d, "xxe.docx")
        _write_zip(
            xxe_docx,
            {
                "[Content_Types].xml": base_ct,
                "word/document.xml": b'<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><w:document/>',
            },
        )
        _expect_rejected("XXE trong word/document.xml", lambda: validate_office_file(xxe_docx))

        ext_docx = os.path.join(d, "ext.docx")
        _write_zip(
            ext_docx,
            {
                "[Content_Types].xml": base_ct,
                "word/_rels/document.xml.rels": (
                    b'<Relationships><Relationship Id="r1" TargetMode="External" '
                    b'Target="http://evil.local/leak.png"/></Relationships>'
                ),
            },
        )
        _expect_rejected("External relationship SSRF", lambda: validate_office_file(ext_docx))

        zipslip_docx = os.path.join(d, "zipslip.docx")
        _write_zip(zipslip_docx, {"[Content_Types].xml": base_ct, "../../evil.txt": b"pwn"})
        _expect_rejected("Zip Slip trong OOXML", lambda: validate_office_file(zipslip_docx))

        macro_docx = os.path.join(d, "macro.docx")
        _write_zip(macro_docx, {"[Content_Types].xml": base_ct, "word/vbaProject.bin": b"MZ"})
        _expect_rejected("VBA macro trong .docx", lambda: validate_office_file(macro_docx))

        valid_zip = os.path.join(d, "ok.docx")
        _write_zip(valid_zip, {"[Content_Types].xml": base_ct, "word/document.xml": b"<w:document/>"})
        _expect_rejected("Path traversal tên file", lambda: validate_office_file(valid_zip, "../../etc/passwd.docx"))
        _expect_rejected("Đuôi file không hợp lệ", lambda: validate_office_file(valid_zip, "payload.exe"))

    print("Security checks passed (8/8 blocked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
