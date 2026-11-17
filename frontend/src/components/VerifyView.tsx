import React, { useEffect, useState } from "react";
import { apiJson, type SignatureVerifyItem } from "../lib/api";

interface Props {
  initialFileId?: string | null;
}

export function VerifyView({ initialFileId }: Props) {
  const [sigs, setSigs] = useState<SignatureVerifyItem[] | null>(null);
  const [allOk, setAllOk] = useState<boolean>(false);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!initialFileId) return;
    setBusy(true);
    setErr("");
    apiJson<{ signatures: SignatureVerifyItem[]; all_ok: boolean }>(`/api/files/${initialFileId}/verify`, {
      method: "POST",
    })
      .then((res) => {
        setSigs(res.signatures);
        setAllOk(res.all_ok);
      })
      .catch((ex) => setErr(ex instanceof Error ? ex.message : "Xác minh thất bại"))
      .finally(() => setBusy(false));
  }, [initialFileId]);

  async function handleVerifyFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setBusy(true);
    setErr("");
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await apiJson<{ signatures: SignatureVerifyItem[]; all_ok: boolean }>("/api/verify", {
        method: "POST",
        body: fd,
      });
      setSigs(res.signatures);
      setAllOk(res.all_ok);
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Xác minh thất bại");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card stack">
      <h2>Xác minh chữ ký số PDF</h2>
      <p className="muted">Tải lên file PDF đã ký để kiểm tra tính toàn vẹn, chuỗi chứng thư, CRL và dấu thời gian.</p>
      <div>
        <label className="btn primary file-btn">
          <span>{busy ? "Đang xác minh..." : "Chọn PDF để xác minh"}</span>
          <input type="file" accept=".pdf" onChange={handleVerifyFile} disabled={busy} data-testid="verify-upload" />
        </label>
      </div>
      {err && <div className="alert error">{err}</div>}
      {sigs !== null && (
        <div className="stack" data-testid="verify-result">
          <div className={`alert ${allOk ? "ok" : "error"}`}>
            {sigs.length === 0
              ? "Tài liệu chưa có chữ ký số"
              : allOk
                ? `Hợp lệ (${sigs.length} chữ ký đều nguyên vẹn, tin cậy và chưa thu hồi)`
                : "Không hợp lệ hoặc tài liệu đã bị thay đổi"}
          </div>
          {sigs.map((s) => (
            <div key={s.field} className="sig-result-card">
              <div className="row">
                <strong>{s.field}</strong>
                <span className={`badge ${s.ok ? "ok" : "bad"}`}>{s.ok ? "HỢP LỆ" : "KHÔNG HỢP LỆ"}</span>
              </div>
              <div>Người ký: {s.signer || "-"}</div>
              <div>Nguyên vẹn: {s.intact ? "Có" : "Không"} · Tin cậy: {s.trusted ? "Có" : "Không"} · Thu hồi: {s.revoked ? "Đã thu hồi" : "Không"}</div>
              {s.signed_at && <div>Thời gian TSA: {s.signed_at}</div>}
              {s.reason && <div>Nhận xét: {s.reason}</div>}
              {s.error && <div className="alert error">{s.error}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
