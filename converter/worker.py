#!/usr/bin/env python3
"""Kiểm tra bảo mật tài liệu Office và chuyển đổi sang PDF bằng LibreOffice headless."""
from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET

ALLOWED_EXTS = {".docx", ".doc", ".xlsx", ".pptx"}
OOXML_EXTS = {".docx", ".xlsx", ".pptx"}
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_ZIP_ENTRIES = 2000
DEFAULT_TIMEOUT = 60

MAGIC_ZIP = b"PK\x03\x04"
MAGIC_OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

BLOCKED_EMBED_EXTS = (
    "vbaproject.bin",
    ".exe",
    ".dll",
    ".bat",
    ".cmd",
    ".ps1",
    ".vbs",
    ".vbe",
    ".js",
    ".jse",
    ".wsf",
    ".sh",
    ".scr",
    ".com",
    ".msi",
    ".hta",
)

_RE_XXE = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
_RE_EXT_URL = re.compile(
    r'xlink:href\s*=\s*["\']\s*(?:file:|https?:|ftp:|gopher:|\\\\)',
    re.IGNORECASE,
)
# Phần tử OLE/ActiveX nhúng (nhị phân) trong gói OOXML
_RE_OLE_PART = re.compile(r"(?:/|^)(?:activex/|[^/]*oleobject[^/]*\.bin$|embeddings/[^/]*\.(?:bin|ole)$)")

# Loại quan hệ bị chặn tuyệt đối (OLE, template ngoài, macro, liên kết sổ tính ngoài...)
_BLOCKED_REL_TYPES = {
    "oleobject", "attachedtemplate", "vbaproject", "frame", "subdocument", "externallink",
    "externallinkpath", "afchunk", "activex", "activexbinary", "control", "oleobjectlink",
}
_SAFE_HYPERLINK_PREFIXES = ("http://", "https://", "mailto:")
_RE_URI_SCHEME = re.compile(r"^[a-z][a-z0-9+.\-]*:", re.IGNORECASE)


def _validate_rels(name: str, text: str) -> None:
    """Kiểm tra file quan hệ OOXML (.rels).

    Cho phép hyperlink ngoài (http/https/mailto) vì converter không bao giờ truy cập chúng.
    Chặn mọi quan hệ ngoài khác (template ngoài, ảnh/đối tượng liên kết), OLE và gói nhúng.
    """
    try:
        root = ET.fromstring(text)  # đã loại DOCTYPE/ENTITY trước đó nên an toàn trước XXE
    except ET.ParseError as exc:
        raise ValueError(f"File quan hệ bị hỏng: {name}") from exc
    in_chart = "/charts/" in "/" + name.lower()
    for rel in root.iter():
        if not rel.tag.endswith("Relationship"):
            continue
        rtype = (rel.get("Type") or "").rstrip("/").rsplit("/", 1)[-1].lower()
        target = (rel.get("Target") or "").strip()
        external = (rel.get("TargetMode") or "").strip().lower() == "external"
        if rtype in _BLOCKED_REL_TYPES or (rtype == "package" and not in_chart):
            raise ValueError(f"Phát hiện đối tượng nhúng/OLE/template bị cấm ({rtype}) trong {name}")
        if external:
            if rtype == "hyperlink" and target.lower().startswith(_SAFE_HYPERLINK_PREFIXES):
                continue
            raise ValueError(f"Phát hiện liên kết ngoài không được phép ({rtype or 'không rõ loại'}) trong {name}")
        if _RE_URI_SCHEME.match(target) or target.startswith(("\\\\", "//")):
            raise ValueError(f"Quan hệ nội bộ trỏ ra URL/UNC trong {name}")
_RE_DOC_DANGEROUS_FIELD = re.compile(
    r"\b(?:INCLUDEPICTURE|INCLUDETEXT|IMPORT|LINK)\b[^\x00]{0,200}(?:file://|https?://|\\\\\\\\)",
    re.IGNORECASE,
)


def _check_safe_name(name: str) -> str:
    """Chặn path traversal và trả về phần mở rộng hợp lệ."""
    if not name or "\x00" in name:
        raise ValueError("Tên file không hợp lệ")
    if name.startswith(("/", "\\")) or ":" in name:
        raise ValueError("Đường dẫn tên file không hợp lệ (chặn path traversal)")
    parts = re.split(r"[/\\]+", name)
    if any(p == ".." for p in parts):
        raise ValueError("Tên file chứa '..' (chặn path traversal)")
    ext = os.path.splitext(parts[-1])[1].lower()
    if ext not in ALLOWED_EXTS:
        raise ValueError(f"Định dạng '{ext or '(trống)'}' không được hỗ trợ; chỉ nhận .docx, .doc, .xlsx, .pptx")
    return ext


def _validate_zip_entry_path(entry_name: str) -> None:
    """Chặn Zip Slip trong archive OOXML."""
    if not entry_name or "\x00" in entry_name or ":" in entry_name:
        raise ValueError(f"Entry ZIP không hợp lệ: {entry_name!r}")
    if entry_name.startswith(("/", "\\")):
        raise ValueError(f"Entry ZIP chứa đường dẫn tuyệt đối: {entry_name!r}")
    parts = re.split(r"[/\\]+", entry_name)
    if any(p == ".." for p in parts):
        raise ValueError(f"Phát hiện Zip Slip trong archive: {entry_name!r}")


def _validate_ooxml(file_path: str) -> None:
    """Kiểm tra cấu trúc ZIP, chống Zip Bomb, Zip Slip, XXE, External Link và Macro."""
    try:
        zf = zipfile.ZipFile(file_path, "r")
    except zipfile.BadZipFile as exc:
        raise ValueError("File OOXML bị hỏng hoặc không phải định dạng ZIP hợp lệ") from exc

    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise ValueError(f"Tài liệu chứa quá nhiều thành phần ({len(infos)} > {MAX_ZIP_ENTRIES})")
        if "[Content_Types].xml" not in zf.namelist():
            raise ValueError("Thiếu [Content_Types].xml: không phải tài liệu Office OOXML hợp lệ")

        total_uncompressed = 0
        for info in infos:
            _validate_zip_entry_path(info.filename)
            lower_name = info.filename.lower()
            if lower_name.endswith(BLOCKED_EMBED_EXTS):
                raise ValueError(f"Phát hiện thành phần thực thi/macro bị cấm: {info.filename}")
            if _RE_OLE_PART.search(lower_name):
                raise ValueError(f"Phát hiện đối tượng nhúng OLE/ActiveX bị cấm: {info.filename}")

            total_uncompressed += info.file_size
            if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("Tổng dung lượng giải nén vượt quá 100MB (chặn Zip Bomb)")
            if info.file_size > 10 * 1024 * 1024 and (
                info.compress_size == 0 or (info.file_size / info.compress_size) > 100
            ):
                raise ValueError("Tỉ lệ nén bất thường (chặn Zip Bomb)")

            if lower_name.endswith((".xml", ".rels")):
                with zf.open(info, "r") as member:
                    raw = member.read(MAX_UNCOMPRESSED_BYTES + 1)
                if len(raw) > MAX_UNCOMPRESSED_BYTES:
                    raise ValueError("Kích thước XML thực tế vượt quá giới hạn giải nén")
                text = raw.decode("utf-8", errors="replace")
                if _RE_XXE.search(text):
                    raise ValueError(f"Phát hiện khai báo DOCTYPE/ENTITY (chặn XXE) trong {info.filename}")
                if lower_name.endswith(".rels"):
                    _validate_rels(info.filename, text)
                elif _RE_EXT_URL.search(text):
                    raise ValueError(f"Phát hiện liên kết ngoài (External link/SSRF) trong {info.filename}")


def _validate_ole2_doc(file_path: str) -> None:
    """Kiểm tra cơ bản file .doc nhị phân OLE2 chống nhúng XXE/liên kết ngoài."""
    with open(file_path, "rb") as f:
        data = f.read()
    for text in (data.decode("latin1", errors="ignore"), data.decode("utf-16le", errors="ignore")):
        if _RE_XXE.search(text):
            raise ValueError("Phát hiện DOCTYPE/ENTITY trong file .doc")
        if _RE_DOC_DANGEROUS_FIELD.search(text):
            raise ValueError("Phát hiện trường liên kết ngoài nguy hiểm trong file .doc")


def validate_office_file(file_path: str, original_name: str | None = None) -> str:
    """Xác thực tên file, kích thước, magic bytes và nội dung an toàn. Trả về extension."""
    ext = _check_safe_name(original_name if original_name is not None else os.path.basename(file_path))

    if os.path.islink(file_path) or not os.path.isfile(file_path):
        raise ValueError("File đầu vào không tồn tại hoặc không phải file hợp lệ")
    size = os.path.getsize(file_path)
    if size <= 0:
        raise ValueError("File rỗng")
    if size > MAX_FILE_BYTES:
        raise ValueError(f"File vượt quá giới hạn {MAX_FILE_BYTES // (1024 * 1024)}MB")

    with open(file_path, "rb") as f:
        header = f.read(8)

    if ext in OOXML_EXTS:
        if header.startswith(MAGIC_OLE2):
            raise ValueError("Tài liệu có mật khẩu/mã hóa OLE2 hoặc sai định dạng OOXML")
        if not header.startswith(MAGIC_ZIP):
            raise ValueError("Magic bytes không khớp định dạng OOXML (.docx/.xlsx/.pptx)")
        _validate_ooxml(file_path)
    else:
        if not header.startswith(MAGIC_OLE2):
            raise ValueError("Magic bytes không khớp định dạng Word 97-2003 (.doc)")
        _validate_ole2_doc(file_path)
    return ext


def _limit_resources(timeout: int) -> None:
    """Áp dụng giới hạn CPU/FSIZE và tạo session riêng cho tiến trình LibreOffice."""
    os.setsid()
    try:
        import resource

        cpu_lim = max(10, int(timeout) + 5)
        fsize_lim = 100 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_lim, cpu_lim))
        resource.setrlimit(resource.RLIMIT_FSIZE, (fsize_lim, fsize_lim))
    except Exception:
        pass


def convert_local(
    in_path: str,
    out_path: str,
    timeout: int = DEFAULT_TIMEOUT,
    original_name: str | None = None,
    target_format: str = "pdf",
) -> str:
    """Kiểm tra bảo mật rồi gọi LibreOffice headless để chuyển đổi sang PDF (hoặc doc)."""
    if target_format not in ("pdf", "doc"):
        raise ValueError("Định dạng đích không hợp lệ")
    if timeout <= 0 or timeout > DEFAULT_TIMEOUT:
        timeout = DEFAULT_TIMEOUT

    ext = validate_office_file(in_path, original_name=original_name)
    with tempfile.TemporaryDirectory(prefix="esign_lo_") as work_dir:
        safe_in = os.path.join(work_dir, f"document{ext}")
        profile_dir = os.path.join(work_dir, "profile")
        os.makedirs(profile_dir, exist_ok=True)
        shutil.copyfile(in_path, safe_in)

        cmd = [
            "soffice",
            "--headless",
            "--nologo",
            "--nodefault",
            "--nofirststartwizard",
            "--norestore",
            "--nolockcheck",
            f"-env:UserInstallation=file://{profile_dir}",
            "--convert-to",
            target_format,
            "--outdir",
            work_dir,
            safe_in,
        ]
        env = dict(os.environ, HOME=work_dir, LANG="C.UTF-8", LC_ALL="C.UTF-8")
        preexec = (lambda: _limit_resources(timeout)) if os.name == "posix" else None
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            preexec_fn=preexec,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            try:
                if os.name == "posix":
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
                proc.communicate(timeout=3)
            except Exception:
                pass
            raise TimeoutError(f"Quá thời gian chuyển đổi ({timeout}s)") from exc

        if proc.returncode != 0:
            err = (stderr or stdout or b"").decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"LibreOffice chuyển đổi thất bại (mã {proc.returncode}): {err}")

        produced = os.path.join(work_dir, f"document.{target_format}")
        if not os.path.isfile(produced) or os.path.getsize(produced) == 0:
            raise RuntimeError(f"Không tạo được file kết quả .{target_format}")
        if target_format == "pdf":
            with open(produced, "rb") as f:
                if f.read(5) != b"%PDF-":
                    raise RuntimeError("File PDF đầu ra không hợp lệ")

        out_dir = os.path.dirname(os.path.abspath(out_path))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        shutil.copyfile(produced, out_path)
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser(description="Worker chuyển đổi Office -> PDF trong container sandbox")
    ap.add_argument("input", help="Đường dẫn file đầu vào")
    ap.add_argument("output", help="Đường dẫn file PDF đầu ra")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="Giới hạn thời gian (giây)")
    ap.add_argument("--original-name", default=None, help="Tên file gốc để kiểm tra path traversal/đuôi file")
    ap.add_argument("--to-doc", action="store_true", help="Chuyển sang .doc (chỉ dùng tạo file mẫu test)")
    args = ap.parse_args()

    try:
        convert_local(
            args.input,
            args.output,
            timeout=args.timeout,
            original_name=args.original_name,
            target_format="doc" if args.to_doc else "pdf",
        )
        return 0
    except TimeoutError as exc:
        print(f"ERROR_TIMEOUT: {exc}", file=sys.stderr)
        return 124
    except ValueError as exc:
        print(f"ERROR_VALIDATION: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR_CONVERT: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
