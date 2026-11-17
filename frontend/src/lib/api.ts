/**
 * API client: KHÔNG dùng localStorage/sessionStorage.
 * Phiên dùng cookie httpOnly tự động qua credentials: "same-origin".
 * csrfToken giữ trong bộ nhớ JS và gửi qua header X-CSRF-Token cho mọi method thay đổi dữ liệu.
 */

let csrfToken = "";

export function setCsrfToken(tok: string): void {
  csrfToken = tok || "";
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
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
    throw new ApiError(res.status, msg);
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
