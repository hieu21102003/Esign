#!/usr/bin/env python3
"""Client & CLI chuyển đổi Word/Office -> PDF qua container LibreOffice sandbox."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

from worker import DEFAULT_TIMEOUT, validate_office_file

DEFAULT_IMAGE = os.environ.get("ESIGN_CONVERTER_IMAGE", "esign-converter:latest")


def _convert_via_http(
    in_path: str,
    out_path: str,
    original_name: str,
    timeout: int,
    converter_url: str,
) -> str:
    url = converter_url.rstrip("/") + "/convert"
    with open(in_path, "rb") as f:
        data = f.read()
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/octet-stream",
            "X-Filename": original_name,
            "X-Timeout": str(timeout),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout + 5) as resp:
            pdf_bytes = resp.read()
    except urllib.error.HTTPError as exc:
        msg = exc.read().decode("utf-8", errors="replace")
        if exc.code == 400:
            raise ValueError(msg) from exc
        if exc.code == 504:
            raise TimeoutError(msg) from exc
        raise RuntimeError(f"Converter HTTP {exc.code}: {msg}") from exc
    if not pdf_bytes.startswith(b"%PDF-"):
        raise RuntimeError("Phản hồi từ converter không phải PDF hợp lệ")
    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(pdf_bytes)
    return out_path


def convert_to_pdf(
    in_path: str,
    out_path: str,
    original_name: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    image: str = DEFAULT_IMAGE,
    cpus: str = "1.0",
    memory: str = "512m",
    converter_url: str | None = None,
    target_format: str = "pdf",
) -> str:
    """Chuyển đổi file Office (.docx/.doc/.xlsx/.pptx) sang PDF trong sandbox không mạng."""
    orig = original_name or os.path.basename(in_path)
    ext = validate_office_file(in_path, original_name=orig)
    if timeout <= 0 or timeout > DEFAULT_TIMEOUT:
        timeout = DEFAULT_TIMEOUT

    url = converter_url or os.environ.get("CONVERTER_URL")
    if url and target_format == "pdf":
        return _convert_via_http(in_path, out_path, orig, timeout, url)

    tmp_base = "/tmp" if os.name == "posix" and os.path.isdir("/tmp") else None
    job_dir = tempfile.mkdtemp(prefix="esign_job_", dir=tmp_base)
    try:
        if os.name == "posix":
            os.chmod(job_dir, 0o777)
        safe_in = os.path.join(job_dir, f"input{ext}")
        shutil.copyfile(in_path, safe_in)
        if os.name == "posix":
            os.chmod(safe_in, 0o644)

        out_name = f"output.{target_format}"
        cmd = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--cpus",
            str(cpus),
            "--memory",
            str(memory),
            "--memory-swap",
            str(memory),
            "--pids-limit",
            "64",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=256m",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "10001:10001",
            "-v",
            f"{job_dir}:/work:rw",
            image,
            f"/work/input{ext}",
            f"/work/{out_name}",
            "--timeout",
            str(timeout),
        ]
        if target_format == "doc":
            cmd.append("--to-doc")

        try:
            proc = subprocess.run(cmd, timeout=timeout + 10, capture_output=True, text=True)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"Quá thời gian chuyển đổi container ({timeout}s)") from exc

        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            if proc.returncode == 124:
                raise TimeoutError(err or f"Quá thời gian chuyển đổi ({timeout}s)")
            if proc.returncode == 2:
                raise ValueError(err or "File đầu vào không hợp lệ")
            raise RuntimeError(f"Chuyển đổi thất bại (mã {proc.returncode}): {err}")

        produced = os.path.join(job_dir, out_name)
        if not os.path.isfile(produced) or os.path.getsize(produced) == 0:
            raise RuntimeError("Không tìm thấy file PDF đầu ra từ container")
        if target_format == "pdf":
            with open(produced, "rb") as f:
                if f.read(5) != b"%PDF-":
                    raise RuntimeError("File PDF đầu ra từ container không hợp lệ")

        out_dir = os.path.dirname(os.path.abspath(out_path))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        shutil.copyfile(produced, out_path)
        return out_path
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Chuyển đổi Word/Excel/PowerPoint sang PDF bằng LibreOffice sandbox")
    ap.add_argument("input", help="File .docx/.doc/.xlsx/.pptx")
    ap.add_argument("output", help="File PDF đích")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="Timeout tối đa (giây, mặc định 60)")
    ap.add_argument("--image", default=DEFAULT_IMAGE, help="Docker image converter")
    args = ap.parse_args()

    try:
        convert_to_pdf(args.input, args.output, timeout=args.timeout, image=args.image)
        print("Đã chuyển đổi:", args.output)
        return 0
    except Exception as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
