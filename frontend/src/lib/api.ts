/**
 * API client: KHÔNG dùng localStorage/sessionStorage.
 * Phiên dùng cookie httpOnly tự động qua credentials: "same-origin".
 * csrfToken giữ trong bộ nhớ JS và gửi qua header X-CSRF-Token cho mọi method thay đổi dữ liệu.
 */

let csrfToken = "";

export function setCsrfToken(tok: string): void {
  csrfToken = tok || "";
}

export interface ApiErrorMeta {
  pinError?: string;
  pinRemaining?: number;
  retryAfter?: number;
}

export class ApiError extends Error {
  status: number;
  pinError?: string;
  pinRemaining?: number;
  retryAfter?: number;
  constructor(status: number, message: string, meta: ApiErrorMeta = {}) {
    super(message);
    this.status = status;
    this.pinError = meta.pinError;
    this.pinRemaining = meta.pinRemaining;
    this.retryAfter = meta.retryAfter;
  }
}

async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const method = (init.method || "GET").toUpperCase();
  const headers = new Headers(init.headers || {});
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && csrfToken) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  const res = await fetch(path, {
    ...init,
    method,
    headers,
    credentials: "same-origin",
  });
  if (!res.ok) {
    let msg = `Lỗi HTTP ${res.status}`;
    try {
      const j = await res.json();
      if (j && typeof j.detail === "string" && j.detail) {
        msg = j.detail;
      }
    } catch {
      // ignore non-json
    }
    const pinError = res.headers.get("X-Pin-Error") || undefined;
    const remRaw = res.headers.get("X-Pin-Remaining");
    const raRaw = res.headers.get("Retry-After");
    const pinRemaining = remRaw !== null && /^\d+$/.test(remRaw) ? Number(remRaw) : undefined;
    const retryAfter = raRaw !== null && /^\d+$/.test(raRaw) ? Number(raRaw) : undefined;
    throw new ApiError(res.status, msg, { pinError, pinRemaining, retryAfter });
  }
  return res;
}

export async function apiJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers || {});
  if (init.body && typeof init.body === "string" && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const res = await request(path, { ...init, headers });
  return (await res.json()) as T;
}

export async function apiBytes(path: string): Promise<Uint8Array> {
  const res = await request(path, { method: "GET" });
  return new Uint8Array(await res.arrayBuffer());
}

export interface MeResponse {
  id: number;
  username: string;
  display_name: string;
  role: "admin" | "user";
  mfa_verified: boolean;
  totp_enrolled: boolean;
  csrf_token: string;
  has_signing_pin: boolean;
  pin_set_at: string | null;
  pin_locked: boolean;
  pin_locked_until: string | null;
}

export interface StoredFile {
  id: string;
  name: string;
  kind: "upload" | "signed" | "image";
  size: number;
  sha256: string;
  pages: number;
  parent_id: string | null;
  created_at: string;
  expires_at: string;
  page_sizes?: [number, number][];
}

export interface CertificateItem {
  id: number;
  subject: string;
  serial: string;
  not_after: string;
}

export interface PresetConfig {
  mode?: "text" | "image" | "both";
  show?: ("name" | "title" | "date" | "location" | "reason")[];
  reason?: string;
  location?: string;
  title?: string;
  level?: "B-B" | "B-T" | "B-LT";
  cert_id?: number;
}

export interface PresetItem {
  id: number;
  name: string;
  config: PresetConfig;
}

export interface SignatureVerifyItem {
  field: string;
  signer?: string;
  intact: boolean;
  valid: boolean;
  trusted: boolean;
  revoked?: boolean;
  signed_at?: string | null;
  reason?: string | null;
  modification?: string | null;
  ok: boolean;
  summary?: string;
  error?: string;
}

export interface UserBrief {
  id: number | null;
  username: string;
  display_name: string;
}

export interface AssignableUser {
  id: number;
  username: string;
  display_name: string;
  has_cert: boolean;
}

export type WorkflowStatus = "in_progress" | "completed" | "rejected" | "cancelled";

export interface WorkflowAssignee {
  id: number;
  user: UserBrief;
  status: "pending" | "signed" | "rejected" | "skipped";
  signed_at: string | null;
  remark: string | null;
  signed_file_id: string | null;
}

export interface WorkflowStep {
  id: number;
  step_order: number;
  mode: "all" | "any";
  status: "active" | "completed" | "rejected" | "cancelled";
  assigned_by: UserBrief;
  created_at: string;
  completed_at: string | null;
  assignees: WorkflowAssignee[];
}

export interface WorkflowVersion {
  version_no: number;
  file_id: string;
  step_id: number | null;
  created_by: UserBrief;
  note: string | null;
  sha256: string;
  size: number;
  created_at: string;
}

export interface WorkflowDocumentSummary {
  id: string;
  title: string;
  description: string;
  status: WorkflowStatus;
  creator: UserBrief;
  original_file_id: string;
  current_file_id: string;
  current_step_order: number;
  total_steps: number;
  my_pending: boolean;
  awaiting_next_step: boolean;
  can_assign_or_complete: boolean;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  rejected_at: string | null;
  cancelled_at: string | null;
  cancel_reason: string | null;
}

export interface WorkflowDocumentDetail extends WorkflowDocumentSummary {
  current_file: {
    id: string;
    name: string;
    size: number;
    sha256: string;
    pages: number;
    page_sizes: [number, number][];
  } | null;
  steps: WorkflowStep[];
  versions: WorkflowVersion[];
  can_sign: boolean;
  can_decide_next_on_sign: boolean;
  can_reject: boolean;
  can_cancel: boolean;
}

export interface TimelineEvent {
  id: number;
  ts: string;
  user_id: number | null;
  username: string;
  action: string;
  status: string;
  ip: string;
  file_id: string | null;
  cert_key_ref: string | null;
  detail: string | null;
  metadata: Record<string, unknown> | null;
}

export interface WorkflowTimeline {
  document_id: string;
  title: string;
  status: WorkflowStatus;
  created_at: string;
  completed_at: string | null;
  rejected_at: string | null;
  cancelled_at: string | null;
  cancel_reason: string | null;
  steps: WorkflowStep[];
  events: TimelineEvent[];
}

export interface NotificationItem {
  id: number;
  document_id: string | null;
  kind: "assigned" | "completed" | "rejected" | "cancelled";
  title: string;
  message: string;
  is_read: boolean;
  created_at: string;
  read_at: string | null;
}

export interface NotificationsResponse {
  unread_count: number;
  items: NotificationItem[];
}
