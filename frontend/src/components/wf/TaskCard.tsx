import React from "react";
import { formatDateTime, initialsOf, type WfTaskItem } from "../../lib/wfApi";
import { StatusPill } from "./StatusPill";

interface Props {
  task: WfTaskItem;
  onOpen: (taskId: number) => void;
}

export function TaskCard({ task, onOpen }: Props) {
  return (
    <button
      type="button"
      className={`wf-task-card ${task.is_overdue ? "overdue" : ""}`}
      onClick={() => onOpen(task.id)}
      data-testid={`task-card-${task.id}`}
    >
      <div className="wf-task-card-head">
        <strong className="wf-task-card-title">{task.title}</strong>
        {task.document_id && (
          <span className="badge mode-badge" title="Có tài liệu gắn kèm">
            PDF
          </span>
        )}
      </div>

      {task.labels.length > 0 && (
        <div className="row wrap" style={{ gap: 4 }}>
          {task.labels.map((lb) => (
            <span key={lb} className="wf-chip small">{lb}</span>
          ))}
        </div>
      )}

      <div className="wf-task-card-meta">
        <div className="row wrap" style={{ gap: 4 }}>
          {task.assignees.length === 0 ? (
            <span className="muted">Chưa phân công</span>
          ) : (
            task.assignees.slice(0, 3).map((a) => (
              <span key={a.id} className="wf-assignee-pill" title={a.display_name}>
                <span className="wf-avatar tiny">{initialsOf(a.display_name)}</span>
                <span>{a.display_name}</span>
              </span>
            ))
          )}
          {task.assignees.length > 3 && (
            <span className="muted">+{task.assignees.length - 3}</span>
          )}
        </div>

        <div className="row wrap" style={{ justifyContent: "space-between", width: "100%" }}>
          <span className={`muted mono ${task.is_overdue ? "text-danger" : ""}`}>
            {task.deadline ? `Hạn: ${formatDateTime(task.deadline)}` : "Không có SLA"}
          </span>
          {task.is_overdue && <StatusPill kind="task" status={task.status} overdue />}
        </div>
      </div>
    </button>
  );
}
