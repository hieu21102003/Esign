import React, { useCallback, useEffect, useState } from "react";
import { apiJson } from "../../lib/api";
import { useVisibilityPolling } from "../../lib/usePolling";
import {
  formatDateTime,
  formatTodoResult,
  userDisplayName,
  type WfPaged,
  type WfTodoItem,
} from "../../lib/wfApi";
import { EmptyState } from "./EmptyState";
import { StatusPill } from "./StatusPill";
import { Topbar } from "./Topbar";

const STATUS_TABS = [
  { id: "", label: "Tất cả" },
  { id: "doing", label: "Đang làm" },
  { id: "done", label: "Đã hoàn thành" },
  { id: "overdue", label: "Quá hạn" },
];

const ROLES = [
  { id: "assigned", label: "Việc của tôi" },
  { id: "created", label: "Tôi tạo nhiệm vụ" },
  { id: "following", label: "Tôi theo dõi" },
  { id: "team", label: "Nhóm / Phòng ban" },
];

export function TodosView({ onOpenTask }: { onOpenTask: (taskId: number) => void }) {
  const [role, setRole] = useState("assigned");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<WfPaged<WfTodoItem> | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

  const loadTodos = useCallback(async () => {
    setErr("");
    try {
      const qs = new URLSearchParams({ role, page: String(page), page_size: "20" });
      if (status) qs.set("status", status);
      const res = await apiJson<WfPaged<WfTodoItem>>(`/api/todos?${qs.toString()}`);
      setData(res);
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Không tải được danh sách công việc");
    } finally {
      setLoading(false);
    }
  }, [role, status, page]);

  useEffect(() => {
    loadTodos();
  }, [loadTodos]);

  useVisibilityPolling(loadTodos, 25000);

  return (
    <div className="stack" data-testid="todos-view">
      <Topbar
        title="Công việc của tôi"
        counter={data ? `${data.total} việc` : undefined}
        secondaryActions={
          <select
            value={role}
            onChange={(e) => {
              setRole(e.target.value);
              setPage(1);
            }}
            aria-label="Chuyển vai trò"
            style={{ width: "auto" }}
            data-testid="todos-role-select"
          >
            {ROLES.map((r) => (
              <option key={r.id} value={r.id}>
                {r.label}
              </option>
            ))}
          </select>
        }
      />

      <div className="card stack">
        <div className="row wrap">
          {STATUS_TABS.map((t) => (
            <button
              key={t.id || "all"}
              type="button"
              className={`wf-pill-btn ${status === t.id ? "active" : ""}`}
              onClick={() => {
                setStatus(t.id);
                setPage(1);
              }}
              data-testid={`todos-status-${t.id || "all"}`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {err && <div className="alert error">{err}</div>}

        {loading && !data ? (
          <div className="muted">Đang tải công việc...</div>
        ) : !data || data.items.length === 0 ? (
          <EmptyState
            title="Không có công việc nào"
            description="Bạn chưa có đầu việc nào khớp bộ lọc hiện tại."
          />
        ) : (
          <div className="wf-table-scroll">
            <table className="tbl wf-clickable-table" data-testid="todos-table">
              <thead>
                <tr>
                  <th>Công việc</th>
                  <th>Kết quả</th>
                  <th>Giao cho</th>
                  <th>Thời hạn</th>
                  <th>Nhiệm vụ liên kết</th>
                  <th>Quy trình</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((td) => (
                  <tr
                    key={td.id}
                    onClick={() => onOpenTask(td.task_id)}
                    data-testid={`todo-row-${td.id}`}
                  >
                    <td>
                      <div className="row wrap">
                        <strong>
                          {td.stage_order ? `Bước ${td.stage_order}: ` : ""}
                          {td.stage_name || `Công việc #${td.id}`}
                        </strong>
                        <StatusPill kind="todo" status={td.status} overdue={td.is_overdue} />
                      </div>
                    </td>
                    <td>{formatTodoResult(td.result) || <span className="muted">-</span>}</td>
                    <td>{userDisplayName(td.assignee)}</td>
                    <td className={`mono ${td.is_overdue ? "text-danger" : ""}`}>
                      {formatDateTime(td.deadline)}
                    </td>
                    <td>
                      <div>{td.task?.title || `#${td.task_id}`}</div>
                    </td>
                    <td>{td.task?.workflow_name || "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {data && data.pages > 1 && (
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span className="muted">
              Trang {data.page} / {data.pages}
            </span>
            <div className="row">
              <button type="button" className="btn tiny" disabled={page <= 1} onClick={() => setPage(page - 1)}>
                Trước
              </button>
              <button
                type="button"
                className="btn tiny"
                disabled={page >= data.pages}
                onClick={() => setPage(page + 1)}
              >
                Sau
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
