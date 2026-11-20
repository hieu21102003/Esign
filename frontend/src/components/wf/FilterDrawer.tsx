import React, { useEffect, useState } from "react";
import type { AssignableUser } from "../../lib/api";
import type { JobsFilterState } from "../../lib/router";
import type { WfDetail, WfSummary } from "../../lib/wfApi";
import { DateRangePicker } from "./DateRangePicker";

interface Props {
  open: boolean;
  value: JobsFilterState;
  workflows: WfSummary[];
  workflowDetail: WfDetail | null;
  users: AssignableUser[];
  fixedWorkflowId?: number;
  onClose: () => void;
  onApply: (next: JobsFilterState) => void;
  onReset: () => void;
}

export function FilterDrawer({
  open,
  value,
  workflows,
  workflowDetail,
  users,
  fixedWorkflowId,
  onClose,
  onApply,
  onReset,
}: Props) {
  const [draft, setDraft] = useState<JobsFilterState>(value);

  useEffect(() => {
    if (open) setDraft(value);
  }, [open, value]);

  if (!open) return null;

  const stages = workflowDetail?.stages || [];

  return (
    <div className="wf-drawer-backdrop" role="dialog" aria-modal="true" aria-label="Bộ lọc nâng cao" data-testid="filter-drawer">
      <div className="wf-drawer-panel stack">
        <div className="preview-toolbar">
          <h3 style={{ margin: 0 }}>Bộ lọc nâng cao</h3>
          <button type="button" className="btn tiny" onClick={onClose} data-testid="filter-drawer-close">
            Đóng
          </button>
        </div>

        <div className="wf-drawer-body stack">
          <label className="field">
            <span>Tên / nhãn / mô tả</span>
            <input
              value={draft.q}
              onChange={(e) => setDraft({ ...draft, q: e.target.value })}
              placeholder="Từ khóa..."
              data-testid="filter-q"
            />
          </label>

          {!fixedWorkflowId && (
            <label className="field">
              <span>Quy trình</span>
              <select
                value={draft.workflow_id}
                onChange={(e) => setDraft({ ...draft, workflow_id: e.target.value, stage_id: "" })}
                data-testid="filter-workflow"
              >
                <option value="">Tất cả quy trình</option>
                {workflows.map((w) => (
                  <option key={w.id} value={String(w.id)}>
                    {w.name}
                  </option>
                ))}
              </select>
            </label>
          )}

          {stages.length > 0 && (
            <label className="field">
              <span>Giai đoạn</span>
              <select
                value={draft.stage_id}
                onChange={(e) => setDraft({ ...draft, stage_id: e.target.value })}
                data-testid="filter-stage"
              >
                <option value="">Tất cả giai đoạn</option>
                {stages.map((s) => (
                  <option key={s.id} value={String(s.id)}>
                    Bước {s.order}: {s.name}
                  </option>
                ))}
              </select>
            </label>
          )}

          <label className="field">
            <span>Trạng thái</span>
            <select
              value={draft.status}
              onChange={(e) => setDraft({ ...draft, status: e.target.value })}
              data-testid="filter-status"
            >
              <option value="">Tất cả</option>
              <option value="processing">Đang xử lý</option>
              <option value="completed">Hoàn thành</option>
              <option value="failed">Thất bại</option>
              <option value="overdue">Quá hạn</option>
            </select>
          </label>

          <div className="two-col">
            <label className="field">
              <span>Người tạo</span>
              <select
                value={draft.creator_id}
                onChange={(e) => setDraft({ ...draft, creator_id: e.target.value })}
                data-testid="filter-creator"
              >
                <option value="">Tất cả</option>
                {users.map((u) => (
                  <option key={u.id} value={String(u.id)}>
                    {u.display_name || u.username}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Người nhận hiện tại</span>
              <select
                value={draft.assignee_id}
                onChange={(e) => setDraft({ ...draft, assignee_id: e.target.value })}
                data-testid="filter-assignee"
              >
                <option value="">Tất cả</option>
                {users.map((u) => (
                  <option key={u.id} value={String(u.id)}>
                    {u.display_name || u.username}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <DateRangePicker
            label="Khoảng ngày thời hạn"
            from={draft.deadline_from}
            to={draft.deadline_to}
            onChange={(from, to) => setDraft({ ...draft, deadline_from: from, deadline_to: to })}
            testIdPrefix="filter-deadline"
          />
          <DateRangePicker
            label="Khoảng ngày hoàn thành"
            from={draft.completed_from}
            to={draft.completed_to}
            onChange={(from, to) => setDraft({ ...draft, completed_from: from, completed_to: to })}
            testIdPrefix="filter-completed"
          />
          <DateRangePicker
            label="Khoảng ngày tạo"
            from={draft.created_from}
            to={draft.created_to}
            onChange={(from, to) => setDraft({ ...draft, created_from: from, created_to: to })}
            testIdPrefix="filter-created"
          />

          <label className="field">
            <span>Sắp xếp</span>
            <select
              value={draft.sort}
              onChange={(e) => setDraft({ ...draft, sort: e.target.value })}
              data-testid="filter-sort"
            >
              <option value="-created_at">Mới tạo nhất</option>
              <option value="created_at">Tạo cũ nhất</option>
              <option value="deadline">Hạn xử lý gần nhất</option>
              <option value="-deadline">Hạn xử lý xa nhất</option>
              <option value="-updated_at">Cập nhật gần nhất</option>
              <option value="-completed_at">Hoàn thành gần nhất</option>
              <option value="title">Tiêu đề A → Z</option>
            </select>
          </label>
        </div>

        <div className="row" style={{ justifyContent: "space-between", paddingTop: 8, borderTop: "1px solid var(--border)" }}>
          <button
            type="button"
            className="btn small"
            onClick={() => {
              onReset();
              onClose();
            }}
            data-testid="filter-reset-btn"
          >
            Xóa bộ lọc
          </button>
          <button
            type="button"
            className="btn small primary"
            onClick={() => {
              onApply({ ...draft, page: 1 });
              onClose();
            }}
            data-testid="filter-apply-btn"
          >
            Áp dụng
          </button>
        </div>
      </div>
    </div>
  );
}
