import React, { useCallback, useEffect, useState } from "react";
import { apiJson, type AssignableUser, type MeResponse } from "../../lib/api";
import {
  createWorkflow,
  patchWorkflow,
  validateWorkflowDraft,
  type WfBuilderDraft,
  type WfBuilderFieldDraft,
  type WfBuilderReasonDraft,
  type WfBuilderRoleDraft,
  type WfBuilderStageDraft,
  type WfDetail,
  type WfTemplate,
  type WfWarning,
  type WorkflowStatusKind,
  type WorkflowVisibility,
} from "../../lib/wfApi";
import { FormFieldBuilder } from "./FormFieldBuilder";
import { RichTextEditor } from "./RichTextEditor";
import { createEmptyStageDraft, StageBuilder } from "./StageBuilder";
import { TemplateGalleryModal } from "./TemplateGalleryModal";
import { Topbar } from "./Topbar";

interface Props {
  mode: "create" | "edit";
  me: MeResponse;
  workflowId?: number;
  initialTemplate?: WfTemplate | null;
  onBack: () => void;
  onOpenBoard: (workflowId: number) => void;
  onOpenPeople: (workflowId: number) => void;
  onWorkflowsChanged: () => void;
}

const ROLE_LABELS: Record<WfBuilderRoleDraft["role"], string> = {
  admin: "Quản trị quy trình (admin)",
  supervisor: "Giám sát chỉ đọc (supervisor)",
  creator: "Người tạo việc (creator)",
  viewer: "Người xem (viewer)",
};

function makeEmptyDraft(creatorId: number): WfBuilderDraft {
  return {
    name: "",
    slug: "",
    category: "",
    description: "",
    guide_html: "",
    visibility: "followers_only",
    status: "active",
    stages: [createEmptyStageDraft(1)],
    form_fields: [],
    roles: [{ user_id: creatorId, role: "admin" }],
    failure_reasons: [],
  };
}

function draftFromTemplate(tpl: WfTemplate, creatorId: number): WfBuilderDraft {
  const stamp = Date.now();
  return {
    name: tpl.name,
    slug: "",
    category: tpl.category || "",
    description: tpl.description || "",
    guide_html: tpl.definition.guide_html || "",
    visibility: tpl.definition.visibility || "followers_only",
    status: "active",
    stages: tpl.definition.stages.map(
      (s, i): WfBuilderStageDraft => ({
        localKey: `tpl-stg-${stamp}-${i}`,
        name: s.name,
        type: s.type,
        sla_hours: s.sla_hours,
        assign_mode: s.assign_mode,
        guide_html: s.guide_html || "",
        member_ids: [],
        is_active: true,
      }),
    ),
    form_fields: tpl.definition.form_fields.map(
      (f, i): WfBuilderFieldDraft => ({
        localKey: `tpl-fld-${stamp}-${i}`,
        label: f.label,
        type: f.type,
        required: Boolean(f.required),
        placeholder: f.placeholder || "",
        options: Array.isArray(f.options) ? [...f.options] : [],
        is_active: true,
      }),
    ),
    roles: [{ user_id: creatorId, role: "admin" }],
    failure_reasons: tpl.definition.failure_reasons.map(
      (lbl, i): WfBuilderReasonDraft => ({
        localKey: `tpl-rsn-${stamp}-${i}`,
        label: lbl,
      }),
    ),
  };
}

function draftFromDetail(det: WfDetail): WfBuilderDraft {
  return {
    name: det.name,
    slug: det.slug,
    category: det.category || "",
    description: det.description || "",
    guide_html: det.guide_html || "",
    visibility: det.visibility,
    status: det.status,
    stages: det.stages.map(
      (s): WfBuilderStageDraft => ({
        localKey: `stg-id-${s.id}`,
        id: s.id,
        name: s.name,
        type: s.type,
        sla_hours: s.sla_hours,
        assign_mode: s.assign_mode,
        guide_html: s.guide_html || "",
        member_ids: (s.members || []).map((m) => m.id).filter((id): id is number => typeof id === "number"),
        is_active: s.is_active !== false,
        processing_count: s.processing_count,
        has_history: s.has_history,
      }),
    ),
    form_fields: det.form_fields.map(
      (f): WfBuilderFieldDraft => ({
        localKey: `fld-id-${f.id}`,
        id: f.id,
        label: f.label,
        type: f.type,
        required: f.required,
        placeholder: f.placeholder || "",
        options: Array.isArray(f.options) ? [...f.options] : [],
        is_active: f.is_active !== false,
        has_data: f.has_data,
      }),
    ),
    roles: (det.roles || [])
      .map((r) => ({ user_id: r.user.id, role: r.role }))
      .filter((r): r is WfBuilderRoleDraft => typeof r.user_id === "number"),
    failure_reasons: det.failure_reasons.map((r) => ({
      localKey: `rsn-id-${r.id}`,
      id: r.id,
      label: r.label,
    })),
  };
}

export function WorkflowBuilderView({
  mode,
  me,
  workflowId,
  initialTemplate,
  onBack,
  onOpenBoard,
  onOpenPeople,
  onWorkflowsChanged,
}: Props) {
  const isEdit = mode === "edit";
  const [draft, setDraft] = useState<WfBuilderDraft>(() =>
    mode === "create" && initialTemplate ? draftFromTemplate(initialTemplate, me.id) : makeEmptyDraft(me.id),
  );
  const [users, setUsers] = useState<AssignableUser[]>([]);
  const [wfDetail, setWfDetail] = useState<WfDetail | null>(null);
  const [warnings, setWarnings] = useState<WfWarning[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [tplOpen, setTplOpen] = useState(false);
  const [newRoleUserId, setNewRoleUserId] = useState<string>("");
  const [newRoleKind, setNewRoleKind] = useState<WfBuilderRoleDraft["role"]>("creator");

  const loadData = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const uList = await apiJson<AssignableUser[]>("/api/users/assignable");
      setUsers(uList);
      if (uList.length > 0) {
        setNewRoleUserId((prev) => prev || String(uList[0].id));
      }
      if (isEdit && workflowId) {
        const det = await apiJson<WfDetail>(`/api/workflows/${workflowId}`);
        setWfDetail(det);
        setWarnings(det.warnings || []);
        setDraft(draftFromDetail(det));
      } else if (!isEdit) {
        setWfDetail(null);
        setWarnings([]);
        setDraft(initialTemplate ? draftFromTemplate(initialTemplate, me.id) : makeEmptyDraft(me.id));
      }
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Không tải được dữ liệu thiết kế quy trình");
    } finally {
      setLoading(false);
    }
  }, [isEdit, workflowId, initialTemplate, me.id]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const userById = new Map(users.map((u) => [u.id, u]));

  function addRole() {
    const uid = Number(newRoleUserId);
    if (!uid) return;
    if (draft.roles.some((r) => r.user_id === uid && r.role === newRoleKind)) return;
    setDraft((prev) => ({
      ...prev,
      roles: [...prev.roles, { user_id: uid, role: newRoleKind }],
    }));
  }

  function removeRole(uid: number, role: WfBuilderRoleDraft["role"]) {
    setDraft((prev) => ({
      ...prev,
      roles: prev.roles.filter((r) => !(r.user_id === uid && r.role === role)),
    }));
  }

  function addReason() {
    setDraft((prev) => ({
      ...prev,
      failure_reasons: [
        ...prev.failure_reasons,
        { localKey: `rsn-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`, label: "" },
      ],
    }));
  }

  function buildPayload(targetStatus: WorkflowStatusKind) {
    const activeStages = draft.stages.filter((s) => s.is_active !== false);
    const inactiveStages = draft.stages.filter((s) => s.is_active === false && s.id !== undefined);
    const orderedStages = [...activeStages, ...inactiveStages];

    return {
      name: draft.name.trim(),
      ...(!isEdit && draft.slug.trim() ? { slug: draft.slug.trim().toLowerCase() } : {}),
      category: draft.category.trim(),
      description: draft.description.trim(),
      guide_html: draft.guide_html,
      visibility: draft.visibility,
      status: targetStatus,
      stages: orderedStages.map((s) => ({
        ...(s.id !== undefined ? { id: s.id } : {}),
        name: s.name.trim(),
        type: s.type,
        sla_hours: s.sla_hours || null,
        guide_html: s.guide_html,
        assign_mode: s.assign_mode,
        member_ids: s.member_ids,
        is_active: s.is_active !== false,
      })),
      form_fields: draft.form_fields.map((f) => ({
        ...(f.id !== undefined ? { id: f.id } : {}),
        label: f.label.trim(),
        type: f.type,
        required: f.required,
        placeholder: f.placeholder.trim(),
        options: f.type === "select" ? f.options.map((o) => o.trim()).filter(Boolean) : null,
        is_active: f.is_active !== false,
      })),
      roles: draft.roles.map((r) => ({ user_id: r.user_id, role: r.role })),
      failure_reasons: draft.failure_reasons
        .filter((r) => r.label.trim() !== "")
        .map((r) => ({
          ...(r.id !== undefined ? { id: r.id } : {}),
          label: r.label.trim(),
        })),
    };
  }

  async function handleSave(targetStatus: WorkflowStatusKind) {
    setErr("");
    setMsg("");
    const checkDraft: WfBuilderDraft = { ...draft, status: targetStatus };
    const errs = validateWorkflowDraft(checkDraft, isEdit);
    if (errs.length > 0) {
      setErr(errs.join(" "));
      return;
    }

    setSaving(true);
    try {
      const payload = buildPayload(targetStatus);
      if (isEdit && workflowId) {
        const updated = await patchWorkflow(workflowId, payload);
        setWfDetail(updated);
        setWarnings(updated.warnings || []);
        setDraft(draftFromDetail(updated));
        setMsg("Đã lưu thay đổi quy trình thành công.");
        onWorkflowsChanged();
      } else {
        const created = await createWorkflow(payload);
        onWorkflowsChanged();
        onOpenBoard(created.id);
      }
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Lưu quy trình thất bại");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <div className="card muted">Đang tải trình thiết kế quy trình...</div>;
  }

  if (isEdit && wfDetail && !wfDetail.can_manage) {
    return (
      <div className="stack" data-testid="workflow-builder-readonly">
        <Topbar
          title={`Xem cấu trúc · ${wfDetail.name}`}
          backLabel="Quay lại bảng"
          onBack={() => onOpenBoard(wfDetail.id)}
        />
        <div className="alert error" role="alert">
          Chỉ quản trị viên của quy trình mới được chỉnh sửa cấu trúc. Bạn đang ở vai trò chỉ đọc.
        </div>
      </div>
    );
  }

  return (
    <div className="stack" data-testid="workflow-builder-view">
      <Topbar
        title={isEdit ? `Chỉnh sửa · ${wfDetail?.name || draft.name}` : "Thiết kế quy trình mới"}
        backLabel={isEdit && workflowId ? "Bảng quy trình" : "Quản lý quy trình"}
        onBack={() => (isEdit && workflowId ? onOpenBoard(workflowId) : onBack())}
        secondaryActions={
          <div className="row wrap" style={{ gap: 8 }}>
            {!isEdit && (
              <button
                type="button"
                className="btn small"
                onClick={() => setTplOpen(true)}
                data-testid="wf-builder-open-templates"
              >
                Chọn từ mẫu
              </button>
            )}
            {isEdit && workflowId && (
              <button
                type="button"
                className="btn small"
                onClick={() => onOpenPeople(workflowId)}
                data-testid="wf-builder-open-people"
              >
                Thành viên &amp; Vai trò
              </button>
            )}
          </div>
        }
        primaryAction={{
          label: saving ? "Đang lưu..." : isEdit ? "Lưu thay đổi" : "Lưu & kích hoạt",
          disabled: saving,
          onClick: () => handleSave(isEdit ? draft.status : "active"),
          testId: isEdit ? "wf-builder-save" : "wf-builder-save-active",
        }}
      />

      {msg && (
        <div className="alert ok" data-testid="wf-builder-msg">
          {msg}
        </div>
      )}
      {err && (
        <div className="alert error" role="alert" data-testid="wf-builder-error">
          {err}
        </div>
      )}

      {warnings.length > 0 && (
        <div className="alert warn stack" data-testid="wf-builder-warnings" style={{ gap: 4 }}>
          <strong>Cảnh báo cấu hình bước ký (không chặn lưu):</strong>
          {warnings.map((w, idx) => (
            <div key={idx}>• {w.message}</div>
          ))}
        </div>
      )}

      <div className="card stack" data-testid="wf-builder-meta">
        <h3 style={{ margin: 0 }}>Thông tin chung</h3>
        <div className="two-col">
          <label className="field">
            <span>Tên quy trình *</span>
            <input
              value={draft.name}
              onChange={(e) => setDraft((p) => ({ ...p, name: e.target.value }))}
              placeholder="Ví dụ: Quy trình trình ký hợp đồng"
              data-testid="wf-builder-name"
            />
          </label>
          <label className="field">
            <span>Mã slug (chữ thường, a-z 0-9 và -)</span>
            <input
              value={draft.slug}
              disabled={isEdit}
              onChange={(e) => setDraft((p) => ({ ...p, slug: e.target.value }))}
              placeholder={isEdit ? "" : "Để trống để tự sinh theo tên"}
              data-testid="wf-builder-slug"
            />
          </label>
          <label className="field">
            <span>Phân loại</span>
            <input
              value={draft.category}
              onChange={(e) => setDraft((p) => ({ ...p, category: e.target.value }))}
              placeholder="Ví dụ: Hành chính, Ký duyệt, Tài chính"
              data-testid="wf-builder-category"
            />
          </label>
          <label className="field">
            <span>Quyền hiển thị công việc</span>
            <select
              value={draft.visibility}
              onChange={(e) => setDraft((p) => ({ ...p, visibility: e.target.value as WorkflowVisibility }))}
              data-testid="wf-builder-visibility"
            >
              <option value="followers_only">Chỉ người tạo, người thực hiện, theo dõi và admin/giám sát thấy</option>
              <option value="all_visible">Mọi thành viên có vai trò trong quy trình đều thấy</option>
            </select>
          </label>
        </div>

        <label className="field">
          <span>Mô tả ngắn</span>
          <textarea
            rows={2}
            value={draft.description}
            onChange={(e) => setDraft((p) => ({ ...p, description: e.target.value }))}
            placeholder="Mục đích và phạm vi áp dụng của quy trình..."
            data-testid="wf-builder-description"
          />
        </label>

        <RichTextEditor
          value={draft.guide_html}
          onChange={(html) => setDraft((p) => ({ ...p, guide_html: html }))}
          label="Hướng dẫn thực hiện quy trình (HTML allowlist an toàn)"
          rows={4}
          testIdPrefix="wf-builder-guide"
        />
      </div>

      <StageBuilder
        stages={draft.stages}
        users={users}
        isEdit={isEdit}
        onChange={(stages) => setDraft((p) => ({ ...p, stages }))}
      />

      <FormFieldBuilder
        fields={draft.form_fields}
        users={users}
        onChange={(form_fields) => setDraft((p) => ({ ...p, form_fields }))}
      />

      <div className="two-col" style={{ alignItems: "start" }}>
        <div className="card stack" data-testid="wf-builder-roles">
          <h3 style={{ margin: 0 }}>Vai trò trong quy trình</h3>
          <div className="row wrap">
            <select
              value={newRoleUserId}
              onChange={(e) => setNewRoleUserId(e.target.value)}
              style={{ flex: 1, minWidth: 160 }}
              data-testid="wf-role-user-select"
            >
              {users.map((u) => (
                <option key={u.id} value={String(u.id)}>
                  {u.display_name || u.username} (@{u.username})
                </option>
              ))}
            </select>
            <select
              value={newRoleKind}
              onChange={(e) => setNewRoleKind(e.target.value as WfBuilderRoleDraft["role"])}
              data-testid="wf-role-kind-select"
            >
              <option value="admin">admin</option>
              <option value="supervisor">supervisor</option>
              <option value="creator">creator</option>
              <option value="viewer">viewer</option>
            </select>
            <button type="button" className="btn small" onClick={addRole} data-testid="wf-role-add-btn">
              + Thêm vai trò
            </button>
          </div>
          {draft.roles.length === 0 ? (
            <div className="muted">Chưa gán vai trò (người tạo mặc định là admin).</div>
          ) : (
            <div className="stack" style={{ gap: 6 }}>
              {draft.roles.map((r, idx) => {
                const u = userById.get(r.user_id);
                return (
                  <div key={`${r.user_id}-${r.role}-${idx}`} className="wf-role-row">
                    <div>
                      <strong>{u ? u.display_name || u.username : `#${r.user_id}`}</strong>
                      {u && <span className="muted"> (@{u.username})</span>}
                      <div>
                        <span className="badge muted">{ROLE_LABELS[r.role]}</span>
                      </div>
                    </div>
                    <button
                      type="button"
                      className="btn tiny danger"
                      onClick={() => removeRole(r.user_id, r.role)}
                      data-testid={`wf-role-remove-${r.user_id}-${r.role}`}
                    >
                      Gỡ
                    </button>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className="card stack" data-testid="wf-builder-reasons">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h3 style={{ margin: 0 }}>Lý do thất bại / từ chối</h3>
            <button type="button" className="btn small" onClick={addReason} data-testid="wf-reason-add-btn">
              + Thêm lý do
            </button>
          </div>
          {draft.failure_reasons.length === 0 ? (
            <div className="muted">Chưa khai báo lý do mẫu.</div>
          ) : (
            <div className="stack" style={{ gap: 6 }}>
              {draft.failure_reasons.map((r, idx) => (
                <div key={r.localKey} className="row">
                  <input
                    value={r.label}
                    onChange={(e) => {
                      const lbl = e.target.value;
                      setDraft((p) => ({
                        ...p,
                        failure_reasons: p.failure_reasons.map((x) =>
                          x.localKey === r.localKey ? { ...x, label: lbl } : x,
                        ),
                      }));
                    }}
                    placeholder={`Lý do #${idx + 1}`}
                    data-testid={`wf-reason-input-${idx}`}
                  />
                  <button
                    type="button"
                    className="btn tiny danger"
                    onClick={() =>
                      setDraft((p) => ({
                        ...p,
                        failure_reasons: p.failure_reasons.filter((x) => x.localKey !== r.localKey),
                      }))
                    }
                    data-testid={`wf-reason-remove-${idx}`}
                  >
                    Xóa
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="card row wrap" style={{ justifyContent: "space-between" }}>
        <button
          type="button"
          className="btn small"
          disabled={saving}
          onClick={() => (isEdit && workflowId ? onOpenBoard(workflowId) : onBack())}
        >
          Hủy
        </button>
        <div className="row wrap">
          {!isEdit && (
            <button
              type="button"
              className="btn small"
              disabled={saving}
              onClick={() => handleSave("closed")}
              data-testid="wf-builder-save-draft"
            >
              Lưu nháp
            </button>
          )}
          <button
            type="button"
            className="btn small primary"
            disabled={saving}
            onClick={() => handleSave(isEdit ? draft.status : "active")}
            data-testid={isEdit ? "wf-builder-save-bottom" : "wf-builder-save-active-bottom"}
          >
            {saving ? "Đang lưu..." : isEdit ? "Lưu thay đổi" : "Lưu & kích hoạt"}
          </button>
        </div>
      </div>

      <TemplateGalleryModal
        open={tplOpen}
        onClose={() => setTplOpen(false)}
        onUseTemplate={(tpl) => {
          setDraft(draftFromTemplate(tpl, me.id));
          setTplOpen(false);
          setMsg(`Đã nạp mẫu "${tpl.name}". Hãy chọn thành viên cho từng giai đoạn trước khi kích hoạt.`);
        }}
      />
    </div>
  );
}
