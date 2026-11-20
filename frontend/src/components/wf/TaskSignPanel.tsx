import React, { useEffect, useState } from "react";
import {
  ApiError,
  apiBytes,
  apiJson,
  type CertificateItem,
  type MeResponse,
  type PresetItem,
} from "../../lib/api";
import type { PdfBox } from "../../lib/coords";
import type { WfFailureReason, WfTaskDetail } from "../../lib/wfApi";
import { PdfPreviewBox } from "../PdfPreviewBox";
import { PinModal } from "../PinModal";
import { FailTaskForm } from "./FailTaskForm";

type ShowField = "name" | "title" | "date" | "location" | "reason";
const ALL_SHOW: { key: ShowField; label: string }[] = [
  { key: "name", label: "Tên người ký" },
  { key: "title", label: "Chức danh" },
  { key: "date", label: "Ngày ký" },
  { key: "location", label: "Nơi ký" },
  { key: "reason", label: "Nhận xét" },
];

interface Props {
  me: MeResponse;
  task: WfTaskDetail;
  pdfBytes: Uint8Array | null;
  failureReasons: WfFailureReason[];
  onRefreshMe: () => Promise<void> | void;
  onGoAccount: () => void;
  onUpdated: (msg: string) => Promise<void> | void;
}

export function TaskSignPanel({
  me,
  task,
  pdfBytes,
  failureReasons,
  onRefreshMe,
  onGoAccount,
  onUpdated,
}: Props) {
  const [certs, setCerts] = useState<CertificateItem[]>([]);
  const [presets, setPresets] = useState<PresetItem[]>([]);
  const [certId, setCertId] = useState<number | "">("");
  const [page, setPage] = useState(1);
  const [box, setBox] = useState<PdfBox>([40, 40, 260, 110]);
  const [signMode, setSignMode] = useState<"text" | "image" | "both">("text");
  const [imageId, setImageId] = useState<string | null>(me.signature_image_file_id);
  const [imagePreviewUrl, setImagePreviewUrl] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [sigTitle, setSigTitle] = useState(me.job_title || "");
  const [location, setLocation] = useState("");
  const [show, setShow] = useState<ShowField[]>(["name", "date", "reason"]);
  const [level, setLevel] = useState<"B-B" | "B-T" | "B-LT">("B-T");

  const [rejectOpen, setRejectOpen] = useState(false);
  const [pendingReject, setPendingReject] = useState<{ failure_reason_id: number; note: string } | null>(null);
  const [pinAction, setPinAction] = useState<"sign" | "reject" | null>(null);
  const [pinErr, setPinErr] = useState("");
  const [pinErrKind, setPinErrKind] = useState<string | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    let cancelled = false;
    Promise.all([apiJson<CertificateItem[]>("/api/certificates"), apiJson<PresetItem[]>("/api/presets")])
      .then(([cList, pList]) => {
        if (cancelled) return;
        setCerts(cList);
        setPresets(pList);
        if (cList.length > 0) setCertId(cList[0].id);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [task.id]);

  useEffect(() => {
    if (!me.signature_image_file_id) return;
    let cancelled = false;
    apiBytes(`/api/signature-images/${me.signature_image_file_id}`)
      .then((bytes) => {
        if (cancelled) return;
        const url = URL.createObjectURL(new Blob([new Uint8Array(bytes)], { type: "image/png" }));
        setImageId(me.signature_image_file_id);
        setImagePreviewUrl((prev) => {
          if (prev) URL.revokeObjectURL(prev);
          return url;
        });
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [me.signature_image_file_id]);

  useEffect(() => {
    return () => {
      if (imagePreviewUrl) URL.revokeObjectURL(imagePreviewUrl);
    };
  }, [imagePreviewUrl]);

  function openPin(action: "sign" | "reject") {
    setErr("");
    setPinErr(me.pin_locked ? "Chức năng ký đang tạm khóa do nhập sai PIN quá nhiều lần." : "");
    setPinErrKind(me.has_signing_pin ? (me.pin_locked ? "pin_locked" : undefined) : "pin_not_set");
    setPinAction(action);
  }

  function applyPreset(p: PresetItem) {
    const c = p.config;
    if (c.mode) setSignMode(c.mode);
    if (c.show) setShow(c.show);
    if (c.reason !== undefined) setReason(c.reason);
    if (c.location !== undefined) setLocation(c.location);
    if (c.title !== undefined) setSigTitle(c.title);
    if (c.level) setLevel(c.level);
    if (c.cert_id && certs.some((x) => x.id === c.cert_id)) setCertId(c.cert_id);
  }

  async function handleSigImageUpload(e: React.ChangeEvent<HTMLInputElement>) {
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
      if (signMode === "text") setSignMode("both");
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Tải ảnh chữ ký thất bại");
    }
  }

  async function handlePinConfirm(pin: string) {
    if (!pinAction) return;
    setErr("");
    setPinErr("");
    setPinErrKind(undefined);
    setBusy(true);
    try {
      if (pinAction === "sign") {
        await apiJson(`/api/tasks/${task.id}/sign`, {
          method: "POST",
          body: JSON.stringify({
            cert_id: Number(certId),
            mode: signMode,
            image_id: signMode === "text" ? null : imageId,
            page,
            box,
            reason: reason || null,
            location: location || null,
            title: sigTitle || null,
            show,
            level,
            pin,
          }),
        });
        setPinAction(null);
        await onUpdated("Đã ký số tài liệu thành công");
      } else if (pinAction === "reject" && pendingReject) {
        await apiJson(`/api/tasks/${task.id}/reject`, {
          method: "POST",
          body: JSON.stringify({
            reason: pendingReject.note || "Từ chối ký tài liệu",
            failure_reason_id: pendingReject.failure_reason_id,
            pin,
          }),
        });
        setPinAction(null);
        setRejectOpen(false);
        setPendingReject(null);
        await onUpdated("Đã từ chối ký và dừng nhiệm vụ");
      }
    } catch (ex) {
      if (ex instanceof ApiError && ex.pinError) {
        setPinErr(ex.message);
        setPinErrKind(ex.pinError);
        if (ex.pinError === "pin_locked" || ex.pinError === "pin_not_set") {
          await onRefreshMe();
        }
      } else {
        setPinAction(null);
        setErr(ex instanceof Error ? ex.message : "Thao tác thất bại");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack" data-testid="task-sign-panel">
      <div className="alert ok">
        Bước ký số bắt buộc thực hiện bằng chứng thư nội bộ và mã PIN 6 chữ số (không có nút hoàn thành thủ công).
      </div>

      {err && (
        <div className="alert error" role="alert" data-testid="task-sign-error">
          {err}
        </div>
      )}

      {!task.document_id ? (
        <div className="alert error">
          Nhiệm vụ chưa đính kèm tài liệu PDF để ký.
        </div>
      ) : (
        <div className="wf-sign-split">
          <div className="stack card" style={{ padding: 12 }}>
            <h4 style={{ margin: 0 }}>Thông số chữ ký</h4>
            {presets.length > 0 && (
              <div className="field">
                <span>Mẫu chữ ký</span>
                <div className="row wrap">
                  {presets.map((p) => (
                    <button key={p.id} type="button" className="btn tiny" onClick={() => applyPreset(p)}>
                      {p.name}
                    </button>
                  ))}
                </div>
              </div>
            )}

            <label className="field">
              <span>Chứng thư số</span>
              <select
                value={certId === "" ? "" : String(certId)}
                onChange={(e) => setCertId(e.target.value ? Number(e.target.value) : "")}
                data-testid="wf-task-sign-cert"
              >
                <option value="">-- Chọn chứng thư --</option>
                {certs.map((c) => (
                  <option key={c.id} value={String(c.id)}>
                    {c.subject} (SN: {c.serial})
                  </option>
                ))}
              </select>
            </label>

            {certs.length === 0 && (
              <div className="alert error">Tài khoản chưa được cấp chứng thư số. Liên hệ quản trị viên.</div>
            )}

            <div className="field">
              <span>Kiểu hiển thị</span>
              <div className="seg-group">
                {(["text", "image", "both"] as const).map((m) => (
                  <button
                    key={m}
                    type="button"
                    className={`btn tiny ${signMode === m ? "primary" : ""}`}
                    onClick={() => setSignMode(m)}
                  >
                    {m === "text" ? "Chữ" : m === "image" ? "Ảnh" : "Ảnh + chữ"}
                  </button>
                ))}
              </div>
            </div>

            {(signMode === "image" || signMode === "both") && (
              <label className="btn small file-btn">
                <span>{imageId ? "Đổi ảnh chữ ký" : "Tải ảnh chữ ký PNG/JPG"}</span>
                <input type="file" accept=".png,.jpg,.jpeg" onChange={handleSigImageUpload} />
              </label>
            )}

            <div className="two-col">
              <label className="field">
                <span>Mức chữ ký</span>
                <select
                  value={level}
                  onChange={(e) => setLevel(e.target.value as "B-B" | "B-T" | "B-LT")}
                  data-testid="wf-task-sign-level"
                >
                  <option value="B-B">PAdES B-B</option>
                  <option value="B-T">PAdES B-T (TSA)</option>
                  <option value="B-LT">PAdES B-LT</option>
                </select>
              </label>
              <label className="field">
                <span>Chức danh</span>
                <input value={sigTitle} onChange={(e) => setSigTitle(e.target.value)} />
              </label>
            </div>

            <label className="field">
              <span>Nhận xét / Lý do ký</span>
              <input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Ví dụ: Đã kiểm tra và ký duyệt"
                data-testid="wf-task-sign-reason"
              />
            </label>

            <label className="field">
              <span>Nơi ký</span>
              <input value={location} onChange={(e) => setLocation(e.target.value)} placeholder="Hà Nội" />
            </label>

            <div className="check-grid">
              {ALL_SHOW.map((it) => (
                <label key={it.key} className="check-item">
                  <input
                    type="checkbox"
                    checked={show.includes(it.key)}
                    onChange={() =>
                      setShow((prev) =>
                        prev.includes(it.key) ? prev.filter((x) => x !== it.key) : [...prev, it.key],
                      )
                    }
                  />
                  <span>{it.label}</span>
                </label>
              ))}
            </div>

            <div className="row wrap" style={{ paddingTop: 4 }}>
              <button
                type="button"
                className="btn small primary"
                disabled={busy || certId === "" || !pdfBytes}
                onClick={() => openPin("sign")}
                data-testid="wf-task-sign-btn"
              >
                Ký số tài liệu
              </button>
              <button
                type="button"
                className="btn small danger"
                disabled={busy}
                onClick={() => setRejectOpen((v) => !v)}
                data-testid="wf-task-reject-toggle"
              >
                Từ chối ký
              </button>
            </div>

            {rejectOpen && (
              <div className="card" style={{ background: "#fffafa" }}>
                <FailTaskForm
                  reasons={failureReasons}
                  busy={busy}
                  submitLabel="Tiếp tục nhập PIN từ chối"
                  requireNote
                  noteLabel="Lý do từ chối cụ thể"
                  onCancel={() => setRejectOpen(false)}
                  onSubmit={(payload) => {
                    setPendingReject(payload);
                    openPin("reject");
                  }}
                />
              </div>
            )}
          </div>

          <PdfPreviewBox
            pdfBytes={pdfBytes}
            page={page}
            onPageChange={setPage}
            box={box}
            onBoxChange={setBox}
            mode={signMode}
            reason={reason}
            signerName={me.display_name || me.username}
            imagePreviewUrl={imagePreviewUrl}
            interactive
          />
        </div>
      )}

      <PinModal
        open={pinAction !== null}
        title={pinAction === "reject" ? "Xác thực PIN để từ chối ký" : "Xác thực PIN để ký số"}
        busy={busy}
        error={pinErr}
        pinErrorKind={pinErrKind}
        hasSigningPin={me.has_signing_pin}
        onClose={() => !busy && setPinAction(null)}
        onConfirm={handlePinConfirm}
        onGoSettings={onGoAccount}
      />
    </div>
  );
}
