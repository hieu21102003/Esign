import React, { useCallback, useEffect, useMemo, useState } from "react";
import { apiJson, type AssignableUser } from "../../lib/api";
import {
  countActiveJobFilters,
  DEFAULT_JOBS_FILTER,
  type JobsFilterState,
} from "../../lib/router";
import { useVisibilityPolling } from "../../lib/usePolling";
import {
  cloneWorkflow,
  closeWorkflow,
  downloadCsv,
  formatDateTime,
  reopenWorkflow,
  saveWorkflowAsTemplate,
  type WfBoard,
  type WfDetail,
  type WfPaged,
  type WfTaskItem,
} from "../../lib/wfApi";
import { CreateTaskModal } from "./CreateTaskModal";
import { EmptyState } from "./EmptyState";
import { FilterDrawer } from "./FilterDrawer";
import { SafeHtml } from "./SafeHtml";
import { StageColumn } from "./StageColumn";
import { StatusPill } from "./StatusPill";
import { Topbar } from "./Topbar";

interface Props {
  workflowId: number;
  archiveMode?: boolean;
  onBackWorkflows: () => void;
  onOpenBoard: (workflowId: number) => void;
  onOpenArchive: (workflowId: number) => void;
  onEditWorkflow: (workflowId: number) => void;
  onOpenPeople: (workflowId: number) => void;
  onOpenTask: (taskId: number) => void;
  onNotifyRefresh: () => void;
  onWorkflowsChanged: () => void;
}

export function WorkflowBoardView({
  workflowId,
  archiveMode = false,
  onBackWorkflows,
  onOpenBoard,
  onOpenArchive,
  onEditWorkflow,
  onOpenPeople,
  onOpenTask,
  onNotifyRefresh,
  onWorkflowsChanged,
}: Props) {
  const [view, setView] = useState<"board" | "list">("board");
  const [wfDetail, setWfDetail] = useState<WfDetail | null>(null);
  const [board, setBoard] = useState<WfBoard | null>(null);
  const [users, setUsers] = useState<AssignableUser[]>([]);
  const [filter, setFilter] = useState<JobsFilterState>(() => ({
    ...DEFAULT_JOBS_FILTER,
    role: "",
    workflow_id: String(workflowId),
  }));
  const [listData, setListData] = useState<WfPaged<WfTaskItem> | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [guideOpen, setGuideOpen] = useState(false);
  const [cloneOpen, setCloneOpen] = useState(false);
  const [cloneName, setCloneName] = useState("");
  const [cloneBusy, setCloneBusy] = useState(false);
  const [saveTplOpen, setSaveTplOpen] = useState(false);
  const [tplName, setTplName] = useState("");
  const [tplCategory, setTplCategory] = useState("");
  const [tplDesc, setTplDesc] = useState("");
  const [saveTplBusy, setSaveTplBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");

  useEffect(() => {
    setFilter({
      ...DEFAULT_JOBS_FILTER,
      role: "",
      workflow_id: String(workflowId),
    });
    setView("board");
  }, [workflowId, archiveMode]);

  const loadCore = useCallback(async () => {
    setErr("");
    try {
      const [det, brd, uList] = await Promise.all([
        apiJson<WfDetail>(`/api/workflows/${workflowId}`),
        apiJson<WfBoard>(`/api/workflows/${workflowId}/board`),
        apiJson<AssignableUser[]>("/api/users/assignable"),
      ]);
      setWfDetail(det);
      setBoard(brd);
      setUsers(uList);
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Không tải được bảng quy trình");
    } finally {
      setLoading(false);
    }
  }, [workflowId]);

  const buildListQuery = useCallback(
    (archived: boolean) => {
      const qs = new URLSearchParams();
      qs.set("workflow_id", String(workflowId));
      if (archived) qs.set("archived", "true");
      if (filter.status) qs.set("status", filter.status);
      if (filter.stage_id) qs.set("stage_id", filter.stage_id);
      if (filter.q.trim()) qs.set("q", filter.q.trim());
      if (filter.created_from) qs.set("created_from", filter.created_from);
      if (filter.created_to) qs.set("created_to", filter.created_to);
      if (filter.deadline_from) qs.set("deadline_from", filter.deadline_from);
      if (filter.deadline_to) qs.set("deadline_to", filter.deadline_to);
      if (filter.completed_from) qs.set("completed_from", filter.completed_from);
      if (filter.completed_to) qs.set("completed_to", filter.completed_to);
      if (filter.sort) qs.set("sort", filter.sort);
      qs.set("page", String(filter.page));
      qs.set("page_size", "20");
      return qs.toString();
    },
    [workflowId, filter],
  );

  const loadList = useCallback(async () => {
    try {
      const res = await apiJson<WfPaged<WfTaskItem>>(`/api/jobs?${buildListQuery(archiveMode)}`);
      setListData(res);
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Không tải được danh sách nhiệm vụ");
    }
  }, [buildListQuery, archiveMode]);

  useEffect(() => {
    loadCore();
  }, [loadCore]);

  useEffect(() => {
    if (archiveMode || view === "list") {
      loadList();
    }
  }, [archiveMode, view, loadList]);

  const refreshActive = useCallback(async () => {
    await loadCore();
    if (archiveMode || view === "list") {
      await loadList();
    }
  }, [loadCore, loadList, archiveMode, view]);

  useVisibilityPolling(refreshActive, 25000, !createOpen && !cloneOpen && !saveTplOpen && !guideOpen && !drawerOpen);

  const totalTasks = useMemo(() => {
    if (!board) return 0;
    return board.stages.reduce((s, c) => s + c.count, 0) + board.completed.count + board.failed.count;
  }, [board]);

  const boardQuery = filter.q.trim().toLowerCase();
  const filterTasks = (tasks: WfTaskItem[]) => {
    if (!boardQuery) return tasks;
    return tasks.filter(
      (t) =>
        t.title.toLowerCase().includes(boardQuery) ||
        t.labels.some((lb) => lb.toLowerCase().includes(boardQuery)),
    );
  };

  async function handleRestore(taskId: number) {
    setErr("");
    setMsg("");
    try {
      await apiJson(`/api/tasks/${taskId}/restore`, { method: "POST" });
      setMsg("Đã khôi phục nhiệm vụ khỏi lưu trữ");
      await refreshActive();
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Khôi phục nhiệm vụ thất bại");
    }
  }

  async function handleCloseWorkflow() {
    setErr("");
    setMsg("");
    try {
      const updated = await closeWorkflow(workflowId);
      setWfDetail(updated);
      setMsg("Đã đóng quy trình. Không thể tạo nhiệm vụ mới nhưng công việc đang xử lý vẫn tiếp tục.");
      onWorkflowsChanged();
      await refreshActive();
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Đóng quy trình thất bại");
    }
  }

  async function handleReopenWorkflow() {
    setErr("");
    setMsg("");
    try {
      const updated = await reopenWorkflow(workflowId);
      setWfDetail(updated);
      setMsg("Đã mở lại quy trình.");
      onWorkflowsChanged();
      await refreshActive();
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Mở lại quy trình thất bại");
    }
  }

  async function handleCloneSubmit(e: React.FormEvent) {
    e.preventDefault();
    setCloneBusy(true);
    setErr("");
    try {
      const cloned = await cloneWorkflow(workflowId, {
        name: cloneName.trim() || undefined,
      });
      setCloneOpen(false);
      onWorkflowsChanged();
      onOpenBoard(cloned.id);
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Nhân bản quy trình thất bại");
    } finally {
      setCloneBusy(false);
    }
  }

  async function handleSaveTemplateSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaveTplBusy(true);
    setErr("");
    try {
      const tpl = await saveWorkflowAsTemplate(workflowId, {
        name: tplName.trim() || undefined,
        category: tplCategory.trim() || undefined,
        description: tplDesc.trim() || undefined,
      });
      setSaveTplOpen(false);
      setMsg(`Đã lưu mẫu "${tpl.name}" vào thư viện mẫu.`);
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Lưu làm mẫu thất bại");
    } finally {
      setSaveTplBusy(false);
    }
  }

  if (loading && !wfDetail) {
    return <div className="card muted">Đang tải quy trình...</div>;
  }

  if (!wfDetail) {
    return (
      <EmptyState
        title="Không tìm thấy quy trình"
        description={err || "Quy trình không tồn tại hoặc bạn không có quyền truy cập."}
        actionLabel="Quay lại danh sách quy trình"
        onAction={onBackWorkflows}
      />
    );
  }

  if (archiveMode) {
    return (
      <div className="stack" data-testid="workflow-archive-view">
        <Topbar
          title={`Lưu trữ · ${wfDetail.name}`}
          backLabel="Quay lại bảng"
          onBack={() => onOpenBoard(workflowId)}
          counter={listData ? `${listData.total} đã lưu trữ` : undefined}
          search={filter.q}
          onSearchChange={(q) => setFilter({ ...filter, q, page: 1 })}
        />
        {msg && <div className="alert ok">{msg}</div>}
        {err && <div className="alert error">{err}</div>}
        <div className="card stack">
          {!listData || listData.items.length === 0 ? (
            <EmptyState
              title="Chưa có nhiệm vụ lưu trữ"
              description="Các nhiệm vụ đã hoàn thành hoặc thất bại sau khi lưu trữ sẽ xuất hiện tại đây."
            />
          ) : (
            <div className="wf-table-scroll">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Tiêu đề</th>
                    <th>Trạng thái</th>
                    <th>Người tạo</th>
                    <th>Ngày lưu trữ</th>
                    <th>Thao tác</th>
                  </tr>
                </thead>
                <tbody>
                  {listData.items.map((it) => (
                    <tr key={it.id}>
                      <td>
                        <button
                          type="button"
                          className="btn tiny ghost"
                          onClick={() => onOpenTask(it.id)}
                          data-testid={`archive-open-${it.id}`}
                        >
                          <strong>{it.title}</strong>
                        </button>
                      </td>
                      <td><StatusPill kind="task" status={it.status} /></td>
                      <td>{it.creator.display_name}</td>
                      <td className="mono">{formatDateTime(it.archived_at)}</td>
                      <td>
                        <button
                          type="button"
                          className="btn tiny primary"
                          onClick={() => handleRestore(it.id)}
                          data-testid={`archive-restore-${it.id}`}
                        >
                          Khôi phục
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    );
  }

  const activeFiltersCount = countActiveJobFilters(filter, ["workflow_id"]);

  return (
    <div className="stack" data-testid="workflow-board-view">
      <Topbar
        title={wfDetail.name}
        subtitle={wfDetail.description || undefined}
        backLabel="Quy trình"
        onBack={onBackWorkflows}
        viewTabs={[
          { id: "board", label: "Bảng", testId: "board-tab-kanban" },
          { id: "list", label: "Danh sách", testId: "board-tab-list" },
        ]}
        activeViewTab={view}
        onViewTabChange={(next) => setView(next as "board" | "list")}
        counter={`${totalTasks} nhiệm vụ`}
        search={filter.q}
        searchPlaceholder="Tìm nhiệm vụ..."
        onSearchChange={(q) => setFilter({ ...filter, q, page: 1 })}
        filterCount={view === "list" ? activeFiltersCount : undefined}
        onOpenFilter={view === "list" ? () => setDrawerOpen(true) : undefined}
        secondaryActions={
          view === "list" ? (
            <button
              type="button"
              className="btn small"
              onClick={() => downloadCsv(`/api/jobs/export?${buildListQuery(false)}`, `${wfDetail.slug}.csv`)}
              data-testid="board-export-btn"
            >
              Xuất Excel
            </button>
          ) : undefined
        }
        primaryAction={
          wfDetail.can_create_task
            ? {
                label: "Tạo nhiệm vụ",
                onClick: () => setCreateOpen(true),
                testId: "board-create-task-btn",
              }
            : null
        }
        menuItems={[
          { id: "members", label: "Thành viên", onSelect: () => onOpenPeople(workflowId), testId: "board-menu-members" },
          { id: "archive", label: "Lưu trữ", onSelect: () => onOpenArchive(workflowId), testId: "board-menu-archive" },
          ...(wfDetail.guide_html
            ? [{ id: "guide", label: "Hướng dẫn quy trình", onSelect: () => setGuideOpen(true), testId: "board-menu-guide" }]
            : []),
          ...(wfDetail.can_manage
            ? [
                {
                  id: "edit",
                  label: "Chỉnh sửa quy trình",
                  onSelect: () => onEditWorkflow(workflowId),
                  testId: "board-menu-edit",
                },
                {
                  id: "clone",
                  label: "Nhân bản",
                  onSelect: () => {
                    setCloneName(`${wfDetail.name} (Bản sao)`);
                    setCloneOpen(true);
                  },
                  testId: "board-menu-clone",
                },
                {
                  id: "save-template",
                  label: "Lưu làm mẫu",
                  onSelect: () => {
                    setTplName(wfDetail.name);
                    setTplCategory(wfDetail.category || "");
                    setTplDesc(wfDetail.description || "");
                    setSaveTplOpen(true);
                  },
                  testId: "board-menu-save-template",
                },
                wfDetail.status === "active"
                  ? {
                      id: "close-wf",
                      label: "Đóng quy trình",
                      danger: true,
                      onSelect: handleCloseWorkflow,
                      testId: "board-menu-close",
                    }
                  : {
                      id: "reopen-wf",
                      label: "Mở lại quy trình",
                      onSelect: handleReopenWorkflow,
                      testId: "board-menu-reopen",
                    },
              ]
            : []),
        ]}
      />

      {msg && <div className="alert ok" data-testid="board-msg">{msg}</div>}
      {err && <div className="alert error" data-testid="board-err">{err}</div>}

      {wfDetail.status === "closed" && (
        <div className="alert warn" data-testid="board-closed-banner">
          Quy trình đang ở trạng thái <strong>Đã đóng</strong> — không thể tạo nhiệm vụ mới; các nhiệm vụ đang xử lý vẫn hoàn tất bình thường.
        </div>
      )}

      {wfDetail.can_manage && (wfDetail.warnings?.length || 0) > 0 && (
        <div className="alert warn stack" data-testid="board-warnings" style={{ gap: 4 }}>
          <strong>Lưu ý cấu hình bước ký ({wfDetail.warnings!.length}):</strong>
          {wfDetail.warnings!.map((w, idx) => (
            <div key={idx}>• {w.message}</div>
          ))}
        </div>
      )}

      {view === "board" && board && (
        <div className="wf-board-scroll" data-testid="wf-kanban-board">
          {board.stages.map((col) => (
            <StageColumn
              key={col.stage.id}
              kind="stage"
              stage={col.stage}
              count={col.count}
              overdueCount={col.overdue_count}
              totalTasks={totalTasks}
              tasks={filterTasks(col.tasks)}
              onHeaderClick={() => {
                setFilter({
                  ...DEFAULT_JOBS_FILTER,
                  role: "",
                  workflow_id: String(workflowId),
                  stage_id: String(col.stage.id),
                });
                setView("list");
              }}
              onOpenTask={onOpenTask}
            />
          ))}
          <StageColumn
            kind="completed"
            title="Hoàn thành"
            count={board.completed.count}
            totalTasks={totalTasks}
            tasks={filterTasks(board.completed.tasks)}
            onHeaderClick={() => {
              setFilter({
                ...DEFAULT_JOBS_FILTER,
                role: "",
                workflow_id: String(workflowId),
                status: "completed",
              });
              setView("list");
            }}
            onOpenTask={onOpenTask}
          />
          <StageColumn
            kind="failed"
            title="Thất bại"
            count={board.failed.count}
            totalTasks={totalTasks}
            tasks={filterTasks(board.failed.tasks)}
            onHeaderClick={() => {
              setFilter({
                ...DEFAULT_JOBS_FILTER,
                role: "",
                workflow_id: String(workflowId),
                status: "failed",
              });
              setView("list");
            }}
            onOpenTask={onOpenTask}
          />
        </div>
      )}

      {view === "list" && (
        <div className="card stack">
          <div className="row wrap" style={{ justifyContent: "space-between" }}>
            <div className="row wrap">
              {filter.stage_id && (
                <span className="wf-chip">
                  <span>
                    Lọc bước:{" "}
                    {wfDetail.stages.find((s) => String(s.id) === filter.stage_id)?.name || filter.stage_id}
                  </span>
                  <button
                    type="button"
                    className="wf-chip-x"
                    onClick={() => setFilter({ ...filter, stage_id: "", page: 1 })}
                  >
                    ×
                  </button>
                </span>
              )}
              {filter.status && (
                <span className="wf-chip">
                  <span>Trạng thái: {filter.status}</span>
                  <button
                    type="button"
                    className="wf-chip-x"
                    onClick={() => setFilter({ ...filter, status: "", page: 1 })}
                  >
                    ×
                  </button>
                </span>
              )}
            </div>
          </div>

          {!listData || listData.items.length === 0 ? (
            <EmptyState title="Không có nhiệm vụ nào khớp bộ lọc" />
          ) : (
            <div className="wf-table-scroll">
              <table className="tbl wf-clickable-table" data-testid="board-list-table">
                <thead>
                  <tr>
                    <th>Tiêu đề</th>
                    <th>Giai đoạn</th>
                    <th>Trạng thái</th>
                    <th>Người nhận</th>
                    <th>Thời hạn</th>
                  </tr>
                </thead>
                <tbody>
                  {listData.items.map((it) => (
                    <tr key={it.id} onClick={() => onOpenTask(it.id)}>
                      <td><strong>{it.title}</strong></td>
                      <td>{it.current_stage ? `${it.current_stage.order}. ${it.current_stage.name}` : "-"}</td>
                      <td><StatusPill kind="task" status={it.status} overdue={it.is_overdue} /></td>
                      <td>{it.assignees.map((a) => a.display_name).join(", ") || "-"}</td>
                      <td className={`mono ${it.is_overdue ? "text-danger" : ""}`}>{formatDateTime(it.deadline)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      <FilterDrawer
        open={drawerOpen}
        value={filter}
        workflows={[]}
        workflowDetail={wfDetail}
        users={users}
        fixedWorkflowId={workflowId}
        onClose={() => setDrawerOpen(false)}
        onApply={setFilter}
        onReset={() => setFilter({ ...DEFAULT_JOBS_FILTER, role: "", workflow_id: String(workflowId) })}
      />

      <CreateTaskModal
        open={createOpen}
        workflow={wfDetail}
        users={users}
        onClose={() => setCreateOpen(false)}
        onCreated={async (created) => {
          setMsg(`Đã tạo nhiệm vụ "${created.title}"`);
          onNotifyRefresh();
          await refreshActive();
          onOpenTask(created.id);
        }}
      />

      {cloneOpen && (
        <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="Nhân bản quy trình" data-testid="board-clone-modal">
          <form className="modal-card stack" onSubmit={handleCloneSubmit}>
            <div className="preview-toolbar">
              <h3 style={{ margin: 0 }}>Nhân bản quy trình</h3>
              <button type="button" className="btn tiny" disabled={cloneBusy} onClick={() => setCloneOpen(false)}>
                Đóng
              </button>
            </div>
            <label className="field">
              <span>Tên quy trình mới</span>
              <input
                value={cloneName}
                onChange={(e) => setCloneName(e.target.value)}
                required
                data-testid="board-clone-name"
              />
            </label>
            <div className="row" style={{ justifyContent: "flex-end" }}>
              <button type="button" className="btn small" disabled={cloneBusy} onClick={() => setCloneOpen(false)}>
                Hủy
              </button>
              <button type="submit" className="btn small primary" disabled={cloneBusy} data-testid="board-clone-submit">
                {cloneBusy ? "Đang nhân bản..." : "Tạo bản sao"}
              </button>
            </div>
          </form>
        </div>
      )}

      {saveTplOpen && (
        <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="Lưu quy trình thành mẫu" data-testid="board-save-template-modal">
          <form className="modal-card stack" onSubmit={handleSaveTemplateSubmit}>
            <div className="preview-toolbar">
              <h3 style={{ margin: 0 }}>Lưu quy trình làm mẫu</h3>
              <button type="button" className="btn tiny" disabled={saveTplBusy} onClick={() => setSaveTplOpen(false)}>
                Đóng
              </button>
            </div>
            <label className="field">
              <span>Tên mẫu</span>
              <input
                value={tplName}
                onChange={(e) => setTplName(e.target.value)}
                required
                data-testid="board-save-tpl-name"
              />
            </label>
            <label className="field">
              <span>Phân loại</span>
              <input
                value={tplCategory}
                onChange={(e) => setTplCategory(e.target.value)}
                data-testid="board-save-tpl-category"
              />
            </label>
            <label className="field">
              <span>Mô tả</span>
              <textarea
                rows={2}
                value={tplDesc}
                onChange={(e) => setTplDesc(e.target.value)}
                data-testid="board-save-tpl-desc"
              />
            </label>
            <div className="row" style={{ justifyContent: "flex-end" }}>
              <button type="button" className="btn small" disabled={saveTplBusy} onClick={() => setSaveTplOpen(false)}>
                Hủy
              </button>
              <button type="submit" className="btn small primary" disabled={saveTplBusy} data-testid="board-save-tpl-submit">
                {saveTplBusy ? "Đang lưu..." : "Lưu mẫu"}
              </button>
            </div>
          </form>
        </div>
      )}

      {guideOpen && (
        <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="Hướng dẫn quy trình">
          <div className="modal-card wide stack">
            <div className="preview-toolbar">
              <h3 style={{ margin: 0 }}>Hướng dẫn · {wfDetail.name}</h3>
              <button type="button" className="btn tiny" onClick={() => setGuideOpen(false)}>Đóng</button>
            </div>
            <div className="wf-modal-scroll stack">
              <SafeHtml html={wfDetail.guide_html} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
