import React, { useEffect, useState } from "react";
import {
  ApiError,
  apiBytes,
  apiJson,
  type AssignableUser,
  type CertificateItem,
  type MeResponse,
  type PresetItem,
  type SignatureVerifyItem,
  type StoredFile,
  type TimelineEvent,
  type WorkflowDocumentDetail,
  type WorkflowDocumentSummary,
  type WorkflowTimeline as WorkflowTimelineData,
} from "../lib/api";
import type { PdfBox } from "../lib/coords";
import { PdfPreviewBox } from "./PdfPreviewBox";
import { PinModal } from "./PinModal";
import { DOC_STATUS_MAP, WorkflowTimeline } from "./WorkflowTimeline";

type ShowField = "name" | "title" | "date" | "location" | "reason";
const ALL_SHOW: { key: ShowField; label: string }[] = [
  { key: "name", label: "Tên người ký" },
  { key: "title", label: "Chức danh" },
  { key: "date", label: "Ngày ký" },
  { key: "location", label: "Nơi ký" },
  { key: "reason", label: "Nhận xét" },
];

interface PickerProps {
  users: AssignableUser[];
  selectedIds: number[];
  onToggle: (uid: number) => void;
  testIdPrefix: string;
}

function CertifiedAssigneePicker({ users, selectedIds, onToggle, testIdPrefix }: PickerProps) {
  const certified = users.filter((u) => u.has_cert);
  if (certified.length === 0) {
    return (
      <div className="alert error">
        Chưa có người dùng nào được gán chứng thư ký. Quản trị viên cần gán chứng thư trước.
      </div>
    );
  }
  return (
    <div className="wf-picker-list">
      {certified.map((u) => (
        <label key={u.id} className="wf-picker-item">
          <input
            type="checkbox"
            checked={selectedIds.includes(u.id)}
            onChange={() => onToggle(u.id)}
            data-testid={`${testIdPrefix}-${u.username}`}
          />
          <span>
            <strong>{u.display_name || u.username}</strong>{" "}
            <small className="muted">(@{u.username})</small>
          </span>
        </label>
      ))}
    </div>
  );
}

interface Props {
  me: MeResponse;
  openDocId: string | null;
  resetSignal?: number;
  onDocOpened: () => void;
  onNotifyRefresh: () => void;
  onGoAccount: () => void;
  onRefreshMe: () => Promise<void> | void;
}

export function WorkflowView({ me, openDocId, resetSignal = 0, onDocOpened, onNotifyRefresh, onGoAccount, onRefreshMe }: Props) {
  const [view, setView] = useState<"list" | "create" | "detail">("list");
  const [scope, setScope] = useState<"pending" | "created" | "participated" | "all">("pending");
  const [statusFilter, setStatusFilter] = useState("");
  const [docs, setDocs] = useState<WorkflowDocumentSummary[]>([]);
  const [assignable, setAssignable] = useState<AssignableUser[]>([]);

  // Tạo tài liệu mới
  const [uploadFiles, setUploadFiles] = useState<StoredFile[]>([]);
  const [selectedFileId, setSelectedFileId] = useState("");
  const [titleInput, setTitleInput] = useState("");
  const [descInput, setDescInput] = useState("");
  const [createAssignees, setCreateAssignees] = useState<number[]>([]);
  const [createMode, setCreateMode] = useState<"all" | "any">("all");

  // Chi tiết tài liệu & ký
  const [detail, setDetail] = useState<WorkflowDocumentDetail | null>(null);
  const [timelineEvents, setTimelineEvents] = useState<TimelineEvent[]>([]);
  const [pdfBytes, setPdfBytes] = useState<Uint8Array | null>(null);
  const [page, setPage] = useState(1);
  const [box, setBox] = useState<PdfBox>([40, 40, 260, 110]);

  const [certs, setCerts] = useState<CertificateItem[]>([]);
  const [presets, setPresets] = useState<PresetItem[]>([]);
  const [certId, setCertId] = useState<number | "">("");
  const [signMode, setSignMode] = useState<"text" | "image" | "both">("text");
  const [imageId, setImageId] = useState<string | null>(null);
  const [imagePreviewUrl, setImagePreviewUrl] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [sigTitle, setSigTitle] = useState("");
  const [location, setLocation] = useState("");
  const [show, setShow] = useState<ShowField[]>(["name", "date", "reason"]);
  const [level, setLevel] = useState<"B-B" | "B-T" | "B-LT">("B-T");

  const [postSignAction, setPostSignAction] = useState<"later" | "assign" | "complete">("later");
  const [postAssignees, setPostAssignees] = useState<number[]>([]);
  const [postMode, setPostMode] = useState<"all" | "any">("all");

  const [nextAssignees, setNextAssignees] = useState<number[]>([]);
  const [nextMode, setNextMode] = useState<"all" | "any">("all");

  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  const [cancelOpen, setCancelOpen] = useState(false);
  const [cancelReason, setCancelReason] = useState("");

  const [verifyRes, setVerifyRes] = useState<{ signatures: SignatureVerifyItem[]; all_ok: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [pinAction, setPinAction] = useState<"sign" | "reject" | "cancel" | null>(null);
  const [pinErr, setPinErr] = useState("");
  const [pinErrKind, setPinErrKind] = useState<string | undefined>(undefined);

  async function loadList(curScope = scope, curStatus = statusFilter) {
    const qs = new URLSearchParams({ scope: curScope });
    if (curStatus) qs.set("status", curStatus);
    const list = await apiJson<WorkflowDocumentSummary[]>(`/api/documents?${qs.toString()}`);
    setDocs(list);
  }

  useEffect(() => {
    loadList(scope, statusFilter).catch((ex) =>
      setErr(ex instanceof Error ? ex.message : "Không tải được danh sách luồng ký"),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope, statusFilter]);

  useEffect(() => {
    return () => {
      if (imagePreviewUrl) URL.revokeObjectURL(imagePreviewUrl);
    };
  }, [imagePreviewUrl]);

  useEffect(() => {
    if (openDocId) {
      openDetail(openDocId, true)
        .catch((ex) => setErr(ex instanceof Error ? ex.message : "Không mở được tài liệu"))
        .finally(onDocOpened);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openDocId]);

  useEffect(() => {
    if (resetSignal > 0 && !openDocId) {
      setView("list");
      setErr("");
      setMsg("");
      loadList(scope, statusFilter).catch((ex) =>
        setErr(ex instanceof Error ? ex.message : "Không tải được danh sách luồng ký"),
      );
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetSignal]);

  function toggleId(setter: React.Dispatch<React.SetStateAction<number[]>>, uid: number) {
    setter((prev) => (prev.includes(uid) ? prev.filter((x) => x !== uid) : [...prev, uid]));
  }

  async function openCreateForm() {
    setErr("");
    setMsg("");
    setTitleInput("");
    setDescInput("");
    setCreateAssignees([]);
    setCreateMode("all");
    try {
      const [fList, uList] = await Promise.all([
        apiJson<StoredFile[]>("/api/files"),
        apiJson<AssignableUser[]>("/api/users/assignable"),
      ]);
      setUploadFiles(fList);
      setSelectedFileId(fList[0]?.id || "");
      setAssignable(uList);
      setView("create");
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Không chuẩn bị được form tạo tài liệu");
    }
  }

  async function handleInlineUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setErr("");
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const uploaded = await apiJson<StoredFile>("/api/files", { method: "POST", body: fd });
      setUploadFiles((prev) => [uploaded, ...prev]);
      setSelectedFileId(uploaded.id);
      if (!titleInput.trim()) {
        setTitleInput(uploaded.name.replace(/\.pdf$/i, ""));
      }
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Tải tài liệu lên thất bại");
    } finally {
      setBusy(false);
    }
  }

  async function handleCreateSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!selectedFileId || !titleInput.trim() || createAssignees.length === 0) {
      setErr("Vui lòng chọn file, nhập tiêu đề và chọn ít nhất 1 người ký.");
      return;
    }
    setErr("");
    setMsg("");
    setBusy(true);
    try {
      const created = await apiJson<WorkflowDocumentDetail>("/api/documents", {
        method: "POST",
        body: JSON.stringify({
          file_id: selectedFileId,
          title: titleInput.trim(),
          description: descInput.trim(),
          assignee_ids: createAssignees,
          mode: createMode,
        }),
      });
      await openDetail(created.id, false);
      onNotifyRefresh();
      setMsg("Đã tạo luồng ký và gửi thông báo cho người ký");
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Tạo luồng ký thất bại");
    } finally {
      setBusy(false);
    }
  }

  async function openDetail(docId: string, track: boolean) {
    setErr("");
    setVerifyRes(null);
    setRejectOpen(false);
    setRejectReason("");
    setCancelOpen(false);
    setCancelReason("");
    setPostSignAction("later");
    setPostAssignees([]);
    setNextAssignees([]);
    const [det, tl, bytes, cList, pList, uList] = await Promise.all([
      apiJson<WorkflowDocumentDetail>(`/api/documents/${docId}?track=${track ? "1" : "0"}`),
      apiJson<WorkflowTimelineData>(`/api/documents/${docId}/timeline`),
      apiBytes(`/api/documents/${docId}/file`).catch(() => null),
      apiJson<CertificateItem[]>("/api/certificates"),
      apiJson<PresetItem[]>("/api/presets"),
      apiJson<AssignableUser[]>("/api/users/assignable"),
    ]);
    setDetail(det);
    setTimelineEvents(tl.events);
    setPdfBytes(bytes);
    setPage(1);
    setCerts(cList);
    setPresets(pList);
    setAssignable(uList);
    if (cList.length > 0 && (certId === "" || !cList.some((c) => c.id === certId))) {
      setCertId(cList[0].id);
    }
    setView("detail");
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

  function openPinPrompt(action: "sign" | "reject" | "cancel") {
    setErr("");
    setMsg("");
    setPinErr(me.pin_locked ? "Chức năng ký đang tạm khóa do nhập sai PIN quá nhiều lần." : "");
    setPinErrKind(me.has_signing_pin ? (me.pin_locked ? "pin_locked" : undefined) : "pin_not_set");
    setPinAction(action);
  }

  function handleSign() {
    if (!detail || certId === "") return;
    if (postSignAction === "assign" && postAssignees.length === 0) {
      setErr("Vui lòng chọn người ký tiếp theo hoặc chọn ký trước");
      return;
    }
    openPinPrompt("sign");
  }

  function handleReject() {
    if (!detail) return;
    if (!rejectReason.trim()) {
      setErr("Bắt buộc phải nhập lý do từ chối");
      return;
    }
    openPinPrompt("reject");
  }

  function handleCancel() {
    if (!detail) return;
    openPinPrompt("cancel");
  }

  async function handlePinConfirm(pin: string) {
    if (!detail || !pinAction) return;
    setErr("");
    setMsg("");
    setPinErr("");
    setPinErrKind(undefined);
    setBusy(true);
    try {
      if (pinAction === "sign") {
        const payload: Record<string, unknown> = {
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
        };
        if (detail.can_decide_next_on_sign && postSignAction !== "later") {
          payload.next_action = postSignAction;
          if (postSignAction === "assign") {
            payload.next_assignee_ids = postAssignees;
            payload.next_mode = postMode;
          }
        }
        await apiJson(`/api/documents/${detail.id}/sign`, {
          method: "POST",
          body: JSON.stringify(payload),
        });
        setPinAction(null);
        await openDetail(detail.id, false);
        await loadList(scope, statusFilter);
        onNotifyRefresh();
        setMsg("Đã ký tài liệu thành công");
      } else if (pinAction === "reject") {
        await apiJson(`/api/documents/${detail.id}/reject`, {
          method: "POST",
          body: JSON.stringify({ reason: rejectReason.trim(), pin }),
        });
        setPinAction(null);
        await openDetail(detail.id, false);
        await loadList(scope, statusFilter);
        onNotifyRefresh();
        setMsg("Đã từ chối ký và dừng luồng");
      } else if (pinAction === "cancel") {
        await apiJson(`/api/documents/${detail.id}/cancel`, {
          method: "POST",
          body: JSON.stringify({ reason: cancelReason.trim(), pin }),
        });
        setPinAction(null);
        await openDetail(detail.id, false);
        await loadList(scope, statusFilter);
        onNotifyRefresh();
        setMsg("Đã hủy luồng ký");
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

  async function handleAssignNext() {
    if (!detail || nextAssignees.length === 0) {
      setErr("Vui lòng chọn ít nhất 1 người ký tiếp theo");
      return;
    }
    setErr("");
    setMsg("");
    setBusy(true);
    try {
      await apiJson(`/api/documents/${detail.id}/assign`, {
        method: "POST",
        body: JSON.stringify({ assignee_ids: nextAssignees, mode: nextMode }),
      });
      await openDetail(detail.id, false);
      await loadList(scope, statusFilter);
      onNotifyRefresh();
      setMsg("Đã chuyển tiếp bước ký mới");
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Assign tiếp thất bại");
    } finally {
      setBusy(false);
    }
  }

  async function handleComplete() {
    if (!detail) return;
    setErr("");
    setMsg("");
    setBusy(true);
    try {
      await apiJson(`/api/documents/${detail.id}/complete`, { method: "POST" });
      await openDetail(detail.id, false);
      await loadList(scope, statusFilter);
      onNotifyRefresh();
      setMsg("Tài liệu đã hoàn tất luồng ký");
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Hoàn tất thất bại");
    } finally {
      setBusy(false);
    }
  }

  // handleReject & handleCancel đã gom vào handlePinConfirm ở trên

  async function handleVerify() {
    if (!detail) return;
    setErr("");
    setBusy(true);
    try {
      const res = await apiJson<{ signatures: SignatureVerifyItem[]; all_ok: boolean }>(
        `/api/documents/${detail.id}/verify`,
        { method: "POST" },
      );
      setVerifyRes(res);
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Xác minh chữ ký thất bại");
    } finally {
      setBusy(false);
    }
  }

  const activeCert = certs.find((c) => c.id === certId);

  if (view === "create") {
    return (
      <div className="card stack" style={{ maxWidth: 720, margin: "0 auto" }}>
        <div className="preview-toolbar">
          <h2>Tạo luồng ký mới</h2>
          <button type="button" className="btn small" onClick={() => setView("list")}>
            Quay lại
          </button>
        </div>
        {err && <div className="alert error" role="alert">{err}</div>}
        <form onSubmit={handleCreateSubmit} className="stack" data-testid="wf-create-form">
          <label className="field">
            <span>Tiêu đề tài liệu</span>
            <input
              value={titleInput}
              onChange={(e) => setTitleInput(e.target.value)}
              placeholder="VD: Hợp đồng dịch vụ tháng 11"
              required
              data-testid="wf-title-input"
            />
          </label>
          <label className="field">
            <span>Ghi chú / Mô tả</span>
            <input
              value={descInput}
              onChange={(e) => setDescInput(e.target.value)}
              placeholder="Tùy chọn"
              data-testid="wf-desc-input"
            />
          </label>
          <div className="field">
            <span>Chọn tài liệu (đã tải lên hoặc tải mới Word / PDF)</span>
            <div className="row wrap">
              <select
                value={selectedFileId}
                onChange={(e) => setSelectedFileId(e.target.value)}
                style={{ flex: 1 }}
                data-testid="wf-file-select"
              >
                {uploadFiles.length === 0 && <option value="">Chưa có file — hãy tải lên bên cạnh</option>}
                {uploadFiles.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.name} ({f.pages} trang)
                  </option>
                ))}
              </select>
              <label className="btn small file-btn">
                <span>{busy ? "Đang tải..." : "Tải lên Word / PDF"}</span>
                <input
                  type="file"
                  accept=".pdf,.docx,.doc,.xlsx,.pptx"
                  onChange={handleInlineUpload}
                  disabled={busy}
                  data-testid="wf-upload-input"
                />
              </label>
            </div>
          </div>

          <div className="field">
            <span>Chọn người ký (chỉ hiển thị tài khoản đã có chứng thư)</span>
            <CertifiedAssigneePicker
              users={assignable}
              selectedIds={createAssignees}
              onToggle={(uid) => toggleId(setCreateAssignees, uid)}
              testIdPrefix="wf-assignee-check"
            />
          </div>

          <label className="field">
            <span>Chế độ bước ký (khi chọn từ 2 người trở lên)</span>
            <select
              value={createMode}
              onChange={(e) => setCreateMode(e.target.value as "all" | "any")}
              data-testid="wf-mode-select"
            >
              <option value="all">Tất cả phải ký (ALL)</option>
              <option value="any">Chỉ cần 1 người ký (ANY)</option>
            </select>
          </label>

          <button
            type="submit"
            className="btn primary"
            disabled={busy || !selectedFileId || createAssignees.length === 0}
            data-testid="wf-submit-create"
          >
            {busy ? "Đang tạo..." : "Khởi tạo luồng ký"}
          </button>
        </form>
      </div>
    );
  }

  if (view === "detail" && detail) {
    const stBadge = DOC_STATUS_MAP[detail.status] || { label: detail.status, cls: "" };
    return (
      <div className="stack">
        <div className="card preview-toolbar">
          <div className="row wrap">
            <button
              type="button"
              className="btn small"
              onClick={() => {
                setView("list");
                loadList(scope, statusFilter).catch(() => {});
              }}
              data-testid="wf-back-btn"
            >
              ← Danh sách
            </button>
            <strong style={{ fontSize: 16 }}>{detail.title}</strong>
            <span className={`badge ${stBadge.cls}`} data-testid="wf-overall-status">
              {stBadge.label}
            </span>
          </div>
          <div className="row wrap">
            <a
              href={`/api/documents/${detail.id}/file`}
              download
              className="btn small"
              data-testid="wf-download-latest"
            >
              Tải bản mới nhất
            </a>
            <button
              type="button"
              className="btn small"
              disabled={busy}
              onClick={handleVerify}
              data-testid="wf-verify-btn"
            >
              Xác minh chữ ký
            </button>
            {detail.can_cancel && (
              <button
                type="button"
                className="btn small danger"
                onClick={() => setCancelOpen((v) => !v)}
                data-testid="wf-cancel-btn"
              >
                Hủy luồng
              </button>
            )}
          </div>
        </div>

        {detail.description && <div className="card muted">{detail.description}</div>}
        {err && <div className="alert error" role="alert">{err}</div>}
        {msg && <div className="alert ok" role="status">{msg}</div>}

        {cancelOpen && detail.can_cancel && (
          <div className="card stack">
            <strong>Xác nhận hủy luồng ký</strong>
            <div className="row wrap">
              <input
                value={cancelReason}
                onChange={(e) => setCancelReason(e.target.value)}
                placeholder="Lý do hủy (tùy chọn)"
                style={{ flex: 1 }}
                data-testid="wf-cancel-reason"
              />
              <button
                type="button"
                className="btn small danger"
                disabled={busy}
                onClick={handleCancel}
                data-testid="wf-cancel-confirm"
              >
                Xác nhận hủy
              </button>
            </div>
          </div>
        )}

        {verifyRes && (
          <div className="card stack" data-testid="wf-verify-results">
            <div className={`alert ${verifyRes.all_ok ? "ok" : "error"}`}>
              {verifyRes.signatures.length === 0
                ? "Tài liệu chưa có chữ ký số"
                : verifyRes.all_ok
                  ? `Hợp lệ (${verifyRes.signatures.length} chữ ký nguyên vẹn, tin cậy và chưa thu hồi)`
                  : "Có chữ ký không hợp lệ hoặc tài liệu đã bị thay đổi"}
            </div>
            {verifyRes.signatures.map((s) => (
              <div key={s.field} className="sig-result-card">
                <div className="row">
                  <strong>{s.field}</strong>
                  <span className={`badge ${s.ok ? "ok" : "bad"}`}>{s.ok ? "HỢP LỆ" : "KHÔNG HỢP LỆ"}</span>
                </div>
                <div>Người ký: {s.signer || "-"}</div>
                <div>Nguyên vẹn: {s.intact ? "Có" : "Không"} · Tin cậy: {s.trusted ? "Có" : "Không"} · Thu hồi: {s.revoked ? "Đã thu hồi" : "Không"}</div>
                {s.signed_at && <div>Thời gian TSA: {s.signed_at}</div>}
                {s.reason && <div>Nhận xét: {s.reason}</div>}
              </div>
            ))}
          </div>
        )}

        <div className="workspace-grid">
          <div className="sidebar-col stack">
            {detail.can_sign && (
              <div className="card stack" data-testid="wf-sign-panel">
                <h3>Ký tài liệu (Bước {detail.current_step_order})</h3>
                <label className="field">
                  <span>Chứng thư ký</span>
                  <select
                    value={certId}
                    onChange={(e) => setCertId(e.target.value ? Number(e.target.value) : "")}
                    data-testid="wf-cert-select"
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
                  <span>Kiểu hiển thị</span>
                  <div className="seg-group">
                    {(["text", "image", "both"] as const).map((m) => (
                      <button
                        key={m}
                        type="button"
                        data-testid={`wf-mode-${m}`}
                        className={`btn small ${signMode === m ? "primary" : ""}`}
                        onClick={() => setSignMode(m)}
                      >
                        {m === "text" ? "Chỉ chữ" : m === "image" ? "Chỉ ảnh" : "Ảnh + Chữ"}
                      </button>
                    ))}
                  </div>
                </div>

                {(signMode === "image" || signMode === "both") && (
                  <div className="field">
                    <span>Ảnh chữ ký (PNG/JPG)</span>
                    <label className="btn small file-btn">
                      <span>{imageId ? "Đổi ảnh chữ ký" : "Chọn ảnh chữ ký"}</span>
                      <input
                        type="file"
                        accept="image/png,image/jpeg"
                        onChange={handleSigImageUpload}
                        data-testid="wf-sig-image-input"
                      />
                    </label>
                  </div>
                )}

                <label className="field">
                  <span>Nhận xét / Lý do ký</span>
                  <input
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    placeholder="VD: Đồng ý ban hành"
                    data-testid="wf-reason-input"
                  />
                </label>

                <div className="two-col">
                  <label className="field">
                    <span>Chức danh</span>
                    <input value={sigTitle} onChange={(e) => setSigTitle(e.target.value)} />
                  </label>
                  <label className="field">
                    <span>Nơi ký</span>
                    <input value={location} onChange={(e) => setLocation(e.target.value)} />
                  </label>
                </div>

                <div className="check-grid">
                  {ALL_SHOW.map((item) => (
                    <label key={item.key} className="check-item">
                      <input
                        type="checkbox"
                        checked={show.includes(item.key)}
                        onChange={() =>
                          setShow((prev) =>
                            prev.includes(item.key) ? prev.filter((k) => k !== item.key) : [...prev, item.key],
                          )
                        }
                      />
                      <span>{item.label}</span>
                    </label>
                  ))}
                </div>

                <label className="field">
                  <span>Chuẩn PAdES</span>
                  <select value={level} onChange={(e) => setLevel(e.target.value as "B-B" | "B-T" | "B-LT")}>
                    <option value="B-T">B-T (có dấu thời gian TSA)</option>
                    <option value="B-B">B-B (cơ bản)</option>
                    <option value="B-LT">B-LT (nhúng thu hồi)</option>
                  </select>
                </label>

                {presets.length > 0 && (
                  <div className="row wrap">
                    <span className="muted">Preset:</span>
                    {presets.map((p) => (
                      <button key={p.id} type="button" className="btn tiny" onClick={() => applyPreset(p)}>
                        {p.name}
                      </button>
                    ))}
                  </div>
                )}

                {detail.can_decide_next_on_sign && (
                  <div className="stack" style={{ borderTop: "1px solid var(--border)", paddingTop: 8 }}>
                    <label className="field">
                      <span>Sau khi ký xong</span>
                      <select
                        value={postSignAction}
                        onChange={(e) => setPostSignAction(e.target.value as "later" | "assign" | "complete")}
                        data-testid="wf-post-sign-action"
                      >
                        <option value="later">Ký trước, chọn chuyển tiếp / hoàn tất sau</option>
                        <option value="assign">Assign tiếp cho người khác</option>
                        <option value="complete">Hoàn tất, không assign thêm</option>
                      </select>
                    </label>
                    {postSignAction === "assign" && (
                      <>
                        <CertifiedAssigneePicker
                          users={assignable}
                          selectedIds={postAssignees}
                          onToggle={(uid) => toggleId(setPostAssignees, uid)}
                          testIdPrefix="wf-post-assignee"
                        />
                        <select
                          value={postMode}
                          onChange={(e) => setPostMode(e.target.value as "all" | "any")}
                          data-testid="wf-post-mode-select"
                        >
                          <option value="all">Tất cả phải ký (ALL)</option>
                          <option value="any">Chỉ cần 1 người ký (ANY)</option>
                        </select>
                      </>
                    )}
                  </div>
                )}

                <button
                  type="button"
                  className="btn primary"
                  disabled={busy || certId === "" || (signMode !== "text" && !imageId)}
                  onClick={handleSign}
                  data-testid="wf-sign-submit"
                >
                  {busy ? "Đang ký..." : "Ký tài liệu"}
                </button>
              </div>
            )}

            {detail.can_reject && (
              <div className="card stack">
                <button
                  type="button"
                  className="btn small danger"
                  onClick={() => setRejectOpen((v) => !v)}
                  data-testid="wf-reject-toggle"
                >
                  Từ chối ký
                </button>
                {rejectOpen && (
                  <div className="stack">
                    <input
                      value={rejectReason}
                      onChange={(e) => setRejectReason(e.target.value)}
                      placeholder="Nhập lý do từ chối (bắt buộc)"
                      data-testid="wf-reject-reason"
                    />
                    <button
                      type="button"
                      className="btn small danger"
                      disabled={busy || !rejectReason.trim()}
                      onClick={handleReject}
                      data-testid="wf-reject-confirm"
                    >
                      Xác nhận từ chối
                    </button>
                  </div>
                )}
              </div>
            )}

            {detail.can_assign_or_complete && (
              <div className="card stack" data-testid="wf-next-step-panel">
                <h3>Chuyển tiếp hoặc Hoàn tất</h3>
                <p className="muted">Bước {detail.current_step_order} đã đủ chữ ký. Chọn bước tiếp theo hoặc hoàn tất luồng.</p>
                <CertifiedAssigneePicker
                  users={assignable}
                  selectedIds={nextAssignees}
                  onToggle={(uid) => toggleId(setNextAssignees, uid)}
                  testIdPrefix="wf-next-assignee"
                />
                <label className="field">
                  <span>Chế độ bước tiếp theo</span>
                  <select
                    value={nextMode}
                    onChange={(e) => setNextMode(e.target.value as "all" | "any")}
                    data-testid="wf-next-mode-select"
                  >
                    <option value="all">Tất cả phải ký (ALL)</option>
                    <option value="any">Chỉ cần 1 người ký (ANY)</option>
                  </select>
                </label>
                <div className="row wrap">
                  <button
                    type="button"
                    className="btn primary small"
                    disabled={busy || nextAssignees.length === 0}
                    onClick={handleAssignNext}
                    data-testid="wf-assign-next-btn"
                  >
                    Assign tiếp
                  </button>
                  <button
                    type="button"
                    className="btn small"
                    disabled={busy}
                    onClick={handleComplete}
                    data-testid="wf-complete-btn"
                  >
                    Hoàn tất tài liệu
                  </button>
                </div>
              </div>
            )}

            <div className="card stack" data-testid="wf-versions-list">
              <h3>Các phiên bản PDF ({detail.versions.length})</h3>
              <div className="file-list">
                {detail.versions.map((v) => (
                  <div key={v.version_no} className="file-item">
                    <div className="file-name-btn" style={{ cursor: "default" }}>
                      <span>
                        <strong>v{v.version_no}</strong> · {v.created_by.display_name || v.created_by.username}
                      </span>
                      <small className="muted">{v.note || (v.version_no === 0 ? "Bản gốc" : "Đã ký")}</small>
                    </div>
                    <a
                      href={`/api/documents/${detail.id}/versions/${v.version_no}/file`}
                      download
                      className="btn tiny"
                      data-testid={`wf-version-dl-${v.version_no}`}
                    >
                      Tải v{v.version_no}
                    </a>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="preview-col">
            <PdfPreviewBox
              interactive={detail.can_sign}
              pdfBytes={pdfBytes}
              page={page}
              onPageChange={setPage}
              box={box}
              onBoxChange={setBox}
              mode={signMode}
              reason={reason}
              signerName={activeCert?.subject || me.display_name || me.username}
              imagePreviewUrl={imagePreviewUrl}
            />
          </div>
        </div>

        <WorkflowTimeline detail={detail} events={timelineEvents} />
        <PinModal
          open={pinAction !== null}
          title={
            pinAction === "reject"
              ? "Xác thực PIN để từ chối ký"
              : pinAction === "cancel"
                ? "Xác thực PIN để hủy luồng ký"
                : "Xác thực PIN để ký tài liệu"
          }
          description={`Tài liệu: ${detail.title}`}
          busy={busy}
          error={pinErr}
          pinErrorKind={pinErrKind}
          hasSigningPin={me.has_signing_pin}
          onClose={() => setPinAction(null)}
          onConfirm={handlePinConfirm}
          onGoSettings={onGoAccount}
        />
      </div>
    );
  }

  return (
    <div className="card stack">
      <div className="preview-toolbar">
        <div className="row wrap">
          <h2 style={{ marginRight: 8 }}>Luồng ký tài liệu</h2>
          {(
            [
              ["pending", "Chờ tôi ký"],
              ["created", "Tôi tạo"],
              ["participated", "Đã tham gia"],
              ["all", "Tất cả"],
            ] as const
          ).map(([k, label]) => (
            <button
              key={k}
              type="button"
              className={`btn small ${scope === k ? "primary" : ""}`}
              onClick={() => setScope(k)}
              data-testid={`wf-scope-${k}`}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="row wrap">
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            style={{ width: "auto" }}
            data-testid="wf-status-filter"
          >
            <option value="">Tất cả trạng thái</option>
            <option value="in_progress">Đang ký</option>
            <option value="completed">Hoàn tất</option>
            <option value="rejected">Bị từ chối</option>
            <option value="cancelled">Đã hủy</option>
          </select>
          <button type="button" className="btn small" onClick={() => loadList(scope, statusFilter)}>
            Làm mới
          </button>
          <button type="button" className="btn primary small" onClick={openCreateForm} data-testid="wf-create-btn">
            + Tạo luồng ký mới
          </button>
        </div>
      </div>

      {err && <div className="alert error" role="alert">{err}</div>}
      {msg && <div className="alert ok" role="status">{msg}</div>}

      {docs.length === 0 ? (
        <div className="empty-preview">Không có tài liệu nào trong danh sách này</div>
      ) : (
        <table className="tbl" data-testid="wf-docs-table">
          <thead>
            <tr>
              <th>Tài liệu</th>
              <th>Người tạo</th>
              <th>Tiến độ</th>
              <th>Trạng thái</th>
              <th>Cập nhật</th>
              <th>Thao tác</th>
            </tr>
          </thead>
          <tbody>
            {docs.map((d) => {
              const stBadge = DOC_STATUS_MAP[d.status] || { label: d.status, cls: "" };
              return (
                <tr key={d.id}>
                  <td>
                    <strong>{d.title}</strong>
                    {d.my_pending && (
                      <div><span className="badge warn">Đến lượt bạn ký</span></div>
                    )}
                    {d.can_assign_or_complete && (
                      <div><span className="badge warn">Chờ chuyển tiếp / hoàn tất</span></div>
                    )}
                  </td>
                  <td>{d.creator.display_name || d.creator.username}</td>
                  <td>Bước {d.current_step_order} / {d.total_steps}</td>
                  <td><span className={`badge ${stBadge.cls}`}>{stBadge.label}</span></td>
                  <td className="mono">{d.updated_at.slice(0, 16).replace("T", " ")}</td>
                  <td>
                    <button
                      type="button"
                      className="btn tiny primary"
                      onClick={() => openDetail(d.id, true)}
                      data-testid={`wf-open-${d.id}`}
                    >
                      Chi tiết
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
