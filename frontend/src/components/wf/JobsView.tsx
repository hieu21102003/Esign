import React, { useCallback, useEffect, useMemo, useState } from "react";
import { apiJson, type AssignableUser } from "../../lib/api";
import {
  countActiveJobFilters,
  DEFAULT_JOBS_FILTER,
  navigateTo,
  parseJobsQuery,
  serializeJobsQuery,
  type JobsFilterState,
} from "../../lib/router";
import { useVisibilityPolling } from "../../lib/usePolling";
import {
  downloadCsv,
  formatDateTime,
  type JobRoleFilter,
  type WfDetail,
  type WfPaged,
  type WfSummary,
  type WfTaskItem,
} from "../../lib/wfApi";
import { EmptyState } from "./EmptyState";
import { FilterDrawer } from "./FilterDrawer";
import { StatusPill } from "./StatusPill";
import { Topbar } from "./Topbar";

const ROLE_TABS: { id: JobRoleFilter; label: string }[] = [
  { id: "assigned", label: "Được giao" },
  { id: "created", label: "Tạo bởi tôi" },
  { id: "following", label: "Tôi theo dõi" },
  { id: "team", label: "Nhân viên của tôi" },
  { id: "", label: "Tất cả" },
];

const STATUS_PILLS: { id: string; label: string }[] = [
  { id: "", label: "Tất cả" },
  { id: "processing", label: "Đang xử lý" },
  { id: "completed", label: "Hoàn thành" },
  { id: "failed", label: "Thất bại" },
  { id: "overdue", label: "Quá hạn" },
];

function buildJobsApiParams(f: JobsFilterState): string {
  const qs = new URLSearchParams();
  if (f.role) qs.set("role", f.role);
  if (f.status) qs.set("status", f.status);
  if (f.workflow_id) qs.set("workflow_id", f.workflow_id);
  if (f.stage_id) qs.set("stage_id", f.stage_id);
  if (f.q.trim()) qs.set("q", f.q.trim());
  if (f.created_from) qs.set("created_from", f.created_from);
  if (f.created_to) qs.set("created_to", f.created_to);
  if (f.deadline_from) qs.set("deadline_from", f.deadline_from);
  if (f.deadline_to) qs.set("deadline_to", f.deadline_to);
  if (f.completed_from) qs.set("completed_from", f.completed_from);
  if (f.completed_to) qs.set("completed_to", f.completed_to);
  if (f.sort) qs.set("sort", f.sort);
  qs.set("page", String(f.page));
  qs.set("page_size", "20");
  return qs.toString();
}

export function JobsView({ onOpenTask }: { onOpenTask: (taskId: number) => void }) {
  const [filter, setFilter] = useState<JobsFilterState>(() =>
    typeof window !== "undefined" ? parseJobsQuery(window.location.search) : DEFAULT_JOBS_FILTER,
  );
  const [data, setData] = useState<WfPaged<WfTaskItem> | null>(null);
  const [workflows, setWorkflows] = useState<WfSummary[]>([]);
  const [wfDetail, setWfDetail] = useState<WfDetail | null>(null);
  const [users, setUsers] = useState<AssignableUser[]>([]);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [exporting, setExporting] = useState(false);

  const loadMeta = useCallback(async () => {
    const [wfRes, uRes] = await Promise.all([
      apiJson<{ items: WfSummary[] }>("/api/workflows"),
      apiJson<AssignableUser[]>("/api/users/assignable"),
    ]);
    setWorkflows(wfRes.items || []);
    setUsers(uRes || []);
  }, []);

  const loadJobs = useCallback(async () => {
    setErr("");
    try {
      const res = await apiJson<WfPaged<WfTaskItem>>(`/api/jobs?${buildJobsApiParams(filter)}`);
      setData(res);
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Không tải được danh sách nhiệm vụ");
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    loadMeta().catch(() => {});
  }, [loadMeta]);

  useEffect(() => {
    loadJobs();
  }, [loadJobs]);

  useEffect(() => {
    if (!filter.workflow_id) {
      setWfDetail(null);
      return;
    }
    apiJson<WfDetail>(`/api/workflows/${filter.workflow_id}`)
      .then(setWfDetail)
      .catch(() => setWfDetail(null));
  }, [filter.workflow_id]);

  useEffect(() => {
    function onPop() {
      if (window.location.pathname.startsWith("/jobs")) {
        setFilter(parseJobsQuery(window.location.search));
      }
    }
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  function updateFilter(next: JobsFilterState) {
    setFilter(next);
    const qs = serializeJobsQuery(next);
    navigateTo(qs ? `/jobs?${qs}` : "/jobs", true);
  }

  useVisibilityPolling(loadJobs, 25000, !drawerOpen);

  const visibleItems = useMemo(() => {
    const raw = data?.items || [];
    return raw.filter((it) => {
      if (filter.creator_id && String(it.creator.id) !== filter.creator_id) return false;
      if (filter.assignee_id && !it.assignees.some((a) => String(a.id) === filter.assignee_id)) return false;
      return true;
    });
  }, [data, filter.creator_id, filter.assignee_id]);

  const filterCount = countActiveJobFilters(filter);

  async function handleExport() {
    setExporting(true);
    setErr("");
    try {
      await downloadCsv(`/api/jobs/export?${buildJobsApiParams(filter)}`, "nhiem-vu.csv");
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Xuất file Excel/CSV thất bại");
    } finally {
      setExporting(false);
    }
  }

  return (
    <div className="stack" data-testid="jobs-view">
      <Topbar
        title="Nhiệm vụ của tôi"
        counter={data ? `${data.total} nhiệm vụ` : undefined}
        search={filter.q}
        searchPlaceholder="Tìm theo tiêu đề, nhãn, mô tả..."
        onSearchChange={(q) => updateFilter({ ...filter, q, page: 1 })}
        filterCount={filterCount}
        onOpenFilter={() => setDrawerOpen(true)}
        secondaryActions={
          <button
            type="button"
            className="btn small"
            disabled={exporting}
            onClick={handleExport}
            data-testid="jobs-export-btn"
          >
            {exporting ? "Đang xuất..." : "Xuất Excel"}
          </button>
        }
      />

      <div className="card stack">
        <div className="wf-toolbar-rows">
          <div className="row wrap" role="tablist" aria-label="Vai trò nhiệm vụ">
            {ROLE_TABS.map((rt) => (
              <button
                key={rt.id || "all"}
                type="button"
                role="tab"
                aria-selected={filter.role === rt.id}
                className={`btn small ${filter.role === rt.id ? "primary" : ""}`}
                onClick={() => updateFilter({ ...filter, role: rt.id, page: 1 })}
                data-testid={`jobs-role-${rt.id || "all"}`}
              >
                {rt.label}
              </button>
            ))}
          </div>

          <div className="row wrap">
            {STATUS_PILLS.map((sp) => (
              <button
                key={sp.id || "all"}
                type="button"
                className={`wf-pill-btn ${filter.status === sp.id ? "active" : ""}`}
                onClick={() => updateFilter({ ...filter, status: sp.id, page: 1 })}
                data-testid={`jobs-status-${sp.id || "all"}`}
              >
                {sp.label}
              </button>
            ))}
          </div>
        </div>

        {err && <div className="alert error">{err}</div>}

        {loading && !data ? (
          <div className="muted">Đang tải nhiệm vụ...</div>
        ) : visibleItems.length === 0 ? (
          <EmptyState
            title="Chưa có nhiệm vụ phù hợp"
            description="Thử đổi tab vai trò hoặc xóa bớt điều kiện lọc."
            actionLabel={filterCount > 0 ? "Xóa bộ lọc" : undefined}
            onAction={
              filterCount > 0
                ? () => updateFilter({ ...DEFAULT_JOBS_FILTER, role: filter.role })
                : undefined
            }
          />
        ) : (
          <div className="wf-table-scroll">
            <table className="tbl wf-clickable-table" data-testid="jobs-table">
              <thead>
                <tr>
                  <th>Tiêu đề</th>
                  <th>Quy trình</th>
                  <th>Giai đoạn</th>
                  <th>Trạng thái</th>
                  <th>Người tạo</th>
                  <th>Người nhận</th>
                  <th>Thời hạn</th>
                </tr>
              </thead>
              <tbody>
                {visibleItems.map((it) => (
                  <tr
                    key={it.id}
                    onClick={() => onOpenTask(it.id)}
                    data-testid={`job-row-${it.id}`}
                  >
                    <td>
                      <strong>{it.title}</strong>
                      {it.labels.length > 0 && (
                        <div className="row wrap" style={{ marginTop: 3, gap: 4 }}>
                          {it.labels.map((lb) => (
                            <span key={lb} className="wf-chip small">{lb}</span>
                          ))}
                        </div>
                      )}
                    </td>
                    <td>{it.workflow_name}</td>
                    <td>{it.current_stage ? `${it.current_stage.order}. ${it.current_stage.name}` : "-"}</td>
                    <td>
                      <StatusPill kind="task" status={it.status} overdue={it.is_overdue} />
                    </td>
                    <td>{it.creator.display_name}</td>
                    <td>
                      {it.assignees.length === 0
                        ? "-"
                        : it.assignees.map((a) => a.display_name).join(", ")}
                    </td>
                    <td className={`mono ${it.is_overdue ? "text-danger" : ""}`}>
                      {formatDateTime(it.deadline)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {data && data.pages > 1 && (
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span className="muted">
              Trang {data.page} / {data.pages} ({data.total} kết quả)
            </span>
            <div className="row">
              <button
                type="button"
                className="btn tiny"
                disabled={data.page <= 1}
                onClick={() => updateFilter({ ...filter, page: data.page - 1 })}
              >
                Trước
              </button>
              <button
                type="button"
                className="btn tiny"
                disabled={data.page >= data.pages}
                onClick={() => updateFilter({ ...filter, page: data.page + 1 })}
              >
                Sau
              </button>
            </div>
          </div>
        )}
      </div>

      <FilterDrawer
        open={drawerOpen}
        value={filter}
        workflows={workflows}
        workflowDetail={wfDetail}
        users={users}
        onClose={() => setDrawerOpen(false)}
        onApply={updateFilter}
        onReset={() => updateFilter({ ...DEFAULT_JOBS_FILTER, role: filter.role })}
      />
    </div>
  );
}
