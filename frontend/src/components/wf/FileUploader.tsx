import React, { useState } from "react";
import { apiJson, type StoredFile } from "../../lib/api";

interface Props {
  files: StoredFile[];
  onChange: (next: StoredFile[]) => void;
  maxFiles?: number;
  label?: string;
  accept?: string;
  testId?: string;
}

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

export function FileUploader({
  files,
  onChange,
  maxFiles = 10,
  label = "Tải tệp (.pdf, .docx, .doc, .xlsx, .pptx)",
  accept = ".pdf,.docx,.doc,.xlsx,.pptx",
  testId = "wf-file-uploader",
}: Props) {
  const [uploading, setUploading] = useState(false);
  const [err, setErr] = useState("");

  async function handleSelect(e: React.ChangeEvent<HTMLInputElement>) {
    const selected = Array.from(e.target.files || []);
    e.target.value = "";
    if (selected.length === 0) return;
    setErr("");
    if (files.length + selected.length > maxFiles) {
      setErr(`Chỉ cho phép tối đa ${maxFiles} tệp.`);
      return;
    }
    setUploading(true);
    try {
      const added: StoredFile[] = [];
      for (const f of selected) {
        const fd = new FormData();
        fd.append("file", f);
        const res = await apiJson<StoredFile>("/api/files", { method: "POST", body: fd });
        added.push(res);
      }
      onChange([...files, ...added]);
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Tải tệp lên thất bại");
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="stack" style={{ gap: 6 }}>
      <div className="row wrap">
        <label className={`btn small file-btn ${uploading || files.length >= maxFiles ? "disabled" : ""}`}>
          <span>{uploading ? "Đang tải lên..." : label}</span>
          <input
            type="file"
            accept={accept}
            multiple={maxFiles > 1}
            disabled={uploading || files.length >= maxFiles}
            onChange={handleSelect}
            data-testid={testId}
          />
        </label>
        <span className="muted">
          {files.length}/{maxFiles} tệp
        </span>
      </div>
      {err && <div className="alert error">{err}</div>}
      {files.length > 0 && (
        <div className="file-list">
          {files.map((f) => (
            <div key={f.id} className="file-item">
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  <strong>{f.name}</strong>
                </div>
                <div className="muted">
                  {fmtSize(f.size)} · {f.pages} trang
                </div>
              </div>
              <button
                type="button"
                className="btn tiny danger"
                onClick={() => onChange(files.filter((x) => x.id !== f.id))}
                aria-label={`Gỡ ${f.name}`}
              >
                Gỡ
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
