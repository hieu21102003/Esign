"""Test API với signer + converter THẬT: IDOR, validate upload, docx, ký, verify, audit, cleanup."""
from __future__ import annotations

import hashlib
import io
import os
import re
import subprocess
import tempfile
import uuid
from datetime import timedelta

import pytest
from pypdf import PdfReader, PdfWriter
from sqlalchemy import text

from conftest import ROOT, USER_PASS, TEST_DATA, need_env, read
from docx_factory import make_docx

from esign_core import make_vc, verify_pdf  # noqa: E402  (signer/ đã được thêm vào sys.path)


def upload(api, name, data):
    return api.post("/api/files", files={"file": (name, data, "application/octet-stream")})


def sign_body(file_id, cert_id, **kw):
    b = dict(file_id=file_id, cert_id=cert_id, mode="text", page=1, box=[300, 40, 560, 110],
             reason="Đã duyệt nội dung", show=["name", "date", "reason"])
    b.update(kw)
    return b


def run_tool(cmd, data):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "f.pdf")
        open(p, "wb").write(data)
        return subprocess.run([*cmd, p], capture_output=True, text=True).stdout


def pdftotext(data):
    return run_tool(["pdftotext"], data) if False else subprocess.run(
        ["pdftotext", "-", "-"], input=data, capture_output=True).stdout.decode("utf-8", "replace")


def pdfsig(data):
    return run_tool(["pdfsig", "-nocert"], data)


def pyhanko_verify(data):
    pub = os.path.join(os.environ.get("PKI_DIR", TEST_DATA), "public")
    vc = make_vc(os.path.join(pub, "root.crt"), os.path.join(pub, "chain.pem"),
                 [os.path.join(pub, "int.crl"), os.path.join(pub, "root.crl")], revocation="require")
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "v.pdf")
        open(p, "wb").write(data)
        return verify_pdf(p, vc)


def flip_header_byte(data: bytes) -> bytes:
    """Sửa 1 byte nằm trong vùng được ký (dòng comment nhị phân ở đầu file) mà không làm hỏng cấu trúc PDF."""
    i = data.find(b"\n%", 0, 64)
    i = i + 2 if i >= 0 else 400
    b = bytearray(data)
    b[i] ^= 1
    return bytes(b)


def download(api, fid):
    r = api.get(f"/api/files/{fid}/download")
    assert r.status_code == 200, r.text
    return r.content


# ---------------------------------------------------------------- IDOR / phân quyền
def test_idor_user_a_cannot_read_download_verify_delete_or_sign_user_b_files(env):
    a = env.make_user("usera", cert_keys=["carol"])
    b = env.make_user("userb", cert_keys=["carol"])  # cùng chứng thư: chỉ quyền sở hữu file là rào chắn
    fid = upload(a, "secret.pdf", read("sample.pdf")).json()["id"]
    img = a.post("/api/signature-images", files={"file": ("s.png", read("sig.png"), "image/png")}).json()["id"]
    cid = env.certs["carol"]

    assert b.get(f"/api/files/{fid}").status_code == 404
    assert b.get(f"/api/files/{fid}/download").status_code == 404
    assert b.post(f"/api/files/{fid}/verify").status_code == 404
    assert b.delete(f"/api/files/{fid}").status_code == 404
    assert b.post("/api/sign", json=sign_body(fid, cid)).status_code == 404
    assert all(f["id"] != fid for f in b.get("/api/files").json())
    # Dùng ảnh chữ ký của người khác cũng bị chặn
    own = upload(b, "own.pdf", read("sample.pdf")).json()["id"]
    assert b.post("/api/sign", json=sign_body(own, cid, mode="image", image_id=img)).status_code == 404
    # Người dùng không đăng nhập
    from conftest import Api
    assert Api(env.app).get(f"/api/files/{fid}/download").status_code == 401
    # Chủ file vẫn dùng bình thường; admin tải được nhưng KHÔNG ký thay được
    assert a.get(f"/api/files/{fid}/download").status_code == 200
    assert env.admin.get(f"/api/files/{fid}/download").status_code == 200
    assert env.admin.post("/api/sign", json=sign_body(fid, cid)).status_code == 404
    # File của A vẫn còn nguyên sau mọi nỗ lực trên
    assert a.get(f"/api/files/{fid}").status_code == 200


def test_user_can_only_sign_with_assigned_certificate(env):
    a = env.make_user("usera", cert_keys=["carol"])
    fid = upload(a, "d.pdf", read("sample.pdf")).json()["id"]
    r = a.post("/api/sign", json=sign_body(fid, env.certs["dave"]))  # dave chưa gán cho A
    assert r.status_code == 403
    assert a.post("/api/sign", json=sign_body(fid, 9999)).status_code == 403
    assert [c["id"] for c in a.get("/api/certificates").json()] == [env.certs["carol"]]
    # Admin vô hiệu chứng thư -> không ký được nữa
    env.admin.post(f"/api/admin/certificates/{env.certs['carol']}/deactivate")
    assert a.post("/api/sign", json=sign_body(fid, env.certs["carol"])).status_code == 403
    # Không ai ký nếu admin chưa gán
    n = env.make_user("nocert")
    fid2 = upload(n, "d.pdf", read("sample.pdf")).json()["id"]
    assert n.post("/api/sign", json=sign_body(fid2, env.certs["carol"])).status_code == 403


def test_presets_are_private(env):
    a, b = env.make_user("usera"), env.make_user("userb")
    pid = a.post("/api/presets", json={"name": "Mặc định", "config": {"mode": "text", "box": [1, 2, 3, 4]}}).json()["id"]
    assert b.delete(f"/api/presets/{pid}").status_code == 404
    assert b.get("/api/presets").json() == []
    assert a.post("/api/presets", json={"name": "x", "config": {"evil": 1}}).status_code == 422
    assert a.delete(f"/api/presets/{pid}").status_code == 200


# ---------------------------------------------------------------- Validate upload
def test_upload_validation(env):
    u = env.make_user("usera")
    assert upload(u, "a.pdf", b"khong phai pdf").status_code == 400                    # sai magic bytes
    assert upload(u, "a.docx", b"%PDF-1.4 gia mao").status_code == 400                 # PDF đội lốt docx
    assert upload(u, "evil.exe", b"MZ....").status_code == 415                         # đuôi không cho phép
    assert upload(u, "a.pdf", b"%PDF-1.4\n/JavaScript (app.alert(1))").status_code == 400
    assert upload(u, "empty.pdf", b"").status_code == 400
    # PDF có mật khẩu
    w = PdfWriter()
    w.append(PdfReader(io.BytesIO(read("sample.pdf"))))
    w.encrypt("matkhau")
    buf = io.BytesIO()
    w.write(buf)
    r = upload(u, "enc.pdf", buf.getvalue())
    assert r.status_code == 400 and "mật khẩu" in r.json()["detail"]
    # Giới hạn 25MB (ở handler) và chặn sớm ở middleware khi body lớn hơn nhiều
    assert upload(u, "big.pdf", b"%PDF-" + b"0" * (25 * 1024 * 1024)).status_code == 413
    assert upload(u, "huge.pdf", b"%PDF-" + b"0" * (27 * 1024 * 1024)).status_code == 413
    # Mọi lần từ chối đều có audit
    acts = [a["action"] for a in env.admin.get("/api/admin/audit?limit=200").json()]
    assert acts.count("upload_rejected") >= 6


def test_filename_path_traversal_is_harmless(env):
    u = env.make_user("usera")
    r = upload(u, "../../etc/passwd.pdf", read("sample.pdf"))
    assert r.status_code == 201 and r.json()["name"] == "passwd.pdf"
    names = os.listdir(env.app.state.settings.upload_dir)
    assert names and all(re.fullmatch(r"[0-9a-f-]{36}", n) for n in names)  # tên file trên đĩa = UUID
    assert u.get("/api/files/..%2F..%2Fetc%2Fpasswd").status_code == 404
    assert u.get("/api/files/not-a-uuid/download").status_code == 404


# ---------------------------------------------------------------- DOCX qua converter thật
def test_docx_with_hyperlink_converts(env):
    u = env.make_user("usera")
    r = upload(u, "link.docx", make_docx(hyperlink="https://example.com/trang", marker="XIN CHAO MARKER"))
    assert r.status_code == 201, r.text
    pdf = download(u, r.json()["id"])
    txt = pdftotext(pdf)
    assert "XIN CHAO MARKER" in txt and "LINK_TEXT_OK" in txt


@pytest.mark.parametrize("kw", [
    dict(template="http://evil.local/t.dotm"),
    dict(template="file:///etc/passwd"),
    dict(template="\\\\evil\\share\\t.dotm"),
    dict(ole=True),
    dict(hyperlink="javascript:alert(1)"),
    dict(hyperlink="file:///etc/passwd"),
], ids=["template-http", "template-file", "template-unc", "ole", "hyperlink-js", "hyperlink-file"])
def test_docx_external_template_and_ole_blocked(env, kw):
    u = env.make_user("usera")
    r = upload(u, "bad.docx", make_docx(**kw))
    assert r.status_code == 400, r.text
    assert u.get("/api/files").json() == []  # không lưu gì


# ---------------------------------------------------------------- End-to-end ký + verify
def test_e2e_docx_sign_verify_tamper_and_chained_signing(env):
    u = env.make_user("usera", cert_keys=["carol", "dave"])
    r = upload(u, "Tờ trình.docx", read("sample.docx"))
    assert r.status_code == 201, r.text
    fid = r.json()["id"]
    img = u.post("/api/signature-images", files={"file": ("sig.png", read("sig.png"), "image/png")}).json()["id"]

    r = u.post("/api/sign", json=sign_body(fid, env.certs["carol"], mode="both", image_id=img, page=-1,
                                           box=[40, 40, 330, 120], reason="Đã duyệt nội dung, đồng ý ban hành"))
    assert r.status_code == 200, r.text
    out = r.json()["output_file"]
    signed = download(u, out["id"])

    # (1) pdfsig (poppler) và (2) pyHanko độc lập cùng xác nhận hợp lệ
    assert "Signature is Valid" in pdfsig(signed)
    res = pyhanko_verify(signed)
    assert len(res) == 1 and res[0]["ok"] and "Đã duyệt nội dung" in str(res[0]["reason"])
    # (3) API verify
    v = u.post(f"/api/files/{out['id']}/verify").json()
    assert v["all_ok"] is True and v["signatures"][0]["intact"] is True

    # Ký nối tiếp bằng chứng thư thứ hai (incremental update)
    r = u.post("/api/sign", json=sign_body(out["id"], env.certs["dave"], page=-1, box=[340, 130, 560, 200],
                                           reason="Đồng ý, ký duyệt"))
    assert r.status_code == 200, r.text
    signed2 = download(u, r.json()["output_file"]["id"])
    v2 = u.post("/api/verify", files={"file": ("x.pdf", signed2, "application/pdf")}).json()
    assert v2["all_ok"] is True and len(v2["signatures"]) == 2

    # Sửa 1 byte -> verify báo lỗi (API, pyHanko và pdfsig)
    bad = flip_header_byte(signed)
    vb = u.post("/api/verify", files={"file": ("t.pdf", bad, "application/pdf")})
    assert vb.status_code == 200, vb.text
    assert vb.json()["all_ok"] is False and vb.json()["signatures"][0]["intact"] is False
    assert not any(s["ok"] for s in pyhanko_verify(bad))
    assert "Digest Mismatch" in pdfsig(bad)


def test_revoked_certificate_makes_verify_fail_and_blocks_new_signing(env):
    need_env("PKI_DIR", "ROOT_PASS", "INT_PASS", "USER_PASS")
    env.register_certs("erin")
    u = env.make_user("usera", cert_keys=["erin"])
    fid = upload(u, "d.pdf", read("sample.pdf")).json()["id"]
    r = u.post("/api/sign", json=sign_body(fid, env.certs["erin"]))
    assert r.status_code == 200, r.text
    out_id = r.json()["output_file"]["id"]
    assert u.post(f"/api/files/{out_id}/verify").json()["all_ok"] is True

    p = subprocess.run(["bash", os.path.join(ROOT, "pki", "ca.sh"), "revoke", "erin"], capture_output=True,
                       text=True, cwd=os.path.join(ROOT, "pki"))
    assert p.returncode == 0, p.stderr

    v = u.post(f"/api/files/{out_id}/verify").json()
    assert v["all_ok"] is False
    assert not any(s["ok"] for s in pyhanko_verify(download(u, out_id)))
    r = u.post("/api/sign", json=sign_body(fid, env.certs["erin"]))
    assert r.status_code == 400 and "thu hồi" in r.json()["detail"]


def test_sign_parameter_validation(env):
    u = env.make_user("usera", cert_keys=["carol"])
    fid = upload(u, "d.pdf", read("sample.pdf")).json()["id"]
    c = env.certs["carol"]
    assert u.post("/api/sign", json=sign_body(fid, c, box=[10, 10, 5000, 80])).status_code == 400   # ngoài trang
    assert u.post("/api/sign", json=sign_body(fid, c, page=99)).status_code == 400
    assert u.post("/api/sign", json=sign_body(fid, c, page=0)).status_code == 422
    assert u.post("/api/sign", json=sign_body(fid, c, box=[100, 100, 90, 90])).status_code == 422
    assert u.post("/api/sign", json=sign_body(fid, c, mode="image")).status_code == 400            # thiếu ảnh
    assert u.post("/api/sign", json=sign_body(fid, c, mode="hack")).status_code == 422
    assert u.post("/api/sign", json=sign_body(fid, c, show=["password"])).status_code == 422
    assert u.post("/api/sign", json=sign_body(fid, c, reason="x" * 501)).status_code == 422


# ---------------------------------------------------------------- Audit log
def test_audit_log_is_complete_append_only_and_has_no_secrets(env):
    u = env.make_user("usera", cert_keys=["carol"])
    data = read("sample.pdf")
    fid = upload(u, "d.pdf", data).json()["id"]
    assert u.post("/api/sign", json=sign_body(fid, env.certs["carol"])).status_code == 200
    env.login_password("usera", "mat-khau-sai-Xx1")  # một lần đăng nhập sai
    rows = env.admin.get("/api/admin/audit?limit=500").json()
    s = next(a for a in rows if a["action"] == "sign")
    assert s["username"] == "usera" and s["cert_key_ref"] == "carol" and s["ip"] and s["ts"]
    assert s["file_sha256"] == hashlib.sha256(data).hexdigest() and s["output_sha256"]
    assert any(a["action"] == "login_failed" and a["username"] == "usera" for a in rows)
    dump = str(rows)
    for secret in (USER_PASS, "mat-khau-sai-Xx1", u.secret, os.environ.get("P12_PASSPHRASE", "userpw")):
        assert secret not in dump
    # API không có đường sửa/xóa audit
    for m in ("put", "delete", "post", "patch"):
        assert env.admin.request(m.upper(), "/api/admin/audit/1").status_code in (404, 405)
        assert env.admin.request(m.upper(), "/api/admin/audit").status_code in (404, 405)
    # Ở tầng DB: trigger chặn UPDATE/DELETE; ORM cũng chặn
    eng = env.app.state.engine
    for sql in ("UPDATE audit_logs SET action='x'", "DELETE FROM audit_logs"):
        with pytest.raises(Exception):
            with eng.begin() as con:
                con.execute(text(sql))
    from app.models import AuditLog
    db = env.app.state.session_factory()
    try:
        row = db.get(AuditLog, 1)
        row.action = "tampered"
        with pytest.raises(PermissionError):
            db.commit()
        db.rollback()
        db.delete(db.get(AuditLog, 1))
        with pytest.raises(PermissionError):
            db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------- Xóa file sau 7 ngày
def test_cleanup_deletes_expired_files_but_keeps_audit(env):
    from app.cleanup import purge_expired
    from app.models import StoredFile, utcnow
    u = env.make_user("usera")
    st = env.app.state.settings
    assert st.retention_days == 7
    fid = upload(u, "d.pdf", read("sample.pdf")).json()["id"]
    keep = upload(u, "keep.pdf", read("sample.pdf")).json()["id"]
    path = os.path.join(st.upload_dir, fid)
    assert os.path.exists(path)
    exp = u.get(f"/api/files/{fid}").json()["expires_at"]
    created = u.get(f"/api/files/{fid}").json()["created_at"]
    from datetime import datetime
    assert datetime.fromisoformat(exp) - datetime.fromisoformat(created) == timedelta(days=7)

    db = env.app.state.session_factory()
    db.get(StoredFile, fid).expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    db.close()
    n_audit = len(env.admin.get("/api/admin/audit?limit=500").json())
    assert purge_expired(st, env.app.state.session_factory) == 1
    assert not os.path.exists(path)
    assert os.path.exists(os.path.join(st.upload_dir, keep))
    assert u.get(f"/api/files/{fid}/download").status_code == 404
    assert u.get(f"/api/files/{keep}/download").status_code == 200
    assert len(env.admin.get("/api/admin/audit?limit=500").json()) >= n_audit  # audit không bị xóa
