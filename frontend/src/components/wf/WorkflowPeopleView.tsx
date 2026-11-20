import React, { useCallback, useEffect, useState } from "react";
import { apiJson, type AssignableUser } from "../../lib/api";
import {
  addStageMembers,
  addWorkflowRole,
  removeStageMember,
  removeWorkflowRole,
  userDisplayName,
  type WfDetail,
  type WfRoleItem,
} from "../../lib/wfApi";
import { EmptyState } from "./EmptyState";
import { StatusPill } from "./StatusPill";
import { Topbar } from "./Topbar";

interface Props {
  workflowId: number;
  onBackBoard: (workflowId: number) => void;
  onEditWorkflow: (workflowId: number) => void;
  onWorkflowsChanged: () => void;
}

export function WorkflowPeopleView({ workflowId, onBackBoard, onEditWorkflow, onWorkflowsChanged }: Props) {
  const [wfDetail, setWfDetail] = useState<WfDetail | null>(null);
  const [users, setUsers] = useState<AssignableUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [roleUserId, setRoleUserId] = useState<string>("");
  const [roleKind, setRoleKind] = useState<WfRoleItem["role"]>("creator");
  const [stagePickers, setStagePickers] = useState<Record<number, string>>({});

  const loadAll = useCallback(async () => {
    setErr("");
    try {
      const [det, uList] = await Promise.all([
        apiJson<WfDetail>(`/api/workflows/${workflowId}`),
        apiJson<AssignableUser[]>("/api/users/assignable"),
      ]);
      setWfDetail(det);
      setUsers(uList);
      if (uList.length > 0) {
        setRoleUserId((prev) => prev || String(uList[0].id));
      }
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Không tải được danh sách thành viên");
    } finally {
      setLoading(false);
    }
  }, [workflowId]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  async function handleAddRole(e: React.FormEvent) {
    e.preventDefault();
    const uid = Number(roleUserId);
    if (!uid) return;
    setBusy(true);
    setErr("");
    setMsg("");
    try {
      const updated = await addWorkflowRole(workflowId, uid, roleKind);
      setWfDetail(updated);
      setMsg("Đã thêm vai trò thành công.");
      onWorkflowsChanged();
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Thêm vai trò thất bại");
    } finally {
      setBusy(false);
    }
  }

  async function handleRemoveRole(userId: number, role: string) {
    setBusy(true);
    setErr("");
    setMsg("");
    try {
      const updated = await removeWorkflowRole(workflowId, userId, role);
      setWfDetail(updated);
      setMsg("Đã gỡ vai trò thành công.");
      onWorkflowsChanged();
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Gỡ vai trò thất bại");
    } finally {
      setBusy(false);
    }
  }

  async function handleAddMember(stageId: number, fallbackUid?: number) {
    const picked = Number(stagePickers[stageId] || fallbackUid || 0);
    if (!picked) return;
    setBusy(true);
    setErr("");
    setMsg("");
    try {
      const updated = await addStageMembers(workflowId, stageId, [picked]);
      setWfDetail(updated);
      setStagePickers((prev) => ({ ...prev, [stageId]: "" }));
      setMsg("Đã thêm thành viên giai đoạn.");
      onWorkflowsChanged();
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Thêm thành viên thất bại");
    } finally {
      setBusy(false);
    }
  }

  async function handleRemoveMember(stageId: number, userId: number) {
    setBusy(true);
    setErr("");
    setMsg("");
    try {
      const updated = await removeStageMember(workflowId, stageId, userId);
      setWfDetail(updated);
      setMsg("Đã gỡ thành viên khỏi giai đoạn.");
      onWorkflowsChanged();
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Không thể gỡ thành viên khỏi giai đoạn");
    } finally {
      setBusy(false);
    }
  }

  if (loading && !wfDetail) {
    return <div className="card muted">Đang tải thông tin thành viên...</div>;
  }

  if (!wfDetail) {
    return (
      <EmptyState
        title="Không tìm thấy quy trình"
        description={err || "Quy trình không tồn tại hoặc bạn không có quyền xem."}
        actionLabel="Quay lại"
        onAction={() => onBackBoard(workflowId)}
      />
    );
  }

  const activeStages = wfDetail.stages.filter((s) => s.is_active !== false);
  const warnings = wfDetail.warnings || [];

  return (
    <div className="stack" data-testid="workflow-people-view">
      <Topbar
        title={`Thành viên & Phân quyền · ${wfDetail.name}`}
        backLabel="Bảng quy trình"
        onBack={() => onBackBoard(workflowId)}
        primaryAction={
          wfDetail.can_manage
            ? {
                label: "Chỉnh sửa quy trình",
                onClick: () => onEditWorkflow(workflowId),
                testId: "people-edit-wf-btn",
              }
            : null
        }
      />

      {msg && (
        <div className="alert ok" data-testid="wf-people-msg">
          {msg}
        </div>
      )}
      {err && (
        <div className="alert error" role="alert" data-testid="wf-people-error">
          {err}
        </div>
      )}

      {warnings.length > 0 && (
        <div className="alert warn stack" data-testid="wf-people-warnings" style={{ gap: 4 }}>
          <strong>Lưu ý chứng thư / PIN cho bước ký:</strong>
          {warnings.map((w, idx) => (
            <div key={idx}>• {w.message}</div>
          ))}
        </div>
      )}

      <div className="card stack" data-testid="wf-people-roles">
        <div className="row wrap" style={{ justifyContent: "space-between" }}>
          <h3 style={{ margin: 0 }}>Vai trò trong quy trình</h3>
          {!wfDetail.can_manage && <span className="badge muted">Chế độ chỉ đọc</span>}
        </div>

        {wfDetail.can_manage && (
          <form className="row wrap" onSubmit={handleAddRole}>
            <select
              value={roleUserId}
              onChange={(e) => setRoleUserId(e.target.value)}
              disabled={busy}
              data-testid="people-role-user"
            >
              {users.map((u) => (
                <option key={u.id} value={String(u.id)}>
                  {u.display_name || u.username} (@{u.username})
                </option>
              ))}
            </select>
            <select
              value={roleKind}
              onChange={(e) => setRoleKind(e.target.value as WfRoleItem["role"])}
              disabled={busy}
              data-testid="people-role-kind"
            >
              <option value="admin">admin (Quản trị quy trình)</option>
              <option value="supervisor">supervisor (Giám sát chỉ đọc)</option>
              <option value="creator">creator (Tạo công việc)</option>
              <option value="viewer">viewer (Xem công việc)</option>
            </select>
            <button type="submit" className="btn small primary" disabled={busy} data-testid="people-add-role-btn">
              Thêm vai trò
            </button>
          </form>
        )}

        {!wfDetail.roles || wfDetail.roles.length === 0 ? (
          <div className="muted">Bạn không có quyền xem danh sách vai trò hoặc chưa có vai trò được cấu hình.</div>
        ) : (
          <div className="wf-table-scroll">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Người dùng</th>
                  <th>Vai trò</th>
                  {wfDetail.can_manage && <th>Thao tác</th>}
                </tr>
              </thead>
              <tbody>
                {wfDetail.roles.map((r, i) => (
                  <tr key={`${r.user.id}-${r.role}-${i}`}>
                    <td>{userDisplayName(r.user)}</td>
                    <td>
                      <span className="badge muted">{r.role}</span>
                    </td>
                    {wfDetail.can_manage && (
                      <td>
                        {r.user.id !== null && (
                          <button
                            type="button"
                            className="btn tiny danger"
                            disabled={busy}
                            onClick={() => handleRemoveRole(r.user.id as number, r.role)}
                            data-testid={`people-remove-role-${r.user.id}-${r.role}`}
                          >
                            Gỡ
                          </button>
                        )}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card stack" data-testid="wf-people-stages">
        <h3 style={{ margin: 0 }}>Thành viên từng giai đoạn ({activeStages.length} bước hoạt động)</h3>
        <div className="wf-people-grid">
          {activeStages.map((st) => {
            const members = st.members || [];
            const memberIds = new Set(members.map((m) => m.id).filter((id): id is number => typeof id === "number"));
            const candidates = users.filter((u) => !memberIds.has(u.id));
            const selectedVal = stagePickers[st.id] || (candidates[0] ? String(candidates[0].id) : "");

            return (
              <div key={st.id} className="wf-builder-stage-card stack" data-testid={`people-stage-${st.id}`}>
                <div className="row wrap" style={{ justifyContent: "space-between" }}>
                  <div className="row wrap">
                    <strong>Bước {st.order}: {st.name}</strong>
                    <StatusPill kind="stage" status={st.type} />
                  </div>
                  <span className="muted">
                    {st.sla_hours ? `SLA ${st.sla_hours}h` : "Không giới hạn"} ·{" "}
                    {st.assign_mode === "any" ? "any" : "all"}
                  </span>
                </div>

                <div className="row wrap">
                  {members.length === 0 ? (
                    <span className="muted">Chưa có thành viên.</span>
                  ) : (
                    members.map((m, idx) => (
                      <span key={m.id ?? idx} className="wf-chip">
                        <span>{userDisplayName(m)}</span>
                        {wfDetail.can_manage && m.id !== null && (
                          <button
                            type="button"
                            className="wf-chip-x"
                            disabled={busy}
                            aria-label={`Gỡ ${m.display_name || m.username}`}
                            onClick={() => handleRemoveMember(st.id, m.id as number)}
                            data-testid={`people-remove-member-${st.id}-${m.id}`}
                          >
                            ×
                          </button>
                        )}
                      </span>
                    ))
                  )}
                </div>

                {wfDetail.can_manage && candidates.length > 0 && (
                  <div className="row wrap">
                    <select
                      value={selectedVal}
                      onChange={(e) => setStagePickers((p) => ({ ...p, [st.id]: e.target.value }))}
                      disabled={busy}
                      data-testid={`people-add-member-select-${st.id}`}
                    >
                      {candidates.map((u) => (
                        <option key={u.id} value={String(u.id)}>
                          {u.display_name || u.username} (@{u.username})
                        </option>
                      ))}
                    </select>
                    <button
                      type="button"
                      className="btn tiny"
                      disabled={busy || !selectedVal}
                      onClick={() => handleAddMember(st.id, candidates[0]?.id)}
                      data-testid={`people-add-member-btn-${st.id}`}
                    >
                      + Thêm thành viên
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
