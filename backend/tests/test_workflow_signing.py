"""Kiểm thử Milestone W2: tạo tài liệu, assign nhiều người (all/any), ký nối tiếp & khóa đồng thời."""
from __future__ import annotations

import base64
import hashlib
import io
import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from pypdf import PdfReader, PdfWriter
from sqlalchemy import select

from conftest import ROOT, TEST_DATA, read


def _make_pdf(pages: int = 2) -> bytes:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _assign_cert_db(env, user_id: int, key_ref: str, subject: str = "") -> int:
    from app.models import Certificate, UserCertificate

    sf = env.app.state.session_factory
    db = sf()
    try:
        cert = db.scalar(select(Certificate).where(Certificate.key_ref == key_ref))
        if cert is None:
            cert = Certificate(
                key_ref=key_ref,
                subject=subject or key_ref.upper(),
                serial=f"10{user_id}",
                not_after="2030-01-01T00:00:00+00:00",
                active=True,
            )
            db.add(cert)
            db.flush()
        if db.get(UserCertificate, (user_id, cert.id)) is None:
            db.add(UserCertificate(user_id=user_id, cert_id=cert.id))
        db.commit()
        return cert.id
    finally:
        db.close()


def _upload_pdf(api, name: str = "contract.pdf", pages: int = 2) -> str:
    r = api.post("/api/files", files={"file": (name, _make_pdf(pages), "application/pdf")})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _sign_body(cert_id: int, **kw) -> dict:
    body = {
        "cert_id": cert_id,
        "mode": "text",
        "page": 1,
        "box": [40, 40, 240, 100],
        "reason": "Đồng ý ký duyệt",
        "show": ["name", "date", "reason"],
        "level": "B-T",
    }
    body.update(kw)
    return body


def _parse_fake_sigs(pdf: bytes) -> list[dict]:
    sigs = []
    for raw_line in pdf.splitlines():
        if raw_line.startswith(b"%ESIGN-SIG:"):
            parts = raw_line[len(b"%ESIGN-SIG:"):].decode("utf-8", "replace").split(":", 1)
            sigs.append({
                "field": f"Signature{len(sigs) + 1}",
                "signer": parts[0],
                "reason": parts[1] if len(parts) > 1 else "",
                "intact": True,
                "valid": True,
                "trusted": True,
                "revoked": False,
                "ok": True,
            })
    return sigs


def _patch_fake_signer(monkeypatch, *, delay: float = 0.0, recorder: list | None = None):
    import app.routes_workflow as rw

    def _fake_sign(st, payload):  # noqa: ARG001
        pdf = base64.b64decode(payload["pdf_b64"])
        in_sha = hashlib.sha256(pdf).hexdigest()
        if delay > 0:
            time.sleep(delay)
        marker = f"\n%ESIGN-SIG:{payload['key_ref']}:{payload.get('reason') or '-'}\n".encode("utf-8")
        out = pdf + marker
        if recorder is not None:
            recorder.append({
                "key_ref": payload["key_ref"],
                "in_sha256": in_sha,
                "out_sha256": hashlib.sha256(out).hexdigest(),
            })
        return out

    def _fake_verify(st, pdf):  # noqa: ARG001
        return _parse_fake_sigs(pdf)

    monkeypatch.setattr(rw, "signer_sign", _fake_sign)
    monkeypatch.setattr(rw, "signer_verify", _fake_verify)


def test_w2_assignable_users_and_workflow_file_guards(env_light):
    a = env_light.make_user("alice")
    b = env_light.make_user("bob")
    dis = env_light.make_user("offuser")
    assert env_light.admin.post(f"/api/admin/users/{dis.uid}/disable").status_code == 200
    cert_a = _assign_cert_db(env_light, a.uid, "cert_a", "Alice")

    # Người dùng thường gọi được /api/users/assignable, chỉ thấy user active và biết ai đã có cert
    assignable = a.get("/api/users/assignable").json()
    by_name = {u["username"]: u for u in assignable}
    assert {"alice", "bob", "admin"} <= set(by_name)
    assert "offuser" not in by_name
    assert by_name["alice"]["has_cert"] is True
    assert by_name["bob"]["has_cert"] is False

    fid = _upload_pdf(a, "to-trinh.pdf")
    assert any(f["id"] == fid for f in a.get("/api/files").json())

    # Người khác không thể lấy file của Alice để tạo luồng ký
    assert b.post("/api/documents", json={"file_id": fid, "title": "Trộm file", "assignee_ids": [b.uid]}).status_code == 404

    # Tiêu đề rỗng / user bị vô hiệu hóa bị từ chối
    assert a.post("/api/documents", json={"file_id": fid, "title": "  \n ", "assignee_ids": [b.uid]}).status_code == 422
    assert a.post("/api/documents", json={"file_id": fid, "title": "Hợp lệ", "assignee_ids": [dis.uid]}).status_code == 400

    r = a.post("/api/documents", json={
        "file_id": fid, "title": "  Tờ trình\nSố 1  ", "description": "Mô tả",
        "assignee_ids": [b.uid, b.uid], "mode": "all",
    })
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["title"] == "Tờ trình Số 1"
    assert len(doc["steps"][0]["assignees"]) == 1  # đã khử trùng

    # File thuộc luồng ký được tách khỏi luồng ký lẻ
    assert all(f["id"] != fid for f in a.get("/api/files").json())
    assert a.delete(f"/api/files/{fid}").status_code == 409
    assert a.post("/api/sign", json={"file_id": fid, **_sign_body(cert_a)}).status_code == 409
    assert a.post("/api/documents", json={"file_id": fid, "title": "Tạo lại", "assignee_ids": [b.uid]}).status_code == 409


def test_w2_full_4_signer_flow_all_mode_and_permissions(env_light, monkeypatch):
    _patch_fake_signer(monkeypatch)
    ua = env_light.make_user("user_a")
    ub = env_light.make_user("user_b")
    uc = env_light.make_user("user_c")
    ud = env_light.make_user("user_d")
    ue = env_light.make_user("outsider_e")

    ca = _assign_cert_db(env_light, ua.uid, "cert_a", "Người A")
    cb = _assign_cert_db(env_light, ub.uid, "cert_b", "Người B")
    cc = _assign_cert_db(env_light, uc.uid, "cert_c", "Người C")
    cd = _assign_cert_db(env_light, ud.uid, "cert_d", "Người D")
    ce = _assign_cert_db(env_light, ue.uid, "cert_e", "Người E")

    fid = _upload_pdf(ua, "quyet-dinh.pdf")
    r = ua.post("/api/documents", json={"file_id": fid, "title": "Quyết định ban hành", "assignee_ids": [ua.uid]})
    assert r.status_code == 201, r.text
    doc_id = r.json()["id"]

    # Bước 1 chờ A
    assert [d["id"] for d in ua.get("/api/documents?scope=pending").json()] == [doc_id]
    assert ub.get("/api/documents?scope=pending").json() == []
    assert ub.get(f"/api/documents/{doc_id}").status_code == 404

    # Bước 1: A ký và assign tiếp cho B & C (mode=all)
    rs = ua.post(f"/api/documents/{doc_id}/sign", json=_sign_body(
        ca, reason="A trình", next_action="assign", next_assignee_ids=[ub.uid, uc.uid], next_mode="all",
    ))
    assert rs.status_code == 200, rs.text
    assert rs.json()["version_no"] == 1 and rs.json()["step_completed"] is True
    assert rs.json()["document"]["current_step_order"] == 2

    # B & C thấy trong chờ ký; A không còn chờ ký nhưng vẫn thấy trong created
    assert [d["id"] for d in ub.get("/api/documents?scope=pending").json()] == [doc_id]
    assert [d["id"] for d in uc.get("/api/documents?scope=pending").json()] == [doc_id]
    assert ua.get("/api/documents?scope=pending").json() == []
    assert [d["id"] for d in ua.get("/api/documents?scope=created").json()] == [doc_id]

    # Người ngoài (E) không xem/tải/xác minh/ký được; A cũng không ký được bước 2
    assert ue.get(f"/api/documents/{doc_id}").status_code == 404
    assert ue.get(f"/api/documents/{doc_id}/file").status_code == 404
    assert ue.post(f"/api/documents/{doc_id}/verify").status_code == 404
    assert ue.post(f"/api/documents/{doc_id}/sign", json=_sign_body(ce)).status_code == 404
    assert ua.post(f"/api/documents/{doc_id}/sign", json=_sign_body(ca)).status_code == 403

    # Bước 2 (mode=all): B là người ký đầu không được chọn assign/hoàn tất ngay khi ký
    assert ub.get(f"/api/documents/{doc_id}").json()["can_decide_next_on_sign"] is False
    r_bad_next = ub.post(f"/api/documents/{doc_id}/sign", json=_sign_body(
        cb, next_action="assign", next_assignee_ids=[ud.uid],
    ))
    assert r_bad_next.status_code == 409

    # B ký phần của mình; không được ký lại cùng bước; chưa được gọi /assign hay /complete
    rb = ub.post(f"/api/documents/{doc_id}/sign", json=_sign_body(cb, box=[300, 40, 520, 100], reason="B nhất trí"))
    assert rb.status_code == 200, rb.text
    assert rb.json()["version_no"] == 2 and rb.json()["step_completed"] is False
    assert ub.post(f"/api/documents/{doc_id}/sign", json=_sign_body(cb)).status_code == 409
    assert ub.post(f"/api/documents/{doc_id}/assign", json={"assignee_ids": [ud.uid]}).status_code == 409
    assert ub.post(f"/api/documents/{doc_id}/complete").status_code == 409

    # C ký xong bước 2 -> bước 2 hoàn tất, C trở thành người được chọn assign tiếp / hoàn tất
    assert uc.get(f"/api/documents/{doc_id}").json()["can_decide_next_on_sign"] is True
    rc = uc.post(f"/api/documents/{doc_id}/sign", json=_sign_body(cc, page=2, box=[40, 40, 240, 100], reason="C duyệt"))
    assert rc.status_code == 200, rc.text
    assert rc.json()["version_no"] == 3 and rc.json()["step_completed"] is True
    assert rc.json()["document"]["awaiting_next_step"] is True
    assert rc.json()["document"]["can_assign_or_complete"] is True
    assert ub.get(f"/api/documents/{doc_id}").json()["can_assign_or_complete"] is False

    # B không có quyền assign tiếp; C assign tiếp cho D
    assert ub.post(f"/api/documents/{doc_id}/assign", json={"assignee_ids": [ud.uid]}).status_code == 403
    ra = uc.post(f"/api/documents/{doc_id}/assign", json={"assignee_ids": [ud.uid], "mode": "all"})
    assert ra.status_code == 200, ra.text
    assert ra.json()["current_step_order"] == 3

    # Bước 3: D ký và hoàn tất luồng
    rd = ud.post(f"/api/documents/{doc_id}/sign", json=_sign_body(
        cd, page=2, box=[300, 40, 520, 100], reason="D ký ban hành", next_action="complete",
    ))
    assert rd.status_code == 200, rd.text
    final_doc = rd.json()["document"]
    assert rd.json()["version_no"] == 4
    assert final_doc["status"] == "completed" and final_doc["completed_at"] is not None
    assert [v["version_no"] for v in final_doc["versions"]] == [0, 1, 2, 3, 4]

    # Sau hoàn tất không thể ký hay complete lại
    assert ud.post(f"/api/documents/{doc_id}/complete").status_code == 409
    assert ua.post(f"/api/documents/{doc_id}/sign", json=_sign_body(ca)).status_code == 409

    # Tải mọi phiên bản & kiểm tra bản PDF cuối có đủ 4 chữ ký đúng thứ tự
    for vno in range(5):
        rv = env_light.admin.get(f"/api/documents/{doc_id}/versions/{vno}/file")
        assert rv.status_code == 200 and rv.content.startswith(b"%PDF-")
    final_pdf = ua.get(f"/api/documents/{doc_id}/file").content
    sigs = _parse_fake_sigs(final_pdf)
    assert [s["signer"] for s in sigs] == ["cert_a", "cert_b", "cert_c", "cert_d"]
    ver = uc.post(f"/api/documents/{doc_id}/verify")
    assert ver.status_code == 200 and ver.json()["all_ok"] is True and len(ver.json()["signatures"]) == 4

    # Người ngoài vẫn bị chặn sau khi hoàn tất; admin xem được
    assert ue.get(f"/api/documents/{doc_id}").status_code == 404
    assert env_light.admin.get(f"/api/documents/{doc_id}").status_code == 200


def test_w2_any_mode_skips_other_assignees(env_light, monkeypatch):
    _patch_fake_signer(monkeypatch)
    ua = env_light.make_user("any_a")
    ub = env_light.make_user("any_b")
    uc = env_light.make_user("any_c")
    cb = _assign_cert_db(env_light, ub.uid, "cert_any_b")
    cc = _assign_cert_db(env_light, uc.uid, "cert_any_c")

    fid = _upload_pdf(ua, "any.pdf")
    doc_id = ua.post("/api/documents", json={
        "file_id": fid, "title": "Thông báo chế độ ANY",
        "assignee_ids": [ub.uid, uc.uid], "mode": "any",
    }).json()["id"]

    assert ub.get(f"/api/documents/{doc_id}").json()["can_decide_next_on_sign"] is True
    assert uc.get(f"/api/documents/{doc_id}").json()["can_decide_next_on_sign"] is True

    rs = ub.post(f"/api/documents/{doc_id}/sign", json=_sign_body(cb, reason="B chốt", next_action="complete"))
    assert rs.status_code == 200, rs.text
    doc = rs.json()["document"]
    assert doc["status"] == "completed"
    step = doc["steps"][0]
    assert step["status"] == "completed"
    by_user = {a["user"]["id"]: a["status"] for a in step["assignees"]}
    assert by_user == {ub.uid: "signed", uc.uid: "skipped"}

    # C không còn trong danh sách chờ và bị chặn khi thử ký
    assert uc.get("/api/documents?scope=pending").json() == []
    assert uc.post(f"/api/documents/{doc_id}/sign", json=_sign_body(cc)).status_code == 409


def test_w2_concurrent_parallel_signing_preserves_both_signatures(env_light, monkeypatch):
    recorder: list[dict] = []
    _patch_fake_signer(monkeypatch, delay=0.15, recorder=recorder)

    ua = env_light.make_user("con_a")
    ub = env_light.make_user("con_b")
    uc = env_light.make_user("con_c")
    cb = _assign_cert_db(env_light, ub.uid, "cert_con_b")
    cc = _assign_cert_db(env_light, uc.uid, "cert_con_c")

    # Mỗi luồng dùng một client đăng nhập độc lập
    ub_t = env_light.login("con_b", "Str0ng-Passw0rd-Ok", ub.secret)
    uc_t = env_light.login("con_c", "Str0ng-Passw0rd-Ok", uc.secret)

    fid = _upload_pdf(ua, "parallel.pdf")
    doc_id = ua.post("/api/documents", json={
        "file_id": fid, "title": "Ký song song", "assignee_ids": [ub.uid, uc.uid], "mode": "all",
    }).json()["id"]

    barrier = threading.Barrier(2)

    def _call(api_client, cert_id, box, reason):
        barrier.wait(timeout=5)
        return api_client.post(f"/api/documents/{doc_id}/sign", json=_sign_body(cert_id, box=box, reason=reason))

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(_call, ub_t, cb, [40, 40, 240, 100], "B ký song song")
        f2 = pool.submit(_call, uc_t, cc, [300, 40, 500, 100], "C ký song song")
        r1, r2 = f1.result(timeout=15), f2.result(timeout=15)

    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert sorted([r1.json()["version_no"], r2.json()["version_no"]]) == [1, 2]

    # Phải được tuần tự hóa: đầu vào người thứ hai chính là đầu ra người thứ nhất
    assert len(recorder) == 2
    assert recorder[1]["in_sha256"] == recorder[0]["out_sha256"]

    final_pdf = ua.get(f"/api/documents/{doc_id}/file").content
    sigs = _parse_fake_sigs(final_pdf)
    assert {s["signer"] for s in sigs} == {"cert_con_b", "cert_con_c"}

    detail_b = ub.get(f"/api/documents/{doc_id}").json()
    detail_c = uc.get(f"/api/documents/{doc_id}").json()
    assert detail_b["steps"][0]["status"] == "completed"
    assert detail_b["awaiting_next_step"] is True
    assert [v["version_no"] for v in detail_b["versions"]] == [0, 1, 2]
    # Đúng 1 người (người ký sau cùng) có quyền hoàn tất
    assert detail_b["can_assign_or_complete"] ^ detail_c["can_assign_or_complete"]

    finisher = ub if detail_b["can_assign_or_complete"] else uc
    rc = finisher.post(f"/api/documents/{doc_id}/complete")
    assert rc.status_code == 200 and rc.json()["status"] == "completed"


@pytest.mark.skipif(not os.environ.get("SIGNER_URL"), reason="Cần service signer thật trong Docker Compose")
def test_w2_e2e_4_signers_real_pyhanko_and_concurrency(env):
    from esign_core import make_vc, verify_pdf

    pki_dir = os.environ.get("PKI_DIR", TEST_DATA)
    frank_p12 = os.path.join(pki_dir, "issued", "frank.p12")
    if not os.path.isfile(frank_p12) and os.environ.get("INT_PASS"):
        p = subprocess.run(
            ["bash", os.path.join(ROOT, "pki", "ca.sh"), "issue", "frank", "Đặng Thị F", "365"],
            cwd=os.path.join(ROOT, "pki"), capture_output=True, text=True,
        )
        assert p.returncode == 0, p.stderr
        try:
            os.chown(frank_p12, 10002, 10002)
        except PermissionError:
            pass

    for kr in ("bob", "frank"):
        if kr not in env.certs:
            env.register_certs(kr)

    ua = env.make_user("wf_e2e_a", cert_keys=["bob"])
    ub = env.make_user("wf_e2e_b", cert_keys=["carol"])
    uc = env.make_user("wf_e2e_c", cert_keys=["dave"])
    ud = env.make_user("wf_e2e_d", cert_keys=["frank"])

    up = ua.post("/api/files", files={"file": ("hop-dong.pdf", read("sample.pdf"), "application/pdf")})
    assert up.status_code == 201, up.text
    fid = up.json()["id"]

    img_res = uc.post("/api/signature-images", files={"file": ("sig.png", read("sig.png"), "image/png")})
    assert img_res.status_code == 201, img_res.text
    img_id = img_res.json()["id"]

    doc_id = ua.post("/api/documents", json={
        "file_id": fid, "title": "Hợp đồng E2E 4 người ký", "assignee_ids": [ua.uid],
    }).json()["id"]

    # A ký bước 1 và assign cho B & C song song (all)
    r_a = ua.post(f"/api/documents/{doc_id}/sign", json=_sign_body(
        env.certs["bob"], page=1, box=[30, 40, 260, 110], reason="A trình duyệt",
        next_action="assign", next_assignee_ids=[ub.uid, uc.uid], next_mode="all",
    ))
    assert r_a.status_code == 200, r_a.text

    # B & C ký gần như đồng thời ở bước 2 (hai vị trí khác nhau)
    ub_t = env.login("wf_e2e_b", "Str0ng-Passw0rd-Ok", ub.secret)
    uc_t = env.login("wf_e2e_c", "Str0ng-Passw0rd-Ok", uc.secret)
    barrier = threading.Barrier(2)

    def _par_sign(client, payload):
        barrier.wait(timeout=10)
        return client.post(f"/api/documents/{doc_id}/sign", json=payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        fb = pool.submit(_par_sign, ub_t, _sign_body(
            env.certs["carol"], page=1, box=[310, 40, 550, 110], reason="B kiểm tra",
        ))
        fc = pool.submit(_par_sign, uc_t, _sign_body(
            env.certs["dave"], mode="both", image_id=img_id, page=2, box=[30, 40, 280, 120], reason="C đồng ý",
        ))
        rb, rc = fb.result(timeout=60), fc.result(timeout=60)

    assert rb.status_code == 200, rb.text
    assert rc.status_code == 200, rc.text

    # Người hoàn tất bước 2 assign tiếp cho D
    det_b = ub.get(f"/api/documents/{doc_id}").json()
    completer = ub if det_b["can_assign_or_complete"] else uc
    ra2 = completer.post(f"/api/documents/{doc_id}/assign", json={"assignee_ids": [ud.uid], "mode": "all"})
    assert ra2.status_code == 200, ra2.text

    # D ký bước 3 và hoàn tất
    rd = ud.post(f"/api/documents/{doc_id}/sign", json=_sign_body(
        env.certs["frank"], page=-1, box=[310, 40, 550, 110], reason="D ký ban hành", next_action="complete",
    ))
    assert rd.status_code == 200, rd.text
    assert rd.json()["document"]["status"] == "completed"

    # Kiểm chứng 4 chữ ký hợp lệ qua API, pyHanko trực tiếp và pdfsig
    v_api = ua.post(f"/api/documents/{doc_id}/verify")
    assert v_api.status_code == 200, v_api.text
    assert v_api.json()["all_ok"] is True and len(v_api.json()["signatures"]) == 4

    final_pdf = ua.get(f"/api/documents/{doc_id}/file").content
    pub = os.path.join(pki_dir, "public")
    vc = make_vc(os.path.join(pub, "root.crt"), os.path.join(pub, "chain.pem"),
                 [os.path.join(pub, "int.crl"), os.path.join(pub, "root.crl")], revocation="require")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "final.pdf")
        open(p, "wb").write(final_pdf)
        sigs = verify_pdf(p, vc)
        assert len(sigs) == 4
        assert all(s["ok"] and s["intact"] and s["valid"] and s["trusted"] and not s["revoked"] for s in sigs)
        if shutil.which("pdfsig"):
            ps = subprocess.run(["pdfsig", "-nocert", p], capture_output=True, text=True).stdout
            assert ps.count("Signature is Valid") == 4
