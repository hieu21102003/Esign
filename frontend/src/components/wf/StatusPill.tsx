import React from "react";
import type { StageType, TaskStatus, TodoStatus, WorkflowStatusKind } from "../../lib/wfApi";

const TASK_MAP: Record<TaskStatus | "overdue", { label: string; cls: string }> = {
  processing: { label: "Đang xử lý", cls: "warn" },
  completed: { label: "Hoàn thành", cls: "ok" },
  failed: { label: "Thất bại", cls: "bad" },
  overdue: { label: "Quá hạn", cls: "bad" },
};

const TODO_MAP: Record<TodoStatus | "overdue", { label: string; cls: string }> = {
  doing: { label: "Đang làm", cls: "warn" },
  done: { label: "Đã hoàn thành", cls: "ok" },
  failed: { label: "Thất bại", cls: "bad" },
  cancelled: { label: "Đã hủy", cls: "muted" },
  overdue: { label: "Quá hạn", cls: "bad" },
};

const STAGE_TYPE_MAP: Record<StageType, { label: string; cls: string }> = {
  normal: { label: "Thường", cls: "muted" },
  sign: { label: "Ký số", cls: "mode-badge" },
  approve: { label: "Phê duyệt", cls: "warn" },
};

const WF_STATUS_MAP: Record<WorkflowStatusKind, { label: string; cls: string }> = {
  active: { label: "Đang khả dụng", cls: "ok" },
  closed: { label: "Đã đóng", cls: "muted" },
};

type PillProps =
  | { kind: "task"; status: TaskStatus; overdue?: boolean; testId?: string }
  | { kind: "todo"; status: TodoStatus; overdue?: boolean; testId?: string }
  | { kind: "stage"; status: StageType; testId?: string }
  | { kind: "workflow"; status: WorkflowStatusKind; testId?: string };

export function StatusPill(props: PillProps) {
  let item: { label: string; cls: string } = { label: String(props.status), cls: "muted" };
  if (props.kind === "task") {
    item = props.overdue && props.status === "processing"
      ? TASK_MAP.overdue
      : TASK_MAP[props.status] || item;
  } else if (props.kind === "todo") {
    item = props.overdue && props.status === "doing"
      ? TODO_MAP.overdue
      : TODO_MAP[props.status] || item;
  } else if (props.kind === "stage") {
    item = STAGE_TYPE_MAP[props.status] || item;
  } else if (props.kind === "workflow") {
    item = WF_STATUS_MAP[props.status] || item;
  }
  return (
    <span className={`badge ${item.cls}`} data-testid={props.testId}>
      {item.label}
    </span>
  );
}
