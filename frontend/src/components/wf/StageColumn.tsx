import React from "react";
import { initialsOf, type WfStage, type WfTaskItem } from "../../lib/wfApi";
import { StatusPill } from "./StatusPill";
import { TaskCard } from "./TaskCard";

interface StageColumnProps {
  kind: "stage" | "completed" | "failed";
  stage?: WfStage;
  title?: string;
  count: number;
  overdueCount?: number;
  totalTasks: number;
  tasks: WfTaskItem[];
  onHeaderClick?: () => void;
  onOpenTask: (taskId: number) => void;
}

export function StageColumn({
  kind,
  stage,
  title,
  count,
  overdueCount = 0,
  totalTasks,
  tasks,
  onHeaderClick,
  onOpenTask,
}: StageColumnProps) {
  const headerTitle = kind === "stage" && stage ? stage.name : (title || "");
  const members = stage?.members || [];

  return (
    <section
      className={`wf-stage-col kind-${kind}`}
      data-testid={kind === "stage" && stage ? `board-col-stage-${stage.id}` : `board-col-${kind}`}
    >
      <div className="wf-stage-header">
        <button
          type="button"
          className="wf-stage-header-btn"
          onClick={onHeaderClick}
          disabled={!onHeaderClick}
          title={onHeaderClick ? "Bấm để xem danh sách lọc theo trạng thái/bước này" : undefined}
          data-testid={kind === "stage" && stage ? `board-col-header-${stage.id}` : `board-col-header-${kind}`}
        >
          <div className="row" style={{ justifyContent: "space-between", width: "100%" }}>
            <div className="row" style={{ minWidth: 0 }}>
              {kind === "stage" && stage && (
                <span className="wf-stage-order">{stage.order}</span>
              )}
              <strong className="wf-stage-title">{headerTitle}</strong>
            </div>
            {kind === "stage" && stage && <StatusPill kind="stage" status={stage.type} />}
          </div>

          <div className="wf-stage-sub">
            <span className="muted">
              {count}/{totalTasks} NV · {overdueCount} Q.hạn
            </span>
            {kind === "stage" && stage && stage.sla_hours !== null && (
              <span className="wf-sla-tag" title={`Thời hạn SLA: ${stage.sla_hours} giờ`}>
                ⏱ {stage.sla_hours}h
              </span>
            )}
          </div>
        </button>

        {kind === "stage" && members.length > 0 && (
          <div className="wf-avatar-row" title={members.map((m) => m.display_name || m.username).join(", ")}>
            {members.slice(0, 5).map((m, idx) => (
              <span key={m.id ?? idx} className="wf-avatar tiny" title={m.display_name || m.username}>
                {initialsOf(m.display_name || m.username)}
              </span>
            ))}
            {members.length > 5 && <span className="muted">+{members.length - 5}</span>}
          </div>
        )}
      </div>

      <div className="wf-stage-body">
        {tasks.length === 0 ? (
          <div className="wf-col-empty muted">Không có nhiệm vụ</div>
        ) : (
          tasks.map((t) => <TaskCard key={t.id} task={t} onOpen={onOpenTask} />)
        )}
      </div>
    </section>
  );
}
