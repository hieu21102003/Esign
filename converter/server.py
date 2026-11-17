#!/usr/bin/env python3
"""HTTP service nội bộ cho container LibreOffice converter (sẵn sàng cho M4/M6)."""
from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from worker import DEFAULT_TIMEOUT, MAX_FILE_BYTES, convert_local

# Giới hạn số lần chuyển đổi đồng thời (mỗi lần = 1 tiến trình LibreOffice)
_SLOTS = threading.BoundedSemaphore(int(os.environ.get("CONVERTER_MAX_PARALLEL", "2")))


def _has_egress() -> bool:
    """True nếu container ra được Internet (không mong muốn: converter phải bị cô lập)."""
    for host in ("8.8.8.8", "1.1.1.1"):
        try:
            with socket.create_connection((host, 53), timeout=2):
                return True
        except OSError:
            continue
    return False


class ConverterHandler(BaseHTTPRequestHandler):
    def _send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            return self._send_json(200, {"status": "ok"})
        if self.path == "/netcheck":
            return self._send_json(200, {"egress": _has_egress()})
        self._send_json(404, {"detail": "Not found"})

    def do_POST(self) -> None:
        if self.path != "/convert":
            return self._send_json(404, {"detail": "Not found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self._send_json(400, {"detail": "Content-Length không hợp lệ"})
        if length <= 0 or length > MAX_FILE_BYTES:
            return self._send_json(413, {"detail": f"Kích thước file phải từ 1B đến {MAX_FILE_BYTES}B"})

        filename = self.headers.get("X-Filename", "").strip()
        if not filename:
            return self._send_json(400, {"detail": "Thiếu header X-Filename"})

        try:
            timeout = int(self.headers.get("X-Timeout", str(DEFAULT_TIMEOUT)))
        except ValueError:
            timeout = DEFAULT_TIMEOUT
        timeout = max(1, min(timeout, DEFAULT_TIMEOUT))

        data = self.rfile.read(length)
        if len(data) != length:
            return self._send_json(400, {"detail": "Dữ liệu upload không đầy đủ"})

        if not _SLOTS.acquire(timeout=10):
            return self._send_json(503, {"detail": "Converter đang bận, thử lại sau"})
        try:
          with tempfile.TemporaryDirectory(prefix="esign_srv_") as tmp:
            ext = os.path.splitext(os.path.basename(filename))[1].lower()
            in_path = os.path.join(tmp, f"upload{ext}")
            out_path = os.path.join(tmp, "out.pdf")
            with open(in_path, "wb") as f:
                f.write(data)
            try:
                convert_local(in_path, out_path, timeout=timeout, original_name=filename)
            except ValueError as exc:
                return self._send_json(400, {"detail": str(exc)})
            except TimeoutError as exc:
                return self._send_json(504, {"detail": str(exc)})
            except Exception as exc:
                return self._send_json(500, {"detail": str(exc)})

            with open(out_path, "rb") as f:
                pdf_bytes = f.read()
        finally:
            _SLOTS.release()

        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(pdf_bytes)))
        self.end_headers()
        self.wfile.write(pdf_bytes)

    def log_message(self, format: str, *args) -> None:
        pass


def main() -> None:
    port = int(os.environ.get("PORT", "8001"))
    server = ThreadingHTTPServer(("0.0.0.0", port), ConverterHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
