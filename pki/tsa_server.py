#!/usr/bin/env python3
"""TSA RFC 3161 tối giản, dùng `openssl ts -reply`. Chạy: PKI_DIR=... python3 tsa_server.py [port]
Chỉ dùng nội bộ. Khóa TSA nằm ở $PKI_DIR/tsa/tsa.key (quyền 600)."""
import os, subprocess, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PKI_DIR = os.environ.get("PKI_DIR", os.path.join(HERE, "data"))
CNF = os.path.join(HERE, "openssl.cnf")
LOCK = threading.Lock()  # openssl ts dùng file serial, tuần tự hóa cho an toàn

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        if n <= 0 or n > 10_000:
            return self._err(400)
        query = self.rfile.read(n)
        with LOCK, tempfile.TemporaryDirectory() as d:
            q, r = os.path.join(d, "q.tsq"), os.path.join(d, "r.tsr")
            open(q, "wb").write(query)
            env = dict(os.environ, PKI_DIR=PKI_DIR, CRL_BASE=os.environ.get("CRL_BASE", "http://localhost"))
            p = subprocess.run(["openssl", "ts", "-reply", "-config", CNF, "-section", "tsa_config",
                                "-queryfile", q, "-out", r], env=env, capture_output=True)
            if p.returncode != 0:
                sys.stderr.write(p.stderr.decode(errors="replace"))
                return self._err(500)
            data = open(r, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", "application/timestamp-reply")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers(); self.wfile.write(data)
    def _err(self, code):
        self.send_response(code); self.end_headers()
    def log_message(self, *a): pass

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8318
    HTTPServer(("127.0.0.1", port), H).serve_forever()
