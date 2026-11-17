import React, { useEffect, useState } from "react";
import {
  apiBytes,
  apiJson,
  type CertificateItem,
  type PresetItem,
  type StoredFile,
} from "../lib/api";
import type { PdfBox } from "../lib/coords";
import { PdfPreviewBox } from "./PdfPreviewBox";

type ShowField = "name" | "title" | "date" | "location" | "reason";
const ALL_SHOW: { key: ShowField; label: string }[] = [
  { key: "name", label: "Tên người ký" },
  { key: "title", label: "Chức danh" },
  { key: "date", label: "Ngày ký" },
  { key: "location", label: "Nơi ký" },
  { key: "reason", label: "Nhận xét" },
];

interface Props {
  onGoVerify: (fileId: string) => void;
}

export function SignWorkspace({ onGoVerify }: Props) {
  const [files, setFiles] = useState<StoredFile[]>([]);
  const [selectedFile, setSelectedFile] = useState<StoredFile | null>(null);
  const [pdfBytes, setPdfBytes] = useState<Uint8Array | null>(null);
  const [page, setPage] = useState(1);
  const [box, setBox] = useState<PdfBox>([40, 40, 280, 120]);

  const [certs, setCerts] = useState<CertificateItem[]>([]);
  const [certId, setCertId] = useState<number | "">("");
  const [mode, setMode] = useState<"text" | "image" | "both">("text");
  const [imageId, setImageId] = useState<string | null>(null);
  const [imagePreviewUrl, setImagePreviewUrl] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [title, setTitle] = useState("");
  const [location, setLocation] = useState("");
  const [show, setShow] = useState<ShowField[]>(["name", "date", "reason"]);
  const [level, setLevel] = useState<"B-B" | "B-T" | "B-LT">("B-T");

  const [presets, setPresets] = useState<PresetItem[]>([]);
  const [presetName, setPresetName] = useState("");

  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [signedFile, setSignedFile] = useState<StoredFile | null>(null);

  async function refreshLists() {
    const [fList, cList, pList] = await Promise.all([
      apiJson<StoredFile[]>("/api/files"),
      apiJson<CertificateItem[]>("/api/certificates"),
      apiJson<PresetItem[]>("/api/presets"),
    ]);
    setFiles(fList);
    setCerts(cList);
    setPresets(pList);
    if (cList.length > 0 && certId === "") {
      setCertId(cList[0].id);
    }
  }

  useEffect(() => {
    refreshLists().catch((ex) => setErr(ex instanceof Error ? ex.message : "Không tải được dữ liệu"));
    return () => {
      if (imagePreviewUrl) URL.revokeObjectURL(imagePreviewUrl);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function selectAndLoadFile(f: StoredFile) {
    setErr("");
    setMsg("");
    setSelectedFile(f);
    setPage(1);
    const bytes = await apiBytes(`/api/files/${f.id}/download`);
    setPdfBytes(bytes);
  }

  async function handleUploadDoc(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setErr("");
    setMsg("");
    setSignedFile(null);
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const uploaded = await apiJson<StoredFile>("/api/files", { method: "POST", body: fd });
      await refreshLists();
      await selectAndLoadFile(uploaded);
      setMsg(`Đã tải lên và sẵn sàng ký: ${uploaded.name}`);
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Tải file thất bại");
    } finally {
      setBusy(false);
    }
  }

  async function handleUploadSigImage(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setErr("");
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await apiJson<{ id: string }>("/api/signature-images", { method: "POST", body: fd });
      if (imagePreviewUrl) URL.revokeObjectURL(imagePreviewUrl);
      setImagePreviewUrl(URL.createObjectURL(file));
      setImageId(res.id);
      if (mode === "text") setMode("both");
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Tải ảnh chữ ký thất bại");
    }
  }

  function toggleShow(key: ShowField) {
    setShow((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]));
  }

  async function handleSavePreset() {
    if (!presetName.trim()) return;
    setErr("");
    try {
      const p = await apiJson<PresetItem>("/api/presets", {
        method: "POST",
        body: JSON.stringify({
          name: presetName.trim(),
          config: {
            mode,
            show,
            reason,
            location,
            title,
            level,
            cert_id: certId === "" ? undefined : certId,
          },
        }),
      });
      setPresets((prev) => [...prev, p]);
      setPresetName("");
      setMsg(`Đã lưu preset "${p.name}"`);
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Không lưu được preset");
    }
  }

  function applyPreset(p: PresetItem) {
    const c = p.config;
    if (c.mode) setMode(c.mode);
    if (c.show) setShow(c.show);
    if (c.reason !== undefined) setReason(c.reason);
    if (c.location !== undefined) setLocation(c.location);
    if (c.title !== undefined) setTitle(c.title);
    if (c.level) setLevel(c.level);
    if (c.cert_id && certs.some((x) => x.id === c.cert_id)) setCertId(c.cert_id);
  }

  async function handleDeletePreset(pid: number) {
    await apiJson(`/api/presets/${pid}`, { method: "DELETE" });
    setPresets((prev) => prev.filter((p) => p.id !== pid));
  }

  async function handleSign() {
    if (!selectedFile || certId === "") return;
    setErr("");
    setMsg("");
    setBusy(true);
    try {
      const res = await apiJson<{ job_id: number; output_file: StoredFile }>("/api/sign", {
        method: "POST",
        body: JSON.stringify({
          file_id: selectedFile.id,
          cert_id: Number(certId),
          mode,
          image_id: mode === "text" ? null : imageId,
          page,
          box,
          reason: reason || null,
          location: location || null,
          title: title || null,
          show,
          level,
        }),
      });
      setSignedFile(res.output_file);
      await refreshLists();
      await selectAndLoadFile(res.output_file);
      setMsg("Ký số thành công! Có thể tải về hoặc ký nối tiếp thêm chữ ký.");
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Ký số thất bại");
    } finally {
      setBusy(false);
    }
  }

  const activeCert = certs.find((c) => c.id === certId);

  return (
    <div className="workspace-grid">
      <div className="sidebar-col stack">
        <div className="card stack">
          <h3>1. Tài liệu</h3>
          <label className="btn primary file-btn">
            <span>{busy ? "Đang xử lý..." : "Tải lên Word / PDF"}</span>
            <input
              type="file"
              accept=".pdf,.docx,.doc,.xlsx,.pptx"
              onChange={handleUploadDoc}
              disabled={busy}
              data-testid="upload-doc-input"
            />
          </label>
          {files.length > 0 && (
            <div className="file-list">
              {files.map((f) => (
                <div
                  key={f.id}
                  className={`file-item ${selectedFile?.id === f.id ? "active" : ""}`}
                >
                  <button
                    type="button"
                    className="file-name-btn"
                    onClick={() => selectAndLoadFile(f)}
                  >
                    <span>{f.name}</span>
                    <small className="muted">
                      {f.kind === "signed" ? "Đã ký" : "Gốc"} · {f.pages} trang
                    </small>
                  </button>
                  <a
                    href={`/api/files/${f.id}/download`}
                    className="btn tiny"
                    download={f.name}
                    title="Tải về"
                  >
                    Tải
                  </a>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="card stack">
          <h3>2. Chứng thư & Hiển thị</h3>
          <label className="field">
            <span>Chứng thư ký</span>
            <select
              value={certId}
              onChange={(e) => setCertId(e.target.value ? Number(e.target.value) : "")}
              data-testid="cert-select"
            >
              {certs.length === 0 && <option value="">Chưa được gán chứng thư</option>}
              {certs.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.subject} ({c.serial.slice(0, 8)})
                </option>
              ))}
            </select>
          </label>

          <div className="field">
            <span>Chế độ hiển thị</span>
            <div className="seg-group">
              {(["text", "image", "both"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  data-testid={`mode-${m}`}
                  className={`btn small ${mode === m ? "primary" : ""}`}
                  onClick={() => setMode(m)}
                >
                  {m === "text" ? "Chỉ chữ" : m === "image" ? "Chỉ ảnh" : "Ảnh + Chữ"}
                </button>
              ))}
            </div>
          </div>

          {(mode === "image" || mode === "both") && (
            <div className="field">
              <span>Ảnh chữ ký (PNG/JPG)</span>
              <label className="btn small file-btn">
                <span>{imageId ? "Đổi ảnh chữ ký" : "Chọn ảnh chữ ký"}</span>
                <input
                  type="file"
                  accept="image/png,image/jpeg"
                  onChange={handleUploadSigImage}
                  data-testid="upload-image-input"
                />
              </label>
            </div>
          )}

          <label className="field">
            <span>Nhận xét (Reason — ký bảo vệ)</span>
            <input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="VD: Đã duyệt nội dung"
              data-testid="reason-input"
            />
          </label>

          <div className="two-col">
            <label className="field">
              <span>Chức danh</span>
              <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Trưởng phòng" />
            </label>
            <label className="field">
              <span>Nơi ký</span>
              <input value={location} onChange={(e) => setLocation(e.target.value)} placeholder="Hà Nội" />
            </label>
          </div>

          <div className="field">
            <span>Trường hiển thị</span>
            <div className="check-grid">
              {ALL_SHOW.map((item) => (
                <label key={item.key} className="check-item">
                  <input
                    type="checkbox"
                    checked={show.includes(item.key)}
                    onChange={() => toggleShow(item.key)}
                  />
                  <span>{item.label}</span>
                </label>
              ))}
            </div>
          </div>

          <label className="field">
            <span>Chuẩn PAdES</span>
            <select value={level} onChange={(e) => setLevel(e.target.value as "B-B" | "B-T" | "B-LT")}>
              <option value="B-T">B-T (có dấu thời gian TSA)</option>
              <option value="B-B">B-B (cơ bản)</option>
              <option value="B-LT">B-LT (nhúng thông tin thu hồi)</option>
            </select>
          </label>
        </div>

        <div className="card stack">
          <h3>3. Preset chữ ký</h3>
          <div className="row">
            <input
              value={presetName}
              onChange={(e) => setPresetName(e.target.value)}
              placeholder="Tên preset mới"
              data-testid="preset-name-input"
            />
            <button type="button" className="btn small" onClick={handleSavePreset} data-testid="save-preset-btn">
              Lưu
            </button>
          </div>
          {presets.map((p) => (
            <div key={p.id} className="file-item">
              <button type="button" className="file-name-btn" onClick={() => applyPreset(p)}>
                <span>{p.name}</span>
              </button>
              <button type="button" className="btn tiny" onClick={() => handleDeletePreset(p.id)}>
                Xóa
              </button>
            </div>
          ))}
        </div>

        <div className="card stack">
          {err && <div className="alert error" role="alert">{err}</div>}
          {msg && <div className="alert ok" role="status">{msg}</div>}
          <button
            type="button"
            className="btn primary"
            disabled={busy || !selectedFile || certId === "" || (mode !== "text" && !imageId)}
            onClick={handleSign}
            data-testid="sign-btn"
          >
            {busy ? "Đang ký..." : "Ký tài liệu"}
          </button>
          {signedFile && (
            <div className="row">
              <a
                href={`/api/files/${signedFile.id}/download`}
                className="btn primary"
                download={signedFile.name}
                data-testid="download-signed-link"
              >
                Tải về PDF đã ký
              </a>
              <button
                type="button"
                className="btn"
                onClick={() => onGoVerify(signedFile.id)}
                data-testid="go-verify-btn"
              >
                Xác minh ngay
              </button>
            </div>
          )}
        </div>
      </div>

      <div className="preview-col">
        <PdfPreviewBox
          pdfBytes={pdfBytes}
          page={page}
          onPageChange={setPage}
          box={box}
          onBoxChange={setBox}
          mode={mode}
          reason={reason}
          signerName={activeCert?.subject || "Người ký"}
          imagePreviewUrl={imagePreviewUrl}
        />
      </div>
    </div>
  );
}
