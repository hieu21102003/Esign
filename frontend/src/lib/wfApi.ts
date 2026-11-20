import { apiBytes, apiJson, type StoredFile, type TimelineEvent, type UserBrief } from "./api";

export type StageType = "normal" | "sign" | "approve";
export type AssignMode = "any" | "all";
export type TaskStatus = "processing" | "completed" | "failed";
export type TodoStatus = "doing" | "done" | "failed" | "cancelled";
export type FormFieldType = "text" | "textarea" | "date" | "file" | "number" | "select" | "user";
export type WorkflowVisibility = "followers_only" | "all_visible";
export type WorkflowStatusKind = "active" | "closed";
export type JobRoleFilter = "assigned" | "created" | "following" | "team" | "";

export interface WfStage {
  id: number;
  order: number;
  name: string;
  type: StageType;
  sla_hours: number | null;
  assign_mode: AssignMode;
  guide_html: string;
  is_active?: boolean;
  processing_count?: number;
  has_history?: boolean;
  members?: UserBrief[];
}

export interface WfFormField {
  id: number;
  label: string;
  type: FormFieldType;
  required: boolean;
  placeholder: string;
  options: string[] | null;
  order: number;
  is_active?: boolean;
  has_data?: boolean;
}

export interface WfFailureReason {
  id: number;
  label: string;
  order: number;
}

export interface WfRoleItem {
  user: UserBrief;
  role: "admin" | "supervisor" | "creator" | "viewer";
}

export interface WfWarning {
  code: string;
  stage_id?: number;
  stage_name?: string;
  user_id?: number;
  username?: string;
  message: string;
}

export interface WfSummary {
  id: number;
  name: string;
  slug: string;
  category: string;
  description: string;
  visibility: WorkflowVisibility;
  status: WorkflowStatusKind;
  creator_id: number;
  created_at: string;
  stage_count: number;
  my_roles: string[];
  can_manage?: boolean;
}

export interface WfDetail extends WfSummary {
  guide_html: string;
  stages: WfStage[];
  form_fields: WfFormField[];
  failure_reasons: WfFailureReason[];
  can_manage: boolean;
  can_create_task: boolean;
  processing_count?: number;
  has_history?: boolean;
  warnings?: WfWarning[];
  roles?: WfRoleItem[];
}

export interface WfTemplateStage {
  name: string;
  type: StageType;
  sla_hours: number | null;
  assign_mode: AssignMode;
  guide_html: string;
}

export interface WfTemplateField {
  label: string;
  type: FormFieldType;
  required: boolean;
  placeholder: string;
  options?: string[] | null;
}

export interface WfTemplateDefinition {
  visibility: WorkflowVisibility;
  guide_html: string;
  stages: WfTemplateStage[];
  form_fields: WfTemplateField[];
  failure_reasons: string[];
}

export interface WfTemplate {
  id: number;
  slug: string;
  name: string;
  category: string;
  description: string;
  definition: WfTemplateDefinition;
  is_builtin: boolean;
  creator_id: number | null;
  created_at: string;
}

export interface WfBuilderStageDraft {
  localKey: string;
  id?: number;
  name: string;
  type: StageType;
  sla_hours: number | null;
  assign_mode: AssignMode;
  guide_html: string;
  member_ids: number[];
  is_active: boolean;
  processing_count?: number;
  has_history?: boolean;
}

export interface WfBuilderFieldDraft {
  localKey: string;
  id?: number;
  label: string;
  type: FormFieldType;
  required: boolean;
  placeholder: string;
  options: string[];
  is_active: boolean;
  has_data?: boolean;
}

export interface WfBuilderRoleDraft {
  user_id: number;
  role: "admin" | "supervisor" | "creator" | "viewer";
}

export interface WfBuilderReasonDraft {
  localKey: string;
  id?: number;
  label: string;
}

export interface WfBuilderDraft {
  name: string;
  slug: string;
  category: string;
  description: string;
  guide_html: string;
  visibility: WorkflowVisibility;
  status: WorkflowStatusKind;
  stages: WfBuilderStageDraft[];
  form_fields: WfBuilderFieldDraft[];
  roles: WfBuilderRoleDraft[];
  failure_reasons: WfBuilderReasonDraft[];
}

export interface WfTaskActorBrief {
  id: number;
  display_name: string;
}

export interface WfTaskItem {
  id: number;
  workflow_id: number;
  workflow_name: string;
  title: string;
  labels: string[];
  status: TaskStatus;
  is_overdue: boolean;
  deadline: string | null;
  current_stage: { id: number; order: number; name: string } | null;
  creator: WfTaskActorBrief;
  assignees: WfTaskActorBrief[];
  document_id: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  archived_at: string | null;
}

export interface WfTodoItem {
  id: number;
  task_id: number;
  stage_id: number;
  stage_name: string;
  stage_order: number | null;
  assignee: UserBrief;
  result: string | null;
  status: TodoStatus;
  deadline: string | null;
  is_overdue: boolean;
  workflow_step_id: number | null;
  step_assignee_id: number | null;
  created_at: string;
  completed_at: string | null;
  task?: {
    id: number;
    title: string;
    status: TaskStatus;
    workflow_id: number;
    workflow_name: string;
    is_overdue: boolean;
  };
}

export interface WfTaskSignature {
  step_id: number;
  step_order: number;
  signer: UserBrief;
  signed_at: string | null;
  remark: string | null;
  signed_file_id: string | null;
  sign_job_id: number | null;
  version_no: number | null;
  cert_key_ref: string | null;
}

export interface WfTaskSignStepAssignee {
  id: number;
  user: UserBrief;
  status: string;
  signed_at: string | null;
  remark: string | null;
  signed_file_id: string | null;
}

export interface WfTaskSignStep {
  id: number;
  step_order: number;
  mode: AssignMode;
  status: string;
  created_at: string;
  completed_at: string | null;
  assignees: WfTaskSignStepAssignee[];
}

export interface WfTaskDocVersion {
  version_no: number;
  file_id: string;
  step_id: number | null;
  note: string | null;
  created_at: string;
}

export interface WfTaskDocument {
  id: string;
  name: string;
  title: string;
  status: string;
  original_file_id: string;
  current_file_id: string;
  file_name: string;
  current_version_no: number;
  versions: WfTaskDocVersion[];
  signatures: WfTaskSignature[];
  current_sign_step: WfTaskSignStep | null;
}

export interface WfTaskPermissions {
  can_edit: boolean;
  can_add_followers: boolean;
  can_archive: boolean;
  can_restore: boolean;
  my_todo_ids: number[];
}

export interface WfStageWithState extends WfStage {
  state: "done" | "current" | "failed" | "pending";
}

export interface WfTaskFormEntry {
  field_id: number;
  label: string;
  type: FormFieldType;
  value: unknown;
}

export interface WfTaskDetail {
  id: number;
  workflow: {
    id: number;
    name: string;
    slug: string;
    visibility: WorkflowVisibility;
    status: WorkflowStatusKind;
  };
  title: string;
  labels: string[];
  description_html: string;
  custom_values: Record<string, unknown>;
  form: WfTaskFormEntry[];
  status: TaskStatus;
  is_overdue: boolean;
  deadline: string | null;
  current_stage: WfStage | null;
  stages: WfStageWithState[];
  failure_reason: { id: number; label: string } | null;
  creator: UserBrief;
  followers: UserBrief[];
  todos: WfTodoItem[];
  document_id: string | null;
  document: WfTaskDocument | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  archived_at: string | null;
  permissions: WfTaskPermissions;
  activity: TimelineEvent[];
}

export interface WfTaskTimeline {
  task_id: number;
  document_id: string | null;
  title: string;
  status: TaskStatus;
  created_at: string;
  completed_at: string | null;
  events: TimelineEvent[];
}

export interface WfBoardColumn {
  stage: WfStage;
  count: number;
  overdue_count: number;
  tasks: WfTaskItem[];
}

export interface WfBoard {
  workflow: {
    id: number;
    name: string;
    slug: string;
    visibility: WorkflowVisibility;
    status: WorkflowStatusKind;
  };
  stages: WfBoardColumn[];
  completed: { count: number; tasks: WfTaskItem[] };
  failed: { count: number; tasks: WfTaskItem[] };
}

export interface WfPaged<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface WfStats {
  processing: number;
  completed: number;
  failed: number;
  overdue: number;
  total: number;
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) {
    return iso.slice(0, 16).replace("T", " ");
  }
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function formatShortDate(iso: string | null | undefined): string {
  if (!iso) return "-";
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (m) return `${m[3]}/${m[2]}/${m[1]}`;
  return formatDateTime(iso);
}

export function formatSlaCountdown(
  deadlineIso: string | null | undefined,
  status: string,
  nowMs: number = Date.now(),
): { text: string; overdue: boolean } {
  if (!deadlineIso) return { text: "Không giới hạn SLA", overdue: false };
  const target = new Date(deadlineIso.endsWith("Z") || /[+-]\d\d:\d\d$/.test(deadlineIso) ? deadlineIso : `${deadlineIso}Z`).getTime();
  if (Number.isNaN(target)) return { text: deadlineIso, overdue: false };
  if (status !== "processing" && status !== "doing") {
    return { text: `Hạn: ${formatDateTime(deadlineIso)}`, overdue: false };
  }
  const diffSec = Math.round((target - nowMs) / 1000);
  const absSec = Math.abs(diffSec);
  const days = Math.floor(absSec / 86400);
  const hrs = Math.floor((absSec % 86400) / 3600);
  const mins = Math.floor((absSec % 3600) / 60);
  const parts: string[] = [];
  if (days > 0) parts.push(`${days} ngày`);
  if (hrs > 0) parts.push(`${hrs} giờ`);
  if (days === 0 && (mins > 0 || parts.length === 0)) parts.push(`${mins} phút`);
  const dur = parts.join(" ");
  if (diffSec < 0) {
    return { text: `Quá hạn ${dur}`, overdue: true };
  }
  return { text: `Còn ${dur}`, overdue: false };
}

export function formatTodoResult(result: string | null | undefined): string {
  if (!result) return "";
  const trimmed = result.trim();
  if (trimmed.startsWith("{") && trimmed.endsWith("}")) {
    try {
      const obj = JSON.parse(trimmed) as Record<string, unknown>;
      if (obj && (obj.version_no !== undefined || obj.cert_key_ref || obj.signer)) {
        const who = String(obj.display_name || obj.signer || "");
        const ver = obj.version_no !== undefined ? `v${String(obj.version_no)}` : "";
        const ref = obj.cert_key_ref ? `(${String(obj.cert_key_ref)})` : "";
        return [`Đã ký ${ver}`, who, ref].filter(Boolean).join(" · ");
      }
    } catch {
      // fall through to raw string
    }
  }
  return result;
}

export function validateDynamicForm(
  fields: WfFormField[],
  values: Record<string, unknown>,
): Record<string, string> {
  const errs: Record<string, string> = {};
  for (const f of fields) {
    if (f.is_active === false) continue;
    const k = String(f.id);
    const v = values[k];
    const empty =
      v === undefined ||
      v === null ||
      (typeof v === "string" && v.trim() === "") ||
      (Array.isArray(v) && v.length === 0);
    if (empty) {
      if (f.required) {
        errs[k] = `Vui lòng nhập "${f.label}"`;
      }
      continue;
    }
    if (f.type === "number" && Number.isNaN(Number(v))) {
      errs[k] = `"${f.label}" phải là số hợp lệ`;
    } else if (f.type === "date" && typeof v === "string" && !/^\d{4}-\d{2}-\d{2}/.test(v)) {
      errs[k] = `"${f.label}" phải là ngày hợp lệ`;
    } else if (f.type === "select" && f.options && !f.options.includes(String(v))) {
      errs[k] = `"${f.label}" chứa lựa chọn không hợp lệ`;
    }
  }
  return errs;
}

export function initialsOf(name: string | null | undefined): string {
  const s = (name || "").trim();
  if (!s) return "?";
  const parts = s.split(/\s+/).filter(Boolean);
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export function userDisplayName(u: UserBrief | { display_name?: string; username?: string } | null | undefined): string {
  if (!u) return "-";
  const d = u.display_name || "";
  const un = u.username || "";
  if (d && un && d !== un) return `${d} (@${un})`;
  return d || un || "-";
}

export async function downloadCsv(path: string, filename: string): Promise<void> {
  const bytes = await apiBytes(path);
  const blob = new Blob([new Uint8Array(bytes)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}

export async function fetchWorkflowStats(wfId: number): Promise<WfStats> {
  const board = await apiJson<WfBoard>(`/api/workflows/${wfId}/board?per_column=1`);
  let processing = 0;
  let overdue = 0;
  for (const col of board.stages) {
    processing += col.count;
    overdue += col.overdue_count;
  }
  const completed = board.completed.count;
  const failed = board.failed.count;
  return { processing, completed, failed, overdue, total: processing + completed + failed };
}

export type UploadedDocEntry = StoredFile & { sourceLabel?: string };

export function validateWorkflowDraft(draft: WfBuilderDraft, isEdit = false): string[] {
  const errs: string[] = [];
  if (!draft.name.trim()) {
    errs.push("Vui lòng nhập tên quy trình.");
  }
  const activeStages = draft.stages.filter((s) => s.is_active !== false);
  if (activeStages.length === 0) {
    errs.push("Quy trình phải có ít nhất 1 giai đoạn đang hoạt động.");
  }
  const seenNames = new Set<string>();
  for (let i = 0; i < activeStages.length; i += 1) {
    const s = activeStages[i];
    const sName = s.name.trim();
    if (!sName) {
      errs.push(`Giai đoạn #${i + 1}: tên không được để trống.`);
    } else {
      const key = sName.toLowerCase();
      if (seenNames.has(key)) {
        errs.push(`Tên giai đoạn "${sName}" bị trùng lặp.`);
      }
      seenNames.add(key);
    }
    if (s.sla_hours !== null && s.sla_hours !== undefined) {
      if (!Number.isInteger(s.sla_hours) || s.sla_hours < 1 || s.sla_hours > 8760) {
        errs.push(`Giai đoạn "${sName || `#${i + 1}`}": thời hạn SLA phải từ 1 đến 8760 giờ.`);
      }
    }
    if (draft.status === "active" && s.member_ids.length === 0) {
      errs.push(`Giai đoạn "${sName || `#${i + 1}`}": cần chọn ít nhất 1 thành viên khi quy trình hoạt động.`);
    }
  }
  const activeFields = draft.form_fields.filter((f) => f.is_active !== false);
  for (let i = 0; i < activeFields.length; i += 1) {
    const f = activeFields[i];
    if (!f.label.trim()) {
      errs.push(`Trường biểu mẫu #${i + 1}: nhãn không được để trống.`);
    }
    if (f.type === "select") {
      const cleanOpts = f.options.map((o) => o.trim()).filter(Boolean);
      if (cleanOpts.length === 0) {
        errs.push(`Trường lựa chọn "${f.label.trim() || `#${i + 1}`}": cần ít nhất 1 lựa chọn.`);
      }
    }
  }
  if ((draft.roles.length > 0 || isEdit) && !draft.roles.some((r) => r.role === "admin")) {
    errs.push("Quy trình phải có ít nhất 1 vai trò admin.");
  }
  return errs;
}

export async function fetchTemplates(): Promise<WfTemplate[]> {
  const res = await apiJson<{ items: WfTemplate[] }>("/api/templates");
  return res.items || [];
}

export async function createWorkflow(payload: Record<string, unknown>): Promise<WfDetail> {
  return apiJson<WfDetail>("/api/workflows", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function createWorkflowFromTemplate(payload: Record<string, unknown>): Promise<WfDetail> {
  return apiJson<WfDetail>("/api/workflows/from-template", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function patchWorkflow(workflowId: number, payload: Record<string, unknown>): Promise<WfDetail> {
  return apiJson<WfDetail>(`/api/workflows/${workflowId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function closeWorkflow(workflowId: number): Promise<WfDetail> {
  return apiJson<WfDetail>(`/api/workflows/${workflowId}/close`, { method: "POST" });
}

export async function reopenWorkflow(workflowId: number): Promise<WfDetail> {
  return apiJson<WfDetail>(`/api/workflows/${workflowId}/reopen`, { method: "POST" });
}

export async function cloneWorkflow(workflowId: number, payload?: { name?: string; slug?: string }): Promise<WfDetail> {
  return apiJson<WfDetail>(`/api/workflows/${workflowId}/clone`, {
    method: "POST",
    body: JSON.stringify(payload || {}),
  });
}

export async function saveWorkflowAsTemplate(
  workflowId: number,
  payload: { name?: string; category?: string; description?: string },
): Promise<WfTemplate> {
  return apiJson<WfTemplate>(`/api/workflows/${workflowId}/save-as-template`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function addWorkflowRole(
  workflowId: number,
  userId: number,
  role: "admin" | "supervisor" | "creator" | "viewer",
): Promise<WfDetail> {
  return apiJson<WfDetail>(`/api/workflows/${workflowId}/roles`, {
    method: "POST",
    body: JSON.stringify({ user_id: userId, role }),
  });
}

export async function removeWorkflowRole(workflowId: number, userId: number, role: string): Promise<WfDetail> {
  return apiJson<WfDetail>(`/api/workflows/${workflowId}/roles/${userId}/${role}`, { method: "DELETE" });
}

export async function addStageMembers(workflowId: number, stageId: number, userIds: number[]): Promise<WfDetail> {
  return apiJson<WfDetail>(`/api/workflows/${workflowId}/stages/${stageId}/members`, {
    method: "POST",
    body: JSON.stringify({ user_ids: userIds }),
  });
}

export async function removeStageMember(workflowId: number, stageId: number, userId: number): Promise<WfDetail> {
  return apiJson<WfDetail>(`/api/workflows/${workflowId}/stages/${stageId}/members/${userId}`, {
    method: "DELETE",
  });
}
