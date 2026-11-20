import React, { useEffect, useState } from "react";
import { apiJson, type AssignableUser, type StoredFile } from "../../lib/api";
import {
  formatDateTime,
  validateDynamicForm,
  type WfDetail,
  type WfFormField,
  type WfTaskDetail,
} from "../../lib/wfApi";
import { FileUploader } from "./FileUploader";
import { LabelInput } from "./LabelInput";
import { UserPicker } from "./UserPicker";

interface Props {
  open: boolean;
  workflow: WfDetail;
  users: AssignableUser[];
  onClose: () => void;
  onCreated: (task: WfTaskDetail) => void;
}

export function CreateTaskModal({ open, workflow, users, onClose, onCreated }: Props) {
  const activeStages = workflow.stages.filter((s) => s.is_active !== false);
  const activeFields = workflow.form_fields.filter((f) => f.is_active !== false);
  const firstStage = activeStages[0];
  const hasSignStage = activeStages.some((s) => s.type === "sign");

  const [title, setTitle] = useState("");
  const [labels, setLabels] = useState<string[]>([]);
  const [assigneeIds, setAssigneeIds] = useState<number[]>([]);
  const [followerIds, setFollowerIds] = useState<number[]>([]);
  const [dueHint, setDueHint] = useState("");
  const [description, setDescription] = useState("");
  const [customValues, setCustomValues] = useState<Record<string, unknown>>({});
  const [fieldFiles, setFieldFiles] = useState<Record<string, StoredFile[]>>({});
  const [attachments, setAttachments] = useState<StoredFile[]>([]);
  const [reuseFormFileForSign, setReuseFormFileForSign] = useState(true);
  const [fieldErrs, setFieldErrs] = useState<Record<string, string>>({});
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setTitle("");
    setLabels([]);
    setAssigneeIds([]);
    setFollowerIds([]);
    setDueHint("");
    setDescription("");
    setCustomValues({});
    setFieldFiles({});
    setAttachments([]);
    setReuseFormFileForSign(true);
    setFieldErrs({});
    setErr("");
  }, [open, workflow.id]);

  if (!open) return null;

  const firstMembers = firstStage?.members || [];
  const slaPreview =
    firstStage?.sla_hours !== null && firstStage?.sla_hours !== undefined
      ? formatDateTime(new Date(Date.now() + firstStage.sla_hours * 3600 * 1000).toISOString())
      : "Không giới hạn";

  function setCustomVal(fieldId: number, value: unknown) {
    const key = String(fieldId);
    setCustomValues((prev) => ({ ...prev, [key]: value }));
    setFieldErrs((prev) => {
      if (!prev[key]) return prev;
      const next = { ...prev };
      delete next[key];
      return next;
    });
  }

  function renderField(f: WfFormField) {
    const key = String(f.id);
    const val = customValues[key];
    const errText = fieldErrs[key];

    if (f.type === "file") {
      const files = fieldFiles[key] || [];
      return (
        <div key={f.id} className="field" data-testid={`dyn-field-${f.id}`}>
          <span>
            {f.label}
            {f.required && <span className="text-danger"> *</span>}
          </span>
          <FileUploader
            files={files}
            maxFiles={1}
            label={`Chọn tệp cho "${f.label}"`}
            testId={`dyn-input-${f.id}`}
            onChange={(next) => {
              const latest = next.slice(-1);
              setFieldFiles((prev) => ({ ...prev, [key]: latest }));
              setCustomVal(f.id, latest[0]?.id || "");
            }}
          />
          {errText && <span className="text-danger" data-testid={`dyn-err-${f.id}`}>{errText}</span>}
        </div>
      );
    }

    if (f.type === "textarea") {
      return (
        <label key={f.id} className="field" data-testid={`dyn-field-${f.id}`}>
          <span>
            {f.label}
            {f.required && <span className="text-danger"> *</span>}
          </span>
          <textarea
            rows={3}
            value={typeof val === "string" ? val : ""}
            placeholder={f.placeholder || undefined}
            onChange={(e) => setCustomVal(f.id, e.target.value)}
            data-testid={`dyn-input-${f.id}`}
          />
          {errText && <span className="text-danger" data-testid={`dyn-err-${f.id}`}>{errText}</span>}
        </label>
      );
    }

    if (f.type === "select") {
      return (
        <label key={f.id} className="field" data-testid={`dyn-field-${f.id}`}>
          <span>
            {f.label}
            {f.required && <span className="text-danger"> *</span>}
          </span>
          <select
            value={typeof val === "string" ? val : ""}
            onChange={(e) => setCustomVal(f.id, e.target.value)}
            data-testid={`dyn-input-${f.id}`}
          >
            <option value="">{f.placeholder || "-- Chọn --"}</option>
            {(f.options || []).map((opt) => (
              <option key={opt} value={opt}>
                {opt}
              </option>
            ))}
          </select>
          {errText && <span className="text-danger" data-testid={`dyn-err-${f.id}`}>{errText}</span>}
        </label>
      );
    }

    if (f.type === "user") {
      return (
        <label key={f.id} className="field" data-testid={`dyn-field-${f.id}`}>
          <span>
            {f.label}
            {f.required && <span className="text-danger"> *</span>}
          </span>
          <select
            value={val ? String(val) : ""}
            onChange={(e) => setCustomVal(f.id, e.target.value ? Number(e.target.value) : "")}
            data-testid={`dyn-input-${f.id}`}
          >
            <option value="">{f.placeholder || "-- Chọn người dùng --"}</option>
            {users.map((u) => (
              <option key={u.id} value={String(u.id)}>
                {u.display_name || u.username} (@{u.username})
              </option>
            ))}
          </select>
          {errText && <span className="text-danger" data-testid={`dyn-err-${f.id}`}>{errText}</span>}
        </label>
      );
    }

    return (
      <label key={f.id} className="field" data-testid={`dyn-field-${f.id}`}>
        <span>
          {f.label}
          {f.required && <span className="text-danger"> *</span>}
        </span>
        <input
          type={f.type === "date" ? "date" : f.type === "number" ? "number" : "text"}
          step={f.type === "number" ? "any" : undefined}
          value={val === undefined || val === null ? "" : String(val)}
          placeholder={f.placeholder || undefined}
          onChange={(e) => setCustomVal(f.id, e.target.value)}
          data-testid={`dyn-input-${f.id}`}
        />
        {errText && <span className="text-danger" data-testid={`dyn-err-${f.id}`}>{errText}</span>}
      </label>
    );
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    const nextErrs = validateDynamicForm(activeFields, customValues);
    if (!title.trim()) {
      nextErrs._title = "Vui lòng nhập tên nhiệm vụ";
    }
    setFieldErrs(nextErrs);
    if (Object.keys(nextErrs).length > 0) {
      setErr("Vui lòng điền đầy đủ và đúng định dạng các trường bắt buộc (*).");
      return;
    }

    const cleanCustom: Record<string, unknown> = {};
    for (const f of activeFields) {
      const key = String(f.id);
      const raw = customValues[key];
      if (raw === undefined || raw === null || raw === "") continue;
      if (f.type === "number") {
        cleanCustom[key] = Number(raw);
      } else if (f.type === "user") {
        cleanCustom[key] = Number(raw);
      } else {
        cleanCustom[key] = raw;
      }
    }

    setBusy(true);
    try {
      const descText = description.trim();
      const fullDesc = dueHint ? `${descText}${descText ? "\n" : ""}Hạn mong muốn: ${dueHint}` : descText;
      let created = await apiJson<WfTaskDetail>(`/api/workflows/${workflow.id}/tasks`, {
        method: "POST",
        body: JSON.stringify({
          title: title.trim(),
          labels,
          description_html: fullDesc,
          custom_values: cleanCustom,
          follower_ids: followerIds,
          assignee_ids: assigneeIds.length > 0 ? assigneeIds : undefined,
        }),
      });

      const firstFormFileId = Object.values(fieldFiles).flat()[0]?.id;
      const primaryDocFileId = attachments[0]?.id || (hasSignStage && reuseFormFileForSign ? firstFormFileId : undefined);
      if (primaryDocFileId) {
        created = await apiJson<WfTaskDetail>(`/api/tasks/${created.id}/document`, {
          method: "POST",
          body: JSON.stringify({ file_id: primaryDocFileId }),
        });
      }
      onCreated(created);
      onClose();
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Tạo nhiệm vụ thất bại");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="Tạo nhiệm vụ mới" data-testid="create-task-modal">
      <form className="modal-card wide stack" onSubmit={handleSubmit} noValidate>
        <div className="preview-toolbar">
          <div>
            <h3 style={{ margin: 0 }}>Tạo nhiệm vụ mới</h3>
            <div className="muted">Quy trình: {workflow.name}</div>
          </div>
          <button type="button" className="btn tiny" disabled={busy} onClick={onClose}>
            Đóng
          </button>
        </div>

        <div className="wf-modal-scroll stack">
          {err && (
            <div className="alert error" role="alert" data-testid="create-task-error">
              {err}
            </div>
          )}

          <label className="field">
            <span>
              Tên nhiệm vụ <span className="text-danger">*</span>
            </span>
            <input
              value={title}
              onChange={(e) => {
                setTitle(e.target.value);
                if (fieldErrs._title) {
                  setFieldErrs((p) => {
                    const n = { ...p };
                    delete n._title;
                    return n;
                  });
                }
              }}
              placeholder="Ví dụ: Công văn số 102/CV-KT"
              data-testid="create-task-title"
            />
            {fieldErrs._title && <span className="text-danger">{fieldErrs._title}</span>}
          </label>

          <div className="field">
            <span>Nhãn</span>
            <LabelInput labels={labels} onChange={setLabels} testId="create-task-labels" />
          </div>

          <div className="two-col">
            <label className="field">
              <span>Thời hạn SLA bước 1 ({firstStage?.name || "-"})</span>
              <input value={slaPreview} readOnly disabled />
            </label>
            <label className="field">
              <span>Hạn mong muốn (ghi chú tùy chọn)</span>
              <input
                type="date"
                value={dueHint}
                onChange={(e) => setDueHint(e.target.value)}
                data-testid="create-task-due-hint"
              />
            </label>
          </div>

          {activeFields.length > 0 && (
            <div className="card stack" style={{ background: "#fafbfc" }}>
              <strong>Thông tin biểu mẫu quy trình</strong>
              {activeFields.map((f) => renderField(f))}
            </div>
          )}

          <div className="field">
            <span>
              Giao cho (thành viên bước đầu: {firstStage?.name || "-"} — bỏ trống để giao tất cả)
            </span>
            <UserPicker
              options={firstMembers}
              selectedIds={assigneeIds}
              onChange={setAssigneeIds}
              multiple
              testIdPrefix="create-task-assignee"
            />
          </div>

          <div className="field">
            <span>Người theo dõi</span>
            <UserPicker
              options={users}
              selectedIds={followerIds}
              onChange={setFollowerIds}
              multiple
              testIdPrefix="create-task-follower"
            />
          </div>

          <label className="field">
            <span>Mô tả</span>
            <textarea
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Nội dung chi tiết hoặc lưu ý xử lý..."
              data-testid="create-task-description"
            />
          </label>

          <div className="field">
            <span>Tài liệu đính kèm công việc (tối đa 10 tệp; tệp đầu tiên dùng làm tài liệu trình ký)</span>
            <FileUploader
              files={attachments}
              onChange={setAttachments}
              maxFiles={10}
              testId="create-task-attachments"
            />
            {hasSignStage && attachments.length === 0 && Object.values(fieldFiles).flat().length > 0 && (
              <label className="check-item" style={{ marginTop: 4 }}>
                <input
                  type="checkbox"
                  checked={reuseFormFileForSign}
                  onChange={(e) => setReuseFormFileForSign(e.target.checked)}
                  data-testid="create-task-reuse-form-file"
                />
                <span>Sử dụng tệp từ trường biểu mẫu làm tài liệu chính để ký ở bước ký</span>
              </label>
            )}
          </div>
        </div>

        <div className="row" style={{ justifyContent: "flex-end", paddingTop: 8, borderTop: "1px solid var(--border)" }}>
          <button type="button" className="btn small" disabled={busy} onClick={onClose}>
            Hủy
          </button>
          <button
            type="submit"
            className="btn small primary"
            disabled={busy}
            data-testid="create-task-submit"
          >
            {busy ? "Đang tạo..." : "Tạo nhiệm vụ"}
          </button>
        </div>
      </form>
    </div>
  );
}
