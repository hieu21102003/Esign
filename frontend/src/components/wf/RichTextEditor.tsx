import React, { useRef, useState } from "react";
import { SafeHtml } from "./SafeHtml";

interface Props {
  value: string;
  onChange: (html: string) => void;
  label?: string;
  placeholder?: string;
  rows?: number;
  testIdPrefix?: string;
}

export function RichTextEditor({
  value,
  onChange,
  label = "Nội dung hướng dẫn",
  placeholder = "Nhập hướng dẫn cho người thực hiện...",
  rows = 4,
  testIdPrefix = "wf-rte",
}: Props) {
  const taRef = useRef<HTMLTextAreaElement | null>(null);
  const [preview, setPreview] = useState(false);
  const [linkOpen, setLinkOpen] = useState(false);
  const [linkHref, setLinkHref] = useState("");
  const [linkText, setLinkText] = useState("");

  function wrapTag(openTag: string, closeTag: string, sample: string) {
    const el = taRef.current;
    if (!el) {
      onChange(`${value}${openTag}${sample}${closeTag}`);
      return;
    }
    const start = el.selectionStart ?? value.length;
    const end = el.selectionEnd ?? value.length;
    const selected = value.slice(start, end) || sample;
    const next = `${value.slice(0, start)}${openTag}${selected}${closeTag}${value.slice(end)}`;
    onChange(next);
    window.requestAnimationFrame(() => {
      el.focus();
      const pos = start + openTag.length + selected.length + closeTag.length;
      el.setSelectionRange(pos, pos);
    });
  }

  function insertLink() {
    const href = linkHref.trim();
    if (!href) return;
    const safeHref = href.replace(/"/g, "&quot;");
    const txt = (linkText.trim() || href).replace(/</g, "&lt;").replace(/>/g, "&gt;");
    const tag = `<a href="${safeHref}">${txt}</a>`;
    onChange(value ? `${value} ${tag}` : tag);
    setLinkHref("");
    setLinkText("");
    setLinkOpen(false);
  }

  return (
    <div className="wf-rte" data-testid={`${testIdPrefix}-wrap`}>
      <div className="wf-rte-header">
        <span className="muted" style={{ fontWeight: 600 }}>{label}</span>
        <div className="row wrap" style={{ gap: 4 }}>
          {!preview && (
            <>
              <button
                type="button"
                className="btn tiny"
                onClick={() => wrapTag("<b>", "</b>", "in đậm")}
                title="In đậm"
                data-testid={`${testIdPrefix}-bold`}
              >
                <strong>B</strong>
              </button>
              <button
                type="button"
                className="btn tiny"
                onClick={() => wrapTag("<i>", "</i>", "in nghiêng")}
                title="In nghiêng"
                data-testid={`${testIdPrefix}-italic`}
              >
                <em>I</em>
              </button>
              <button
                type="button"
                className="btn tiny"
                onClick={() => wrapTag("<h3>", "</h3>", "Tiêu đề")}
                title="Tiêu đề nhỏ"
              >
                H3
              </button>
              <button
                type="button"
                className="btn tiny"
                onClick={() => wrapTag("<ul>\n  <li>", "</li>\n</ul>", "Mục 1")}
                title="Danh sách dấu chấm"
              >
                • List
              </button>
              <button
                type="button"
                className="btn tiny"
                onClick={() => wrapTag("<ol>\n  <li>", "</li>\n</ol>", "Bước 1")}
                title="Danh sách đánh số"
              >
                1. List
              </button>
              <button
                type="button"
                className="btn tiny"
                onClick={() => setLinkOpen((o) => !o)}
                title="Chèn liên kết"
              >
                Liên kết
              </button>
            </>
          )}
          <button
            type="button"
            className={`btn tiny ${preview ? "mode" : "ghost"}`}
            onClick={() => setPreview((p) => !p)}
            data-testid={`${testIdPrefix}-preview-toggle`}
          >
            {preview ? "Soạn thảo" : "Xem trước"}
          </button>
        </div>
      </div>

      {linkOpen && !preview && (
        <div className="wf-rte-linkbar">
          <input
            value={linkText}
            onChange={(e) => setLinkText(e.target.value)}
            placeholder="Tên hiển thị (ví dụ: Quy định nội bộ)"
          />
          <input
            value={linkHref}
            onChange={(e) => setLinkHref(e.target.value)}
            placeholder="Đường dẫn liên kết (ví dụ: /workflows hoặc /tai-lieu.pdf)"
          />
          <button type="button" className="btn tiny primary" onClick={insertLink}>
            Chèn
          </button>
          <button type="button" className="btn tiny ghost" onClick={() => setLinkOpen(false)}>
            Đóng
          </button>
        </div>
      )}

      {preview ? (
        <div className="wf-rte-preview" data-testid={`${testIdPrefix}-preview`}>
          {value.trim() ? <SafeHtml html={value} /> : <span className="muted">(Chưa có nội dung)</span>}
        </div>
      ) : (
        <textarea
          ref={taRef}
          rows={rows}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          data-testid={`${testIdPrefix}-input`}
        />
      )}
    </div>
  );
}
