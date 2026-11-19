import React from "react";
import type { TimelineEvent, UserBrief, WorkflowDocumentDetail, WorkflowStatus } from "../lib/api";

export const DOC_STATUS_MAP: Record<WorkflowStatus, { label: string; cls: string }> = {
  in_progress: { label: "Đang ký", cls: "warn" },
  completed: { label: "Hoàn tất", cls: "ok" },
  rejected: { label: "Bị từ chối", cls: "bad" },
  cancelled: { label: "Đã hủy", cls: "muted" },
};

const STEP_STATUS_MAP: Record<string, { label: string; cls: string }> = {
  active: { label: "Bước hiện tại", cls: "warn" },
  completed: { label: "Đã xong", cls: "ok" },
  rejected: { label: "Bị từ chối", cls: "bad" },
  cancelled: { label: "Đã hủy", cls: "muted" },
};

const ASSIGNEE_STATUS_MAP: Record<string, { label: string; cls: string }> = {
  pending: { label: "Chờ ký", cls: "warn" },
  signed: { label: "Đã ký", cls: "ok" },
  rejected: { label: "Từ chối", cls: "bad" },
  skipped: { label: "Đã bỏ qua", cls: "muted" },
};

function userLabel(u: UserBrief): string {
  if (!u.display_name) return u.username || "-";
  return u.username ? `${u.display_name} (@${u.username})` : u.display_name;
}

function fmtTime(iso: string | null): string {
  return iso ? iso.slice(0, 19).replace("T", " ") : "";
}

interface Props {
  detail: WorkflowDocumentDetail;
  events: TimelineEvent[];
}

export function WorkflowTimeline({ detail, events }: Props) {
  const docBadge = DOC_STATUS_MAP[detail.status] || { label: detail.status, cls: "" };

  return (
    <div className="card stack" data-testid="workflow-timeline">
      <div className="preview-toolbar">
        <div className="row wrap">
          <h3>Hành trình tài liệu</h3>
          <span className={`badge ${docBadge.cls}`} data-testid="timeline-overall-status">
            {docBadge.label}
          </span>
        </div>
        <span className="muted">
          Tạo bởi {userLabel(detail.creator)} · {fmtTime(detail.created_at)}
        </span>
      </div>

      {detail.status === "cancelled" && detail.cancel_reason && (
        <div className="alert error">Lý do hủy luồng: {detail.cancel_reason}</div>
      )}

      <div className="wf-timeline-list">
        {detail.steps.map((step) => {
          const isCurrent = detail.status === "in_progress" && step.step_order === detail.current_step_order;
          const stepBadge = STEP_STATUS_MAP[step.status] || { label: step.status, cls: "" };
          const assignerName = userLabel(step.assigned_by);

          return (
            <div
              key={step.id}
              className={`wf-step-card status-${step.status} ${isCurrent ? "current" : ""}`}
              data-testid={`timeline-step-${step.step_order}`}
            >
              <div className="wf-step-head">
                <div className="row wrap">
                  <strong>Bước {step.step_order}</strong>
                  <span className="badge mode-badge" data-testid={`timeline-step-${step.step_order}-mode`}>
                    {step.mode === "all" ? "Tất cả phải ký (ALL)" : "Chỉ cần 1 người ký (ANY)"}
                  </span>
                  <span className={`badge ${stepBadge.cls}`}>{stepBadge.label}</span>
                </div>
                <span className="muted">
                  Chuyển bởi {assignerName} · {fmtTime(step.created_at)}
                </span>
              </div>

              <div className="wf-assignee-grid">
                {step.assignees.map((a) => {
                  const st = ASSIGNEE_STATUS_MAP[a.status] || { label: a.status, cls: "" };
                  return (
                    <div
                      key={a.id}
                      className={`wf-assignee-card status-${a.status}`}
                      data-testid={`timeline-assignee-${a.user.username}`}
                    >
                      <div className="row wrap" style={{ justifyContent: "space-between" }}>
                        <strong>{userLabel(a.user)}</strong>
                        <span className={`badge ${st.cls}`}>{st.label}</span>
                      </div>
                      <div className="muted">Giao bởi: {assignerName}</div>
                      {a.signed_at && (
                        <div className="muted mono">Thời điểm: {fmtTime(a.signed_at)}</div>
                      )}
                      {a.remark && (
                        <div className="wf-remark">Nhận xét: {a.remark}</div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>

      {events.length > 0 && (
        <details className="wf-audit-details">
          <summary>Nhật ký kiểm toán chi tiết ({events.length} sự kiện)</summary>
          <table className="tbl" style={{ marginTop: 8 }}>
            <thead>
              <tr>
                <th>Thời điểm</th>
                <th>Người thực hiện</th>
                <th>Sự kiện</th>
                <th>IP</th>
                <th>Chi tiết</th>
              </tr>
            </thead>
            <tbody>
              {events.map((ev) => (
                <tr key={ev.id}>
                  <td className="mono">{fmtTime(ev.ts)}</td>
                  <td>{ev.username || "-"}</td>
                  <td className="mono">{ev.action}</td>
                  <td className="mono">{ev.ip}</td>
                  <td>{ev.detail || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  );
}
