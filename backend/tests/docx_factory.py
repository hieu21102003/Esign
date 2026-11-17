"""Sinh file .docx nhỏ để test hyperlink / template ngoài / OLE."""
from __future__ import annotations

import io
import zipfile

CT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="bin" ContentType="application/vnd.openxmlformats-officedocument.oleObject"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>
</Types>"""
ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" ' \
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'


def _rels(items):
    body = "".join(items)
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + body +
            "</Relationships>")


def make_docx(*, hyperlink=None, template=None, ole=False, marker="XIN CHAO MARKER") -> bytes:
    doc_rels = ['<Relationship Id="rIdSet" Type="%ssettings" Target="settings.xml"/>' % REL]
    body = f"<w:p><w:r><w:t>{marker}</w:t></w:r></w:p>"
    if hyperlink:
        doc_rels.append(f'<Relationship Id="rIdH1" Type="{REL}hyperlink" Target="{hyperlink}" TargetMode="External"/>')
        body += '<w:p><w:hyperlink r:id="rIdH1"><w:r><w:t>LINK_TEXT_OK</w:t></w:r></w:hyperlink></w:p>'
    if ole:
        doc_rels.append(f'<Relationship Id="rIdOle" Type="{REL}oleObject" Target="embeddings/oleObject1.bin"/>')
        body += '<w:p><w:r><w:object><v:shape xmlns:v="urn:schemas-microsoft-com:vml"/></w:object></w:r></w:p>'
    settings = f"<w:settings {W}>" + ('<w:attachedTemplate r:id="rIdT1"/>' if template else "") + "</w:settings>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CT)
        z.writestr("_rels/.rels", ROOT_RELS)
        z.writestr("word/_rels/document.xml.rels", _rels(doc_rels))
        z.writestr("word/document.xml", f'<?xml version="1.0" encoding="UTF-8"?><w:document {W}><w:body>{body}'
                                        '<w:sectPr/></w:body></w:document>')
        z.writestr("word/settings.xml", settings)
        if template:
            z.writestr("word/_rels/settings.xml.rels", _rels(
                [f'<Relationship Id="rIdT1" Type="{REL}attachedTemplate" Target="{template}" TargetMode="External"/>']))
        if ole:
            z.writestr("word/embeddings/oleObject1.bin", b"\xd0\xcf\x11\xe0fake-ole")
    return buf.getvalue()
