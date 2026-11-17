#!/usr/bin/env python3
"""CLI ký PDF.
Ví dụ:
  sign.py in.pdf out.pdf --p12 alice.p12 --pass-env USER_PASS --tsa http://127.0.0.1:8318 \
          --mode both --image sig.png --page -1 --box 40,40,300,110 \
          --reason "Đã duyệt nội dung" --show name,date,reason
"""
import argparse, os, sys
from esign_core import Appearance, make_vc, sign_pdf

ap = argparse.ArgumentParser()
ap.add_argument("input"); ap.add_argument("output")
ap.add_argument("--p12", required=True)
ap.add_argument("--pass-env", default="USER_PASS", help="tên biến môi trường chứa passphrase .p12")
ap.add_argument("--tsa"); ap.add_argument("--level", default="B-T", choices=["B-B", "B-T", "B-LT"])
ap.add_argument("--mode", default="both", choices=["text", "image", "both"])
ap.add_argument("--image"); ap.add_argument("--page", type=int, default=1, help="1-based; -1 = trang cuối")
ap.add_argument("--box", default="40,40,300,110", help="x1,y1,x2,y2 (pt, gốc dưới-trái)")
ap.add_argument("--reason", help="Nhận xét/lý do — được ký bảo vệ bên trong chữ ký")
ap.add_argument("--location"); ap.add_argument("--title", help="Chức danh hiển thị")
ap.add_argument("--show", default="name,date,reason")
ap.add_argument("--font", default=None)
ap.add_argument("--root"); ap.add_argument("--chain"); ap.add_argument("--crl", action="append")
a = ap.parse_args()

pw = os.environ.get(a.pass_env)
if not pw: sys.exit(f"Thiếu biến môi trường {a.pass_env}")
kw = {"font_path": a.font} if a.font else {}
app = Appearance(mode=a.mode, image_path=a.image, page=(a.page - 1 if a.page > 0 else a.page),
                 box=tuple(float(x) for x in a.box.split(",")), show=tuple(a.show.split(",")),
                 title=a.title, **kw)
vc = make_vc(a.root, a.chain, a.crl, revocation="soft-fail") if a.level == "B-LT" else None
sign_pdf(a.input, a.output, a.p12, pw, app, reason=a.reason, location=a.location,
         tsa_url=a.tsa, validation_ctx=vc, level=a.level)
print("Đã ký:", a.output)
