"""Khởi chạy pki/tsa_server.py (nguyên bản) trên 0.0.0.0 để các container khác trong mạng nội bộ gọi được."""
import os
import sys
from http.server import HTTPServer

sys.path.insert(0, "/app/pki")
from tsa_server import H  # noqa: E402

HTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8318"))), H).serve_forever()
