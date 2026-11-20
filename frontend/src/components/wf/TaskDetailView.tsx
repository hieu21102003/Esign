import React, { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  apiBytes,
  apiJson,
  type AssignableUser,
  type MeResponse,
  type SignatureVerifyItem,
  type StoredFile,
} from "../../lib/api";
import type { PdfBox } from "../../lib/coords";
import { useVisibilityPolling } from "../../lib/usePolling";
import {
  formatDateTime,
  formatShortDate,
  formatSlaCountdown,
  formatTodoResult,
  userDisplayName,
  type WfDetail,
  type WfStage,
  type WfTaskDetail,
  type WfTaskTimeline,
} from "../../lib/wfApi";
import { PdfPreviewBox } from "../PdfPreviewBox";
import { ActivityTimeline } from "./ActivityTimeline";
import { CompleteTodoForm } from "./CompleteTodoForm";
import { EmptyState } from "./EmptyState";
import { FailTaskForm } from "./FailTaskForm";
import { FileUploader } from "./FileUploader";
import { ProgressBar } from "./ProgressBar";
import { SafeHtml } from "./SafeHtml";
import { StatusPill } from "./StatusPill";
import { TaskSignPanel } from "./TaskSignPanel";
import { Topbar } from "./Topbar";
import { UserPicker } from "./UserPicker";

interface Props {
  me: MeResponse;
  taskId: number;
  onOpenWorkflow: (workflowId: number) => void;
  onGoAccount: () => void;
  onRefreshMe: () => Promise<void> | void;
  onNotifyRefresh: () => void;
}

export function TaskDetailView({
  me,
  taskId,
  onOpenWorkflow,
  onGoAccount,
  onRefreshMe,
  onNotifyRefresh,
}: Props) {
  const [task, setTask] = useState<WfTaskDetail | null>(null);
  const [wfDetail, setWfDetail] = useState<WfDetail | null>(null);
  const [timeline, setTimeline] = useState<WfTaskTimeline | null>(null);
  const [users, setUsers] = useState<AssignableUser[]>([]);
  const [pdfBytes, setPdfBytes] = useState<Uint8Array | null>(null);
  const [previewPage, setPreviewPage] = useState(1);
  const [previewBox, setPreviewBox] = useState<PdfBox>([40, 40, 200, 100]);
  const [tab, setTab] = useState<"detail" | "document" | "history">("detail");
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [handedOff, setHandedOff] = useState(false);

  // Sub-panels
  const [normalFailOpen, setNormalFailOpen] = useState(false);
  const [approveNote, setApproveNote] = useState("");
  const [approveRejectOpen, setApproveRejectOpen] = useState(false);
  const [followerAddOpen, setFollowerAddOpen] = useState(false);
  const [newFollowerIds, setNewFollowerIds] = useState<number[]>([]);
  const [attachFiles, setAttachFiles] = useState<StoredFile[]>([]);
  const [verifyRes, setVerifyRes] = useState<{ signatures: SignatureVerifyItem[]; all_ok: boolean } | null>(null);

  const loadAll = useCallback(async (preserveOnHandoff = false) => {
    setErr("");
    try {
      const tDet = await apiJson<WfTaskDetail>(`/api/tasks/${taskId}`);
      setTask(tDet);
      setHandedOff(false);
      const [wfRes, tlRes, uList, pdf] = await Promise.all([
        apiJson<WfDetail>(`/api/workflows/${tDet.workflow.id}`).catch(() => null),
        apiJson<WfTaskTimeline>(`/api/tasks/${taskId}/timeline`).catch(() => null),
        apiJson<AssignableUser[]>("/api/users/assignable").catch(() => []),
        tDet.document_id ? apiBytes(`/api/tasks/${taskId}/document/file`).catch(() => null) : Promise.resolve(null),
      ]);
      setWfDetail(wfRes);
      setTimeline(tlRes);
      setUsers(uList);
      setPdfBytes(pdf);
      setPreviewPage(1);
    } catch (ex) {
      if (preserveOnHandoff && ex instanceof ApiError && ex.status === 404) {
        setHandedOff(true);
      } else {
        setTask(null);
        setErr(ex instanceof Error ? ex.message : "Không tải được chi tiết nhiệm vụ");
      }
    } finally {
      setLoading(false);
    }
  }, [taskId]);

  useEffect(() => {
    setLoading(true);
    setHandedOff(false);
    setVerifyRes(null);
    setNormalFailOpen(false);
    setApproveRejectOpen(false);
    loadAll();
  }, [loadAll]);

  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 30000);
    return () => window.clearInterval(id);
  }, []);

  useVisibilityPolling(
    loadAll,
    25000,
    Boolean(task && task.status === "processing" && !handedOff && !busy && !followerAddOpen),
  );

  async function mutate(fn: () => Promise<WfTaskDetail | unknown>, okMessage: string) {
    setErr("");
    setMsg("");
    setBusy(true);
    try {
      const res = await fn();
      if (res && typeof res === "object" && "stages" in res && "permissions" in res) {
        setTask(res as WfTaskDetail);
      }
      await loadAll(true);
      onNotifyRefresh();
      setMsg(okMessage);
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Thao tác thất bại");
    } finally {
      setBusy(false);
    }
  }

  if (loading && !task) {
    return <div className="card muted">Đang tải chi tiết nhiệm vụ...</div>;
  }

  if (!task) {
    return (
      <EmptyState
        title="Không thể truy cập nhiệm vụ"
        description={err || "Nhiệm vụ không tồn tại hoặc bạn không nằm trong phạm vi hiển thị."}
        actionLabel="Quay lại"
        onAction={() => window.history.back()}
        testId="task-not-accessible"
      />
    );
  }

  const curStage = task.current_stage;
  const curStageIdx = curStage ? task.stages.findIndex((s) => s.id === curStage.id) : -1;
  const nextStage: WfStage | null =
    wfDetail && curStageIdx >= 0 && curStageIdx + 1 < wfDetail.stages.length
      ? wfDetail.stages[curStageIdx + 1]
      : null;

  const doneCount = task.stages.filter((s) => s.state === "done").length;
  const sla = formatSlaCountdown(task.deadline, task.status, nowMs);
  const myTodoId = task.permissions.my_todo_ids[0] ?? null;
  const canActOnTodo = task.status === "processing" && myTodoId !== null && curStage !== null;
  const signStarted = task.stages.some((s) => s.type === "sign" && s.state !== "pending");
  const canAttachDoc =
    task.status === "processing" &&
    !task.archived_at &&
    !signStarted &&
    (task.permissions.can_edit || myTodoId !== null);

  async function handleDownloadPdf() {
    if (!task) return;
    try {
      const bytes = await apiBytes(`/api/tasks/${task.id}/document/file`);
      const blob = new Blob([new Uint8Array(bytes)], { type: "application/pdf" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = task.document?.file_name || `task-${task.id}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Tải PDF thất bại");
    }
  }

  async function handleVerifyDoc() {
    if (!task) return;
    setErr("");
    setBusy(true);
    try {
      const res = await apiJson<{ signatures: SignatureVerifyItem[]; all_ok: boolean }>(
        `/api/tasks/${task.id}/document/verify`,
        { method: "POST" },
      );
      setVerifyRes(res);
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Xác minh thất bại");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack" data-testid="task-detail-view">
      <Topbar
        title={task.title}
        subtitle={`Quy trình: ${task.workflow.name} · Tạo lúc ${formatDateTime(task.created_at)}`}
        backLabel={task.workflow.name}
        onBack={() => onOpenWorkflow(task.workflow.id)}
        viewTabs={[
          { id: "detail", label: "Chi tiết", testId: "task-tab-detail" },
          { id: "document", label: `Tài liệu${task.document ? ` (v${task.document.current_version_no})` : ""}`, testId: "task-tab-document" },
          { id: "history", label: "Lịch sử", testId: "task-tab-history" },
        ]}
        activeViewTab={tab}
        onViewTabChange={(v) => setTab(v as "detail" | "document" | "history")}
        secondaryActions={
          <div className="row wrap">
            {task.permissions.can_archive && (
              <button
                type="button"
                className="btn small"
                disabled={busy}
                onClick={() => mutate(() => apiJson(`/api/tasks/${task.id}/archive`, { method: "POST" }), "Đã lưu trữ nhiệm vụ")}
                data-testid="task-archive-btn"
              >
                Lưu trữ
              </button>
            )}
            {task.permissions.can_restore && (
              <button
                type="button"
                className="btn small primary"
                disabled={busy}
                onClick={() => mutate(() => apiJson(`/api/tasks/${task.id}/restore`, { method: "POST" }), "Đã khôi phục nhiệm vụ")}
                data-testid="task-restore-btn"
              >
                Khôi phục
              </button>
            )}
            {task.permissions.can_edit && task.status === "processing" && (
              <button
                type="button"
                className="btn small danger"
                disabled={busy}
                onClick={() => {
                  const reason = window.prompt("Nhập lý do hủy nhiệm vụ (tùy chọn):");
                  if (reason === null) return;
                  mutate(
                    () =>
                      apiJson(`/api/tasks/${task.id}/cancel`, {
                        method: "POST",
                        body: JSON.stringify({ reason: reason.trim() }),
                      }),
                    "Đã hủy nhiệm vụ",
                  );
                }}
                data-testid="task-cancel-btn"
              >
                Hủy nhiệm vụ
              </button>
            )}
          </div>
        }
      />

      {msg && <div className="alert ok" data-testid="task-detail-msg">{msg}</div>}
      {err && <div className="alert error" role="alert" data-testid="task-detail-err">{err}</div>}

      <div className="card stack">
        <div className="row wrap" style={{ justifyContent: "space-between" }}>
          <div className="row wrap">
            <StatusPill kind="task" status={task.status} overdue={task.is_overdue} testId="task-detail-status" />
            {curStage && (
              <>
                <span className="badge mode-badge" data-testid="task-current-stage">
                  Giai đoạn {curStage.order}: {curStage.name}
                </span>
                <StatusPill kind="stage" status={curStage.type} />
              </>
            )}
            {task.labels.map((lb) => (
              <span key={lb} className="wf-chip small">{lb}</span>
            ))}
          </div>
          <span className={`mono ${sla.overdue ? "text-danger" : "muted"}`} data-testid="task-sla-countdown">
            {sla.text}
          </span>
        </div>

        <ProgressBar
          value={doneCount}
          max={task.stages.length}
          failed={task.status === "failed" ? 1 : 0}
          label={`Tiến độ giai đoạn: ${doneCount}/${task.stages.length} bước hoàn thành`}
        />

        <div className="wf-stage-stepper">
          {task.stages.map((s) => (
            <div key={s.id} className={`wf-step-chip state-${s.state}`}>
              <span>{s.order}. {s.name}</span>
            </div>
          ))}
        </div>

        {task.status === "failed" && task.failure_reason && (
          <div className="alert error" data-testid="task-failure-banner">
            Lý do thất bại: <strong>{task.failure_reason.label}</strong>
          </div>
        )}
      </div>

      {tab === "detail" && (
        <div className="wf-detail-grid">
          <div className="stack">
            {canActOnTodo && curStage && (
              <div className="card stack" data-testid="task-action-card">
                <div className="row wrap" style={{ justifyContent: "space-between" }}>
                  <h3 style={{ margin: 0 }}>Hành động bước hiện tại: {curStage.name}</h3>
                  <StatusPill kind="stage" status={curStage.type} />
                </div>

                {curStage.guide_html && <SafeHtml html={curStage.guide_html} />}

                {curStage.type === "normal" && (
                  <div className="stack">
                    <CompleteTodoForm
                      nextStage={nextStage}
                      busy={busy}
                      onSubmit={(payload) =>
                        mutate(
                          () =>
                            apiJson(`/api/todos/${myTodoId}/complete`, {
                              method: "POST",
                              body: JSON.stringify(payload),
                            }),
                          "Đã hoàn thành bước xử lý",
                        )
                      }
                    />
                    <div style={{ borderTop: "1px solid var(--border)", paddingTop: 8 }}>
                      <button
                        type="button"
                        className="btn tiny danger"
                        onClick={() => setNormalFailOpen((v) => !v)}
                        data-testid="toggle-fail-btn"
                      >
                        {normalFailOpen ? "Đóng báo thất bại" : "Báo thất bại"}
                      </button>
                    </div>
                    {normalFailOpen && (
                      <FailTaskForm
                        reasons={wfDetail?.failure_reasons || []}
                        busy={busy}
                        onCancel={() => setNormalFailOpen(false)}
                        onSubmit={(payload) =>
                          mutate(
                            () =>
                              apiJson(`/api/todos/${myTodoId}/fail`, {
                                method: "POST",
                                body: JSON.stringify(payload),
                              }),
                            "Đã ghi nhận nhiệm vụ thất bại",
                          )
                        }
                      />
                    )}
                  </div>
                )}

                {curStage.type === "approve" && (
                  <div className="stack" data-testid="task-approve-panel">
                    <label className="field">
                      <span>Ý kiến phê duyệt</span>
                      <textarea
                        rows={2}
                        value={approveNote}
                        onChange={(e) => setApproveNote(e.target.value)}
                        placeholder="Nhập nhận xét phê duyệt (tùy chọn)..."
                        data-testid="approve-note-input"
                      />
                    </label>
                    <div className="row wrap">
                      <button
                        type="button"
                        className="btn small primary"
                        disabled={busy}
                        onClick={() =>
                          mutate(
                            () =>
                              apiJson(`/api/todos/${myTodoId}/complete`, {
                                method: "POST",
                                body: JSON.stringify({
                                  decision: "approve",
                                  result: approveNote.trim() || "Đã phê duyệt",
                                }),
                              }),
                            "Đã phê duyệt và chuyển bước tiếp theo",
                          )
                        }
                        data-testid="approve-submit-btn"
                      >
                        Phê duyệt
                      </button>
                      <button
                        type="button"
                        className="btn small danger"
                        disabled={busy}
                        onClick={() => setApproveRejectOpen((v) => !v)}
                        data-testid="approve-reject-toggle"
                      >
                        Từ chối
                      </button>
                    </div>
                    {approveRejectOpen && (
                      <FailTaskForm
                        reasons={wfDetail?.failure_reasons || []}
                        busy={busy}
                        submitLabel="Xác nhận từ chối"
                        onCancel={() => setApproveRejectOpen(false)}
                        onSubmit={(payload) =>
                          mutate(
                            () =>
                              apiJson(`/api/todos/${myTodoId}/complete`, {
                                method: "POST",
                                body: JSON.stringify({
                                  decision: "reject",
                                  failure_reason_id: payload.failure_reason_id,
                                  note: payload.note || approveNote.trim(),
                                }),
                              }),
                            "Đã từ chối phê duyệt",
                          )
                        }
                      />
                    )}
                  </div>
                )}

                {curStage.type === "sign" && (
                  <TaskSignPanel
                    me={me}
                    task={task}
                    pdfBytes={pdfBytes}
                    failureReasons={wfDetail?.failure_reasons || []}
                    onRefreshMe={onRefreshMe}
                    onGoAccount={onGoAccount}
                    onUpdated={async (okMsg) => {
                      setTask((prev) =>
                        prev
                          ? { ...prev, permissions: { ...prev.permissions, my_todo_ids: [] } }
                          : prev,
                      );
                      await loadAll(true);
                      onNotifyRefresh();
                      setMsg(okMsg);
                    }}
                  />
                )}
              </div>
            )}

            {task.description_html && (
              <div className="card stack">
                <h3 style={{ margin: 0 }}>Mô tả nhiệm vụ</h3>
                <SafeHtml html={task.description_html} />
              </div>
            )}

            {task.form.length > 0 && (
              <div className="card stack" data-testid="task-custom-values">
                <h3 style={{ margin: 0 }}>Thông tin biểu mẫu</h3>
                <table className="tbl">
                  <tbody>
                    {task.form.map((f) => {
                      let display: React.ReactNode = "-";
                      if (f.value !== null && f.value !== undefined && f.value !== "") {
                        if (f.type === "date") {
                          display = formatShortDate(String(f.value));
                        } else if (f.type === "user") {
                          const u = users.find((x) => x.id === Number(f.value));
                          display = u ? userDisplayName(u) : `User #${String(f.value)}`;
                        } else if (f.type === "file") {
                          display = <span className="mono">File #{String(f.value)}</span>;
                        } else {
                          display = String(f.value);
                        }
                      }
                      return (
                        <tr key={f.field_id}>
                          <th style={{ width: "36%" }}>{f.label}</th>
                          <td>{display}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}

            <div className="card stack">
              <h3 style={{ margin: 0 }}>Lịch sử công việc từng bước ({task.todos.length})</h3>
              <div className="wf-table-scroll">
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>Giai đoạn</th>
                      <th>Người nhận</th>
                      <th>Trạng thái</th>
                      <th>Kết quả</th>
                      <th>Hoàn thành</th>
                    </tr>
                  </thead>
                  <tbody>
                    {task.todos.map((td) => (
                      <tr key={td.id}>
                        <td>{td.stage_order}. {td.stage_name}</td>
                        <td>{userDisplayName(td.assignee)}</td>
                        <td><StatusPill kind="todo" status={td.status} overdue={td.is_overdue} /></td>
                        <td>{formatTodoResult(td.result) || "-"}</td>
                        <td className="mono">{formatDateTime(td.completed_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <div className="stack">
            <div className="card stack">
              <h3 style={{ margin: 0 }}>Thông tin tham gia</h3>
              <div>
                <div className="muted">Người tạo</div>
                <strong>{userDisplayName(task.creator)}</strong>
              </div>
              <div>
                <div className="muted">Người nhận hiện tại</div>
                <div className="row wrap" style={{ marginTop: 4 }}>
                  {task.todos.filter((t) => t.status === "doing").length === 0 ? (
                    <span className="muted">Không có</span>
                  ) : (
                    task.todos
                      .filter((t) => t.status === "doing")
                      .map((t) => (
                        <span key={t.id} className="wf-chip">
                          {userDisplayName(t.assignee)}
                        </span>
                      ))
                  )}
                </div>
              </div>
              <div>
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <div className="muted">Người theo dõi ({task.followers.length})</div>
                  {task.permissions.can_add_followers && (
                    <button
                      type="button"
                      className="btn tiny"
                      onClick={() => {
                        setNewFollowerIds([]);
                        setFollowerAddOpen((v) => !v);
                      }}
                      data-testid="toggle-add-follower-btn"
                    >
                      + Thêm
                    </button>
                  )}
                </div>
                <div className="row wrap" style={{ marginTop: 4 }}>
                  {task.followers.length === 0 ? (
                    <span className="muted">Chưa có</span>
                  ) : (
                    task.followers.map((f) => (
                      <span key={f.id ?? f.username} className="wf-chip">
                        <span>{userDisplayName(f)}</span>
                        {task.permissions.can_add_followers && f.id !== null && (
                          <button
                            type="button"
                            className="wf-chip-x"
                            aria-label={`Gỡ ${f.display_name || f.username}`}
                            onClick={() =>
                              mutate(
                                () => apiJson(`/api/tasks/${task.id}/followers/${f.id}`, { method: "DELETE" }),
                                "Đã gỡ người theo dõi",
                              )
                            }
                          >
                            ×
                          </button>
                        )}
                      </span>
                    ))
                  )}
                </div>

                {followerAddOpen && task.permissions.can_add_followers && (
                  <div className="stack" style={{ marginTop: 8 }}>
                    <UserPicker
                      options={users.filter((u) => !task.followers.some((f) => f.id === u.id))}
                      selectedIds={newFollowerIds}
                      onChange={setNewFollowerIds}
                      multiple={false}
                      testIdPrefix="add-follower"
                    />
                    <button
                      type="button"
                      className="btn tiny primary"
                      disabled={busy || newFollowerIds.length === 0}
                      onClick={() =>
                        mutate(async () => {
                          await apiJson(`/api/tasks/${task.id}/followers`, {
                            method: "POST",
                            body: JSON.stringify({ user_id: newFollowerIds[0] }),
                          });
                          setFollowerAddOpen(false);
                          setNewFollowerIds([]);
                        }, "Đã thêm người theo dõi")
                      }
                      data-testid="confirm-add-follower-btn"
                    >
                      Xác nhận thêm
                    </button>
                  </div>
                )}
              </div>
            </div>

            {task.document && (
              <div className="card stack">
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <h3 style={{ margin: 0 }}>Tài liệu liên kết</h3>
                  <span className="badge mode-badge">v{task.document.current_version_no}</span>
                </div>
                <div>
                  <strong>{task.document.title}</strong>
                  <div className="muted">{task.document.file_name} · Trạng thái: {task.document.status}</div>
                </div>
                <div>
                  <div className="muted">Chữ ký số ({task.document.signatures.length})</div>
                  {task.document.signatures.length === 0 ? (
                    <span className="muted">Chưa có chữ ký nào</span>
                  ) : (
                    <div className="stack" style={{ marginTop: 4, gap: 6 }}>
                      {task.document.signatures.map((sig, i) => (
                        <div key={i} className="wf-sig-brief">
                          <strong>{userDisplayName(sig.signer)}</strong>
                          <div className="muted mono">
                            v{sig.version_no ?? "-"} · {sig.cert_key_ref || ""} · {formatDateTime(sig.signed_at)}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
                <button
                  type="button"
                  className="btn small"
                  onClick={() => setTab("document")}
                  data-testid="open-task-doc-tab"
                >
                  Xem PDF & Chi tiết ký
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {tab === "document" && (
        <div className="card stack" data-testid="task-document-panel">
          {!task.document ? (
            <div className="stack">
              <EmptyState
                title="Chưa có tài liệu đính kèm"
                description="Đính kèm tệp PDF hoặc Office để phục vụ kiểm tra và ký số."
              />
              {canAttachDoc && (
                <div className="stack">
                  <FileUploader
                    files={attachFiles}
                    maxFiles={1}
                    label="Chọn tệp đính kèm nhiệm vụ"
                    testId="task-doc-uploader"
                    onChange={setAttachFiles}
                  />
                  {attachFiles.length > 0 && (
                    <button
                      type="button"
                      className="btn small primary"
                      disabled={busy}
                      onClick={() =>
                        mutate(async () => {
                          await apiJson(`/api/tasks/${task.id}/document`, {
                            method: "POST",
                            body: JSON.stringify({ file_id: attachFiles[0].id }),
                          });
                          setAttachFiles([]);
                        }, "Đã đính kèm tài liệu vào nhiệm vụ")
                      }
                      data-testid="task-doc-attach-submit"
                    >
                      Gắn tài liệu vào nhiệm vụ
                    </button>
                  )}
                </div>
              )}
            </div>
          ) : (
            <div className="stack">
              <div className="preview-toolbar">
                <div>
                  <h3 style={{ margin: 0 }}>{task.document.title}</h3>
                  <div className="muted">
                    {task.document.file_name} · Phiên bản hiện tại: v{task.document.current_version_no}
                  </div>
                </div>
                <div className="row wrap">
                  <button type="button" className="btn small" onClick={handleDownloadPdf} data-testid="task-doc-download">
                    Tải PDF
                  </button>
                  <button type="button" className="btn small primary" disabled={busy} onClick={handleVerifyDoc} data-testid="task-doc-verify">
                    Xác minh chữ ký
                  </button>
                </div>
              </div>

              {verifyRes && (
                <div className={`alert ${verifyRes.all_ok ? "ok" : "error"}`} data-testid="task-doc-verify-result">
                  {verifyRes.all_ok
                    ? `Tất cả ${verifyRes.signatures.length} chữ ký hợp lệ và còn hiệu lực.`
                    : `Phát hiện ${verifyRes.signatures.length} chữ ký (cần kiểm tra chi tiết).`}
                </div>
              )}

              {task.document.signatures.length > 0 && (
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>Người ký</th>
                      <th>Phiên bản</th>
                      <th>Chứng thư</th>
                      <th>Thời điểm</th>
                      <th>Nhận xét</th>
                    </tr>
                  </thead>
                  <tbody>
                    {task.document.signatures.map((s, idx) => (
                      <tr key={idx}>
                        <td>{userDisplayName(s.signer)}</td>
                        <td className="mono">v{s.version_no ?? "-"}</td>
                        <td className="mono">{s.cert_key_ref || "-"}</td>
                        <td className="mono">{formatDateTime(s.signed_at)}</td>
                        <td>{s.remark || "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              <PdfPreviewBox
                pdfBytes={pdfBytes}
                page={previewPage}
                onPageChange={setPreviewPage}
                box={previewBox}
                onBoxChange={setPreviewBox}
                mode="text"
                reason=""
                signerName=""
                imagePreviewUrl={null}
                interactive={false}
              />
            </div>
          )}
        </div>
      )}

      {tab === "history" && (
        <div className="card stack" data-testid="task-history-panel">
          <h3 style={{ margin: 0 }}>Dòng thời gian nhiệm vụ & tài liệu</h3>
          <ActivityTimeline events={timeline?.events || task.activity} />
        </div>
      )}
    </div>
  );
}
