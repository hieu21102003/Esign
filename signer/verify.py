#!/usr/bin/env python3
"""Xác minh chữ ký: verify.py file.pdf --root root.crt --chain chain.pem [--crl int.crl --crl root.crl]"""
import argparse, json
from esign_core import make_vc, verify_pdf
ap = argparse.ArgumentParser()
ap.add_argument("pdf"); ap.add_argument("--root", required=True); ap.add_argument("--chain")
ap.add_argument("--crl", action="append"); ap.add_argument("--no-revocation", action="store_true")
a = ap.parse_args()
vc = make_vc(a.root, a.chain, a.crl, revocation="soft-fail" if a.no_revocation else "require")
res = verify_pdf(a.pdf, vc)
print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
raise SystemExit(0 if res and all(r.get("ok") for r in res) else 1)
