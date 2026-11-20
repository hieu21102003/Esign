import type { JobRoleFilter } from "./wfApi";

export type AppRoute =
  | { kind: "legacy"; legacyTab?: "workflow"; docId?: string | null }
  | { kind: "jobs" }
  | { kind: "todos" }
  | { kind: "workflows" }
  | { kind: "workflow_new" }
  | { kind: "workflow_edit"; workflowId: number }
  | { kind: "workflow_people"; workflowId: number }
  | { kind: "workflow_board"; workflowId: number }
  | { kind: "workflow_archive"; workflowId: number }
  | { kind: "task_detail"; taskId: number };

const DOC_PATH_RE = /^\/documents\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\/?$/i;

export function parseRoute(pathname: string): AppRoute {
  const path = pathname.replace(/\/+$/, "") || "/";
  const docM = path.match(DOC_PATH_RE);
  if (docM) return { kind: "legacy", legacyTab: "workflow", docId: docM[1] };
  if (path === "/jobs") return { kind: "jobs" };
  if (path === "/todos") return { kind: "todos" };
  if (path === "/workflows") return { kind: "workflows" };
  if (path === "/workflows/new") return { kind: "workflow_new" };
  const editM = path.match(/^\/workflows\/(\d+)\/edit$/);
  if (editM) return { kind: "workflow_edit", workflowId: Number(editM[1]) };
  const peopleM = path.match(/^\/workflows\/(\d+)\/people$/);
  if (peopleM) return { kind: "workflow_people", workflowId: Number(peopleM[1]) };
  const archM = path.match(/^\/workflows\/(\d+)\/archive$/);
  if (archM) return { kind: "workflow_archive", workflowId: Number(archM[1]) };
  const wfM = path.match(/^\/workflows\/(\d+)$/);
  if (wfM) return { kind: "workflow_board", workflowId: Number(wfM[1]) };
  const taskM = path.match(/^\/tasks\/(\d+)$/);
  if (taskM) return { kind: "task_detail", taskId: Number(taskM[1]) };
  return { kind: "legacy" };
}

export function navigateTo(path: string, replace = false): void {
  if (typeof window === "undefined") return;
  if (replace) {
    window.history.replaceState(null, "", path);
  } else {
    window.history.pushState(null, "", path);
  }
}

export interface JobsFilterState {
  role: JobRoleFilter;
  status: string;
  q: string;
  workflow_id: string;
  stage_id: string;
  creator_id: string;
  assignee_id: string;
  created_from: string;
  created_to: string;
  deadline_from: string;
  deadline_to: string;
  completed_from: string;
  completed_to: string;
  sort: string;
  page: number;
}

export const DEFAULT_JOBS_FILTER: JobsFilterState = {
  role: "assigned",
  status: "",
  q: "",
  workflow_id: "",
  stage_id: "",
  creator_id: "",
  assignee_id: "",
  created_from: "",
  created_to: "",
  deadline_from: "",
  deadline_to: "",
  completed_from: "",
  completed_to: "",
  sort: "-created_at",
  page: 1,
};

export function parseJobsQuery(search: string, defaults: Partial<JobsFilterState> = {}): JobsFilterState {
  const qs = new URLSearchParams(search);
  const base = { ...DEFAULT_JOBS_FILTER, ...defaults };
  const roleParam = qs.get("role");
  const validRoles: JobRoleFilter[] = ["assigned", "created", "following", "team", ""];
  const role: JobRoleFilter =
    roleParam !== null && validRoles.includes(roleParam as JobRoleFilter)
      ? (roleParam as JobRoleFilter)
      : base.role;
  const pageNum = Number(qs.get("page") || base.page);
  return {
    role,
    status: qs.get("status") ?? base.status,
    q: qs.get("q") ?? base.q,
    workflow_id: qs.get("workflow_id") ?? base.workflow_id,
    stage_id: qs.get("stage_id") ?? base.stage_id,
    creator_id: qs.get("creator_id") ?? base.creator_id,
    assignee_id: qs.get("assignee_id") ?? base.assignee_id,
    created_from: qs.get("created_from") ?? base.created_from,
    created_to: qs.get("created_to") ?? base.created_to,
    deadline_from: qs.get("deadline_from") ?? base.deadline_from,
    deadline_to: qs.get("deadline_to") ?? base.deadline_to,
    completed_from: qs.get("completed_from") ?? base.completed_from,
    completed_to: qs.get("completed_to") ?? base.completed_to,
    sort: qs.get("sort") || base.sort,
    page: Number.isFinite(pageNum) && pageNum >= 1 ? Math.floor(pageNum) : 1,
  };
}

export function serializeJobsQuery(state: JobsFilterState, omitFields: (keyof JobsFilterState)[] = []): string {
  const qs = new URLSearchParams();
  const omit = new Set(omitFields);
  const put = (key: keyof JobsFilterState, val: string | number) => {
    if (omit.has(key)) return;
    const s = String(val);
    if (s) qs.set(String(key), s);
  };
  if (state.role !== "assigned") put("role", state.role);
  else if (!omit.has("role") && qs.toString()) qs.set("role", "assigned");
  put("status", state.status);
  put("q", state.q.trim());
  put("workflow_id", state.workflow_id);
  put("stage_id", state.stage_id);
  put("creator_id", state.creator_id);
  put("assignee_id", state.assignee_id);
  put("created_from", state.created_from);
  put("created_to", state.created_to);
  put("deadline_from", state.deadline_from);
  put("deadline_to", state.deadline_to);
  put("completed_from", state.completed_from);
  put("completed_to", state.completed_to);
  if (state.sort && state.sort !== "-created_at") put("sort", state.sort);
  if (state.page > 1) put("page", state.page);
  return qs.toString();
}

export function countActiveJobFilters(
  state: JobsFilterState,
  excludeKeys: (keyof JobsFilterState)[] = [],
): number {
  const skip = new Set<keyof JobsFilterState>(["role", "page", "sort", ...excludeKeys]);
  const keys: (keyof JobsFilterState)[] = [
    "q",
    "status",
    "workflow_id",
    "stage_id",
    "creator_id",
    "assignee_id",
    "created_from",
    "created_to",
    "deadline_from",
    "deadline_to",
    "completed_from",
    "completed_to",
  ];
  let n = 0;
  for (const k of keys) {
    if (!skip.has(k) && String(state[k]).trim()) n += 1;
  }
  return n;
}
