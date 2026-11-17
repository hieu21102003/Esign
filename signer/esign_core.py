"""Lõi ký/xác minh PDF (PAdES) bằng pyHanko. Dùng chung cho CLI và backend."""
from __future__ import annotations
import io, os
from dataclasses import dataclass, field
from datetime import datetime

from pyhanko.pdf_utils import images
from pyhanko.pdf_utils.font import opentype
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.layout import AxisAlignment, Margins, SimpleBoxLayoutRule
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign import fields, signers, timestamps
from pyhanko.sign.signers.pdf_signer import PdfSignatureMetadata, PdfSigner
from pyhanko.sign.validation import validate_pdf_signature
from pyhanko.stamp import TextStampStyle
from pyhanko.pdf_utils.text import TextBoxStyle
from pyhanko_certvalidator import ValidationContext
from pyhanko_certvalidator.registry import SimpleTrustManager
from asn1crypto import pem, x509, crl as asn1crl

# Font mặc định: Noto Sans (1000 đơn vị/em, đủ dấu tiếng Việt). Lưu ý: với pyHanko 0.38 các font
# 2048 đơn vị/em (DejaVu, Liberation, Arial...) bị giãn chữ ~2 lần -> chỉ dùng font 1000 đơn vị/em.
DEFAULT_FONT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "NotoSans-Regular.ttf")


def _load_certs(path):
    data = open(path, "rb").read()
    if pem.detect(data):
        return [x509.Certificate.load(der) for _, _, der in pem.unarmor(data, multiple=True)]
    return [x509.Certificate.load(data)]


def _load_crls(paths):
    out = []
    for p in paths or []:
        data = open(p, "rb").read()
        if pem.detect(data):
            data = pem.unarmor(data)[2]
        out.append(asn1crl.CertificateList.load(data))
    return out


def make_vc(root_path, chain_path=None, crl_paths=None, revocation="require"):
    roots = _load_certs(root_path)
    others = _load_certs(chain_path) if chain_path else []
    return ValidationContext(
        trust_roots=roots, other_certs=others, crls=_load_crls(crl_paths),
        allow_fetching=False, revocation_mode=revocation,
    )


@dataclass
class Appearance:
    mode: str = "both"            # text | image | both
    image_path: str | None = None
    page: int = 0                 # 0-based; -1 = trang cuối
    box: tuple = (40, 40, 260, 110)   # x1,y1,x2,y2 (điểm PDF, gốc dưới-trái)
    show: tuple = ("name", "date", "reason")  # name,title,date,reason,location
    title: str | None = None      # chức danh (tùy chọn)
    font_path: str = DEFAULT_FONT
    font_size: float = 9
    labels: dict = field(default_factory=lambda: {
        "name": "Ký bởi", "title": "Chức danh", "date": "Ngày", "reason": "Nhận xét", "location": "Nơi ký"})


def _stamp_text(ap: Appearance, location: str | None):
    lines = []
    if "name" in ap.show:     lines.append(f"{ap.labels['name']}: %(signer)s")
    if "title" in ap.show and ap.title: lines.append(f"{ap.labels['title']}: %(title)s")
    if "date" in ap.show:     lines.append(f"{ap.labels['date']}: %(ts)s")
    if "location" in ap.show and location: lines.append(f"{ap.labels['location']}: %(location)s")
    if "reason" in ap.show:   lines.append(f"{ap.labels['reason']}: %(reason)s")
    return "\n".join(lines)


def _build_style(ap: Appearance, location):
    from PIL import Image
    font = opentype.GlyphAccumulatorFactory(ap.font_path)
    tbs = TextBoxStyle(font=font, font_size=ap.font_size)
    x1, y1, x2, y2 = ap.box
    w, h = x2 - x1, y2 - y1
    if ap.mode == "text":
        return TextStampStyle(stamp_text=_stamp_text(ap, location), text_box_style=tbs, border_width=0.8)
    if not ap.image_path:
        raise ValueError("Chế độ image/both cần ảnh chữ ký")
    img = Image.open(ap.image_path).convert("RGBA")
    aspect = img.width / img.height
    img_w = min(h * aspect, w * 0.40)
    if ap.mode == "both":
        # Nền = ảnh + vùng trong suốt bên phải, rộng đúng bằng ô chữ ký: ảnh luôn nằm gọn bên trái
        canvas_w = int(round(img.height * (w / h)))
        scale = min(1.0, (canvas_w * 0.40) / img.width)
        thumb = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))))
        canvas = Image.new("RGBA", (canvas_w, img.height), (0, 0, 0, 0))
        canvas.paste(thumb, (0, (img.height - thumb.height) // 2), thumb)
        buf = io.BytesIO(); canvas.save(buf, "PNG"); buf.seek(0)
        bg = images.PdfImage(Image.open(buf))
        img_w = thumb.width / canvas_w * w
    else:
        bg = images.PdfImage(ap.image_path)
    if ap.mode == "image":
        return TextStampStyle(
            stamp_text="", text_box_style=TextBoxStyle(), border_width=0,
            background=bg, background_opacity=1.0,
            background_layout=SimpleBoxLayoutRule(AxisAlignment.ALIGN_MID, AxisAlignment.ALIGN_MID,
                                                  margins=Margins.uniform(2)))
    # both: ảnh bên trái, chữ bên phải
    return TextStampStyle(
        stamp_text=_stamp_text(ap, location), text_box_style=tbs, border_width=0.8,
        background=bg, background_opacity=1.0,
        background_layout=SimpleBoxLayoutRule(AxisAlignment.ALIGN_MID, AxisAlignment.ALIGN_MID,
                                              margins=Margins.uniform(0)),
        inner_content_layout=SimpleBoxLayoutRule(AxisAlignment.ALIGN_MIN, AxisAlignment.ALIGN_MID,
                                                 margins=Margins(left=img_w + 8, right=4, top=3, bottom=3)),
    )


def sign_pdf(in_path, out_path, p12_path, p12_pass, ap: Appearance, reason=None, location=None,
             tsa_url=None, field_name="Signature1", validation_ctx=None, level="B-T"):
    """level: B-B (không timestamp) | B-T (timestamp) | B-LT (timestamp + nhúng revocation info)."""
    signer = signers.SimpleSigner.load_pkcs12(
        p12_path, passphrase=p12_pass.encode() if isinstance(p12_pass, str) else p12_pass,
        ca_chain_files=None)
    with open(in_path, "rb") as f:
        buf = io.BytesIO(f.read())
    w = IncrementalPdfFileWriter(buf, strict=False)
    if getattr(w.prev, "encrypted", False):
        raise ValueError("PDF có mật khẩu/mã hóa: không hỗ trợ")
    n_pages = int(w.root["/Pages"]["/Count"])
    page = ap.page if ap.page >= 0 else n_pages + ap.page
    if not 0 <= page < n_pages:
        raise ValueError(f"Trang {page + 1} không tồn tại (PDF có {n_pages} trang)")
    spec = fields.SigFieldSpec(field_name, on_page=page, box=ap.box)
    # tránh trùng tên field khi ký nhiều lần
    existing = {n for n, _, _ in fields.enumerate_sig_fields(w)}
    i = 1
    while spec.sig_field_name in existing:
        i += 1; spec = fields.SigFieldSpec(f"Signature{i}", on_page=page, box=ap.box)
    meta = PdfSignatureMetadata(
        field_name=spec.sig_field_name, reason=reason or None, location=location or None,
        subfilter=fields.SigSeedSubFilter.PADES, md_algorithm="sha256",
        embed_validation_info=(level == "B-LT"),
        validation_context=validation_ctx if level == "B-LT" else None,
        use_pades_lta=False)
    tst = timestamps.HTTPTimeStamper(tsa_url) if (tsa_url and level != "B-B") else None
    pdf_signer = PdfSigner(meta, signer=signer, timestamper=tst, stamp_style=_build_style(ap, location),
                           new_field_spec=spec)
    params = {"reason": reason or "-", "location": location or "-", "title": ap.title or ""}
    with open(out_path, "wb") as out:
        pdf_signer.sign_pdf(w, output=out, appearance_text_params=params)
    return out_path


def verify_pdf(path, vc):
    out = []
    with open(path, "rb") as f:
        r = PdfFileReader(f)
        for emb in r.embedded_signatures:
            try:
                st = validate_pdf_signature(emb, signer_validation_context=vc)
                out.append(dict(
                    field=emb.field_name, signer=st.signing_cert.subject.human_friendly,
                    intact=st.intact, valid=st.valid, trusted=st.trusted,
                    revoked=getattr(st, "revoked", False),
                    signed_at=str(st.timestamp_validity.timestamp) if st.timestamp_validity else None,
                    reason=emb.sig_object.get("/Reason"),
                    modification=str(st.modification_level.name) if st.modification_level else None,
                    ok=bool(st.intact and st.valid and st.trusted and not getattr(st, 'revoked', False)),
                    summary=st.summary()))
            except Exception as e:  # lỗi cấu trúc -> coi là không hợp lệ
                out.append(dict(field=emb.field_name, error=str(e), valid=False, intact=False, trusted=False, ok=False))
    return out
