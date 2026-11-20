import React, { useState } from "react";
import type { WfFailureReason } from "../../lib/wfApi";

interface Props {
  reasons: WfFailureReason[];
  busy?: boolean;
  submitLabel?: string;
  requireNote?: boolean;
  noteLabel?: string;
  onCancel?: () => void;
  onSubmit: (payload: { failure_reason_id: number; note: string }) => Promise<void> | void;
}

export function FailTaskForm({
  reasons,
  busy = false,
  submitLabel = "Xác nhận thất bại",
  requireNote = false,
  noteLabel = "Ghi chú chi tiết",
  onCancel,
  onSubmit,
}: Props) {
  const [reasonId, setReasonId] = useState<string>(reasons[0] ? String(reasons[0].id) : "");
  const [note, setNote] = useState("");
  const [localErr, setLocalErr] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLocalErr("");
    const parsedId = Number(reasonId);
    if (!parsedId) {
      setLocalErr("Vui lòng chọn lý do từ danh sách quy trình.");
      return;
    }
    if (requireNote && !note.trim()) {
      setLocalErr("Vui lòng nhập nội dung lý do.");
      return;
    }
    await onSubmit({ failure_reason_id: parsedId, note: note.trim() });
  }

  return (
    <form className="stack" onSubmit={handleSubmit} data-testid="fail-task-form">
      {reasons.length === 0 ? (
        <div className="alert error">
          Quy trình chưa cấu hình danh sách lý do thất bại nên không thể báo thất bại tại bước này.
        </div>
      ) : (
        <>
          <label className="field">
            <span>Lý do thất bại *</span>
            <select
              value={reasonId}
              onChange={(e) => setReasonId(e.target.value)}
              disabled={busy}
              data-testid="fail-reason-select"
            >
              <option value="">-- Chọn lý do --</option>
              {reasons.map((r) => (
                <option key={r.id} value={String(r.id)}>
                  {r.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>{noteLabel}{requireNote ? " *" : ""}</span>
            <textarea
              rows={2}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Nhập ghi chú thêm..."
              disabled={busy}
              data-testid="fail-note-input"
            />
          </label>
          {localErr && <div className="alert error">{localErr}</div>}
          <div className="row" style={{ justifyContent: "flex-end" }}>
            {onCancel && (
              <button type="button" className="btn small" disabled={busy} onClick={onCancel}>
                Hủy
              </button>
            )}
            <button
              type="submit"
              className="btn small danger"
              disabled={busy || !reasonId}
              data-testid="fail-submit-btn"
            >
              {busy ? "Đang gửi..." : submitLabel}
            </button>
          </div>
        </>
      )}
    </form>
  );
}
