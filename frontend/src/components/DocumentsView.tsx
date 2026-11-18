import React, { useEffect, useState } from "react";
import { apiJson, type StoredFile } from "../lib/api";

interface Props {
  onOpenSign: (file: StoredFile) => void;
  onGoVerify: (fileId: string) => void;
}

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export function DocumentsView({ onOpenSign, onGoVerify }: Props) {
  const [files, setFiles] = useState<StoredFile[]>([]);
  const [filter, setFilter] = useState<"all" | "upload" | "signed">("all");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  async function loadFiles() {
    setErr("");
    try {
      const list = await apiJson<StoredFile[]>("/api/files");
      setFiles(list);
      setSelectedIds((prev) => prev.filter((id) => list.some((f) => f.id === id)));
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Không tải được danh sách tài liệu");
    }
  }

  useEffect(() => {
    loadFiles();
  }, []);

  const visible = files.filter((f) => filter === "all" || f.kind === filter);
  const allVisibleChecked = visible.length > 0 && visible.every((f) => selectedIds.includes(f.id));

  function toggleOne(id: string) {
    setSelectedIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }

  function toggleAllVisible() {
    const visIds = visible.map((f) => f.id);
    if (allVisibleChecked) {
      setSelectedIds((prev) => prev.filter((id) => !visIds.includes(id)));
    } else {
      setSelectedIds((prev) => Array.from(new Set([...prev, ...visIds])));
    }
  }

  async function deleteOne(f: StoredFile) {
    if (!window.confirm(`Xóa tài liệu "${f.name}"? Thao tác này không hoàn tác được.`)) return;
    setBusy(true);
    setErr("");
    setMsg("");
    try {
      await apiJson(`/api/files/${f.id}`, { method: "DELETE" });
      setMsg(`Đã xóa "${f.name}"`);
      await loadFiles();
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Xóa thất bại");
    } finally {
      setBusy(false);
    }
  }

  async function deleteSelected() {
    if (selectedIds.length === 0) return;
    if (!window.confirm(`Xóa ${selectedIds.length} tài liệu đã chọn? Thao tác này không hoàn tác được.`)) return;
    setBusy(true);
    setErr("");
    setMsg("");
    try {
      await Promise.all(selectedIds.map((id) => apiJson(`/api/files/${id}`, { method: "DELETE" })));
      setMsg(`Đã xóa ${selectedIds.length} tài liệu`);
      setSelectedIds([]);
      await loadFiles();
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Không xóa được toàn bộ tài liệu đã chọn");
      await loadFiles();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card stack">
      <div className="preview-toolbar">
        <div className="row wrap">
          <h2 style={{ marginRight: 8 }}>Quản lý tài liệu</h2>
          {(["all", "upload", "signed"] as const).map((k) => (
            <button
              key={k}
              type="button"
              className={`btn small ${filter === k ? "primary" : ""}`}
              onClick={() => setFilter(k)}
              data-testid={`filter-${k}`}
            >
              {k === "all" ? `Tất cả (${files.length})` : k === "upload" ? "File gốc" : "Đã ký"}
            </button>
          ))}
        </div>
        <div className="row">
          <button type="button" className="btn small" onClick={loadFiles} disabled={busy}>
            Làm mới
          </button>
          <button
            type="button"
            className="btn small danger"
            disabled={busy || selectedIds.length === 0}
            onClick={deleteSelected}
            data-testid="delete-selected-btn"
          >
            Xóa đã chọn ({selectedIds.length})
          </button>
        </div>
      </div>

      {err && <div className="alert error" role="alert">{err}</div>}
      {msg && <div className="alert ok" role="status">{msg}</div>}

      {visible.length === 0 ? (
        <div className="empty-preview">Chưa có tài liệu nào trong mục này</div>
      ) : (
        <table className="tbl" data-testid="docs-table">
          <thead>
            <tr>
              <th style={{ width: 28 }}>
                <input
                  type="checkbox"
                  checked={allVisibleChecked}
                  onChange={toggleAllVisible}
                  aria-label="Chọn tất cả"
                />
              </th>
              <th>Tên tài liệu</th>
              <th>Loại</th>
              <th>Trang</th>
              <th>Dung lượng</th>
              <th>Ngày tạo</th>
              <th>Tự xóa sau</th>
              <th>Thao tác</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((f) => (
              <tr key={f.id}>
                <td>
                  <input
                    type="checkbox"
                    checked={selectedIds.includes(f.id)}
                    onChange={() => toggleOne(f.id)}
                    aria-label={`Chọn ${f.name}`}
                  />
                </td>
                <td><strong>{f.name}</strong></td>
                <td>
                  <span className={`badge ${f.kind === "signed" ? "ok" : ""}`}>
                    {f.kind === "signed" ? "Đã ký" : "Gốc"}
                  </span>
                </td>
                <td>{f.pages}</td>
                <td>{formatSize(f.size)}</td>
                <td className="mono">{f.created_at.slice(0, 16).replace("T", " ")}</td>
                <td className="mono">{f.expires_at.slice(0, 10)}</td>
                <td className="row">
                  <button type="button" className="btn tiny primary" onClick={() => onOpenSign(f)}>
                    {f.kind === "signed" ? "Ký tiếp" : "Mở ký"}
                  </button>
                  <a href={`/api/files/${f.id}/download`} className="btn tiny" download={f.name}>
                    Tải
                  </a>
                  <button type="button" className="btn tiny" onClick={() => onGoVerify(f.id)}>
                    Xác minh
                  </button>
                  <button
                    type="button"
                    className="btn tiny danger"
                    disabled={busy}
                    onClick={() => deleteOne(f)}
                    data-testid={`doc-delete-${f.id}`}
                  >
                    Xóa
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
