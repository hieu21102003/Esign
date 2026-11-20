import React, { useState } from "react";
import type { AssignableUser } from "../../lib/api";
import type { AssignMode, StageType, WfBuilderStageDraft } from "../../lib/wfApi";
import { RichTextEditor } from "./RichTextEditor";
import { StatusPill } from "./StatusPill";
import { UserPicker } from "./UserPicker";

interface Props {
  stages: WfBuilderStageDraft[];
  users: AssignableUser[];
  isEdit: boolean;
  onChange: (next: WfBuilderStageDraft[]) => void;
}

export function createEmptyStageDraft(index: number): WfBuilderStageDraft {
  return {
    localKey: `stg-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    name: `Bước ${index}`,
    type: "normal",
    sla_hours: 8,
    assign_mode: "all",
    guide_html: "",
    member_ids: [],
    is_active: true,
  };
}

export function StageBuilder({ stages, users, isEdit, onChange }: Props) {
  const [openGuides, setOpenGuides] = useState<Record<string, boolean>>({});

  const activeStages = stages.filter((s) => s.is_active !== false);
  const inactiveStages = stages.filter((s) => s.is_active === false);

  const totalSla = activeStages.reduce(
    (sum, s) => sum + (s.sla_hours && s.sla_hours > 0 ? s.sla_hours : 0),
    0,
  );

  function commit(nextActive: WfBuilderStageDraft[], nextInactive: WfBuilderStageDraft[] = inactiveStages) {
    onChange([...nextActive, ...nextInactive]);
  }

  function updateStage(localKey: string, patch: Partial<WfBuilderStageDraft>) {
    onChange(stages.map((s) => (s.localKey === localKey ? { ...s, ...patch } : s)));
  }

  function moveActive(idx: number, delta: -1 | 1) {
    const target = idx + delta;
    if (target < 0 || target >= activeStages.length) return;
    const copy = [...activeStages];
    const tmp = copy[idx];
    copy[idx] = copy[target];
    copy[target] = tmp;
    commit(copy);
  }

  function insertAfter(idx: number) {
    const item = createEmptyStageDraft(activeStages.length + 1);
    const copy = [...activeStages.slice(0, idx + 1), item, ...activeStages.slice(idx + 1)];
    commit(copy);
  }

  function appendEnd() {
    commit([...activeStages, createEmptyStageDraft(activeStages.length + 1)]);
  }

  function removeOrDeactivate(st: WfBuilderStageDraft) {
    if (st.id && st.has_history) {
      const nextActive = activeStages.filter((x) => x.localKey !== st.localKey);
      const nextInactive = [...inactiveStages, { ...st, is_active: false }];
      commit(nextActive, nextInactive);
      return;
    }
    commit(activeStages.filter((x) => x.localKey !== st.localKey));
  }

  function reactivateStage(st: WfBuilderStageDraft) {
    const nextInactive = inactiveStages.filter((x) => x.localKey !== st.localKey);
    commit([...activeStages, { ...st, is_active: true }], nextInactive);
  }

  return (
    <div className="card stack" data-testid="stage-builder">
      <div className="row wrap" style={{ justifyContent: "space-between" }}>
        <div>
          <h3 style={{ margin: 0 }}>Các giai đoạn quy trình ({activeStages.length})</h3>
          <div className="muted">
            Tổng thời hạn SLA toàn luồng: <strong>{totalSla} giờ</strong>
          </div>
        </div>
        <button
          type="button"
          className="btn small primary"
          onClick={appendEnd}
          data-testid="stage-add-btn"
        >
          + Thêm giai đoạn cuối
        </button>
      </div>

      {isEdit && activeStages.some((s) => (s.processing_count || 0) > 0) && (
        <div className="muted">
          Lưu ý: Quy trình đang có công việc xử lý. Thay đổi tên/SLA/hướng dẫn được áp dụng ngay; đổi thứ tự, đổi loại stage hoặc chèn trước bước đang chạy sẽ bị máy chủ từ chối (409).
        </div>
      )}

      <div className="stack">
        {activeStages.map((st, idx) => {
          const guideOpen = Boolean(openGuides[st.localKey] || st.guide_html.trim());
          return (
            <div key={st.localKey} className="wf-builder-stage-card stack" data-testid={`stage-card-${idx}`}>
              <div className="row wrap" style={{ justifyContent: "space-between" }}>
                <div className="row wrap">
                  <span className="badge">Bước {idx + 1}</span>
                  <StatusPill kind="stage" status={st.type} />
                  {st.processing_count !== undefined && st.processing_count > 0 && (
                    <span className="badge mode">Đang xử lý: {st.processing_count}</span>
                  )}
                  {st.has_history && <span className="badge muted">Có lịch sử</span>}
                </div>
                <div className="row wrap" style={{ gap: 6 }}>
                  <button
                    type="button"
                    className="btn tiny"
                    disabled={idx === 0}
                    onClick={() => moveActive(idx, -1)}
                    aria-label={`Đưa bước ${idx + 1} lên`}
                    data-testid={`stage-up-${idx}`}
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    className="btn tiny"
                    disabled={idx === activeStages.length - 1}
                    onClick={() => moveActive(idx, 1)}
                    aria-label={`Đưa bước ${idx + 1} xuống`}
                    data-testid={`stage-down-${idx}`}
                  >
                    ↓
                  </button>
                  <button
                    type="button"
                    className="btn tiny"
                    onClick={() => insertAfter(idx)}
                    data-testid={`stage-insert-${idx}`}
                  >
                    + Chèn sau
                  </button>
                  <button
                    type="button"
                    className="btn tiny danger"
                    disabled={activeStages.length <= 1}
                    onClick={() => removeOrDeactivate(st)}
                    data-testid={`stage-remove-${idx}`}
                  >
                    {st.has_history ? "Vô hiệu hóa" : "Xóa"}
                  </button>
                </div>
              </div>

              <div className="wf-builder-stage-grid">
                <label className="field">
                  <span>Tên giai đoạn *</span>
                  <input
                    value={st.name}
                    onChange={(e) => updateStage(st.localKey, { name: e.target.value })}
                    placeholder="Ví dụ: Giám đốc ký duyệt"
                    data-testid={`stage-name-${idx}`}
                  />
                </label>
                <label className="field">
                  <span>Loại bước</span>
                  <select
                    value={st.type}
                    onChange={(e) => updateStage(st.localKey, { type: e.target.value as StageType })}
                    data-testid={`stage-type-${idx}`}
                  >
                    <option value="normal">Thường (normal)</option>
                    <option value="approve">Phê duyệt (approve)</option>
                    <option value="sign">Ký số PDF (sign)</option>
                  </select>
                </label>
                <label className="field">
                  <span>SLA (giờ)</span>
                  <input
                    type="number"
                    min={1}
                    max={8760}
                    value={st.sla_hours ?? ""}
                    onChange={(e) => {
                      const raw = e.target.value.trim();
                      updateStage(st.localKey, { sla_hours: raw === "" ? null : Number(raw) });
                    }}
                    placeholder="Không giới hạn"
                    data-testid={`stage-sla-${idx}`}
                  />
                </label>
                <label className="field">
                  <span>Chế độ hoàn thành</span>
                  <select
                    value={st.assign_mode}
                    onChange={(e) => updateStage(st.localKey, { assign_mode: e.target.value as AssignMode })}
                    data-testid={`stage-mode-${idx}`}
                  >
                    <option value="all">Tất cả thành viên (all)</option>
                    <option value="any">Bất kỳ 1 người (any)</option>
                  </select>
                </label>
              </div>

              <div className="field">
                <span>
                  Thành viên thực hiện giai đoạn ({st.member_ids.length} đã chọn)
                  {st.type === "sign" && <span className="muted"> · Bước ký số cần có chứng thư và PIN</span>}
                </span>
                <UserPicker
                  options={users}
                  selectedIds={st.member_ids}
                  onChange={(ids) => updateStage(st.localKey, { member_ids: ids })}
                  multiple
                  testIdPrefix={`stage-member-${idx}`}
                />
              </div>

              <div>
                <button
                  type="button"
                  className="btn tiny ghost"
                  onClick={() => setOpenGuides((prev) => ({ ...prev, [st.localKey]: !guideOpen }))}
                  data-testid={`stage-guide-toggle-${idx}`}
                >
                  {guideOpen ? "Thu gọn hướng dẫn bước" : "+ Thêm hướng dẫn bước"}
                </button>
                {guideOpen && (
                  <div style={{ marginTop: 6 }}>
                    <RichTextEditor
                      value={st.guide_html}
                      onChange={(html) => updateStage(st.localKey, { guide_html: html })}
                      label={`Hướng dẫn cho "${st.name || `Bước ${idx + 1}`}"`}
                      rows={3}
                      testIdPrefix={`stage-guide-${idx}`}
                    />
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {inactiveStages.length > 0 && (
        <div className="stack" style={{ marginTop: 8 }}>
          <strong className="muted">Giai đoạn đã vô hiệu hóa ({inactiveStages.length}) — chỉ giữ để hiển thị công việc lịch sử</strong>
          {inactiveStages.map((st, idx) => (
            <div key={st.localKey} className="wf-inactive-row">
              <div className="row wrap">
                <span className="badge muted">Đã vô hiệu</span>
                <strong>{st.name}</strong>
                <StatusPill kind="stage" status={st.type} />
              </div>
              <button
                type="button"
                className="btn tiny"
                onClick={() => reactivateStage(st)}
                data-testid={`stage-reactivate-${st.id || idx}`}
              >
                Kích hoạt lại
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
