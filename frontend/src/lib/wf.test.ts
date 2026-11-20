import { describe, expect, it } from "vitest";
import { sanitizeHref, sanitizeHtmlNodes } from "../components/wf/SafeHtml";
import { countActiveJobFilters, DEFAULT_JOBS_FILTER, parseJobsQuery, parseRoute, serializeJobsQuery } from "./router";
import {
  formatSlaCountdown,
  formatTodoResult,
  validateDynamicForm,
  validateWorkflowDraft,
  type WfBuilderDraft,
  type WfFormField,
} from "./wfApi";

describe("W7 router & URL query persistence", () => {
  it("parses legacy and workflow engine routes", () => {
    expect(parseRoute("/")).toEqual({ kind: "legacy" });
    expect(parseRoute("/documents/123e4567-e89b-12d3-a456-426614174000")).toEqual({
      kind: "legacy",
      legacyTab: "workflow",
      docId: "123e4567-e89b-12d3-a456-426614174000",
    });
    expect(parseRoute("/jobs")).toEqual({ kind: "jobs" });
    expect(parseRoute("/todos")).toEqual({ kind: "todos" });
    expect(parseRoute("/workflows")).toEqual({ kind: "workflows" });
    expect(parseRoute("/workflows/new")).toEqual({ kind: "workflow_new" });
    expect(parseRoute("/workflows/12/edit")).toEqual({ kind: "workflow_edit", workflowId: 12 });
    expect(parseRoute("/workflows/12/people")).toEqual({ kind: "workflow_people", workflowId: 12 });
    expect(parseRoute("/workflows/12")).toEqual({ kind: "workflow_board", workflowId: 12 });
    expect(parseRoute("/workflows/12/archive")).toEqual({ kind: "workflow_archive", workflowId: 12 });
    expect(parseRoute("/tasks/99")).toEqual({ kind: "task_detail", taskId: 99 });
  });

  it("serializes, parses and counts active jobs filters", () => {
    const state = {
      ...DEFAULT_JOBS_FILTER,
      role: "created" as const,
      status: "overdue",
      q: "công văn",
      workflow_id: "3",
      deadline_from: "2026-11-01",
      page: 2,
    };
    const qs = serializeJobsQuery(state);
    const parsed = parseJobsQuery(`?${qs}`);
    expect(parsed.role).toBe("created");
    expect(parsed.status).toBe("overdue");
    expect(parsed.q).toBe("công văn");
    expect(parsed.workflow_id).toBe("3");
    expect(parsed.deadline_from).toBe("2026-11-01");
    expect(parsed.page).toBe(2);
    expect(countActiveJobFilters(parsed)).toBe(4);
  });
});

describe("W7 HTML sanitizer allowlist", () => {
  it("blocks javascript links and drops script/iframe content", () => {
    expect(sanitizeHref("javascript:alert(1)")).toBeNull();
    expect(sanitizeHref("  java\nscript:alert(1)")).toBeNull();
    expect(sanitizeHref("data:text/html,<script>1</script>")).toBeNull();
    expect(sanitizeHref("/workflows/1")).toBe("/workflows/1");
    expect(sanitizeHref("mailto:admin@example.local")).toBe("mailto:admin@example.local");

    const nodes = sanitizeHtmlNodes('<p>Xin chào</p><script>evil()</script><iframe src="x"></iframe>');
    const serialized = JSON.stringify(nodes);
    expect(serialized).toContain("Xin chào");
    expect(serialized).not.toContain("evil()");
    expect(serialized).not.toContain("iframe");
  });
});

describe("W7 dynamic form, todo result & SLA countdown helpers", () => {
  it("validates required and typed dynamic fields", () => {
    const fields: WfFormField[] = [
      { id: 1, label: "Tên công văn", type: "text", required: true, placeholder: "", options: null, order: 1 },
      { id: 2, label: "Số lượng", type: "number", required: false, placeholder: "", options: null, order: 2 },
      { id: 3, label: "Loại", type: "select", required: true, placeholder: "", options: ["A", "B"], order: 3 },
    ];
    const errs1 = validateDynamicForm(fields, { "1": "", "2": "abc", "3": "Z" });
    expect(errs1["1"]).toContain("Tên công văn");
    expect(errs1["2"]).toContain("số");
    expect(errs1["3"]).toContain("lựa chọn");

    const errs2 = validateDynamicForm(fields, { "1": "CV 01", "2": "10", "3": "A" });
    expect(errs2).toEqual({});
  });

  it("formats W6 signature JSON todo result cleanly", () => {
    const raw = JSON.stringify({
      signer: "gd",
      display_name: "Giám đốc",
      version_no: 2,
      cert_key_ref: "bob",
    });
    const formatted = formatTodoResult(raw);
    expect(formatted).toContain("v2");
    expect(formatted).toContain("Giám đốc");
    expect(formatted).toContain("bob");
    expect(formatTodoResult("Đã xong")).toBe("Đã xong");
  });

  it("computes SLA remaining vs overdue", () => {
    const now = Date.UTC(2026, 10, 20, 10, 0, 0);
    const future = new Date(now + 90 * 60 * 1000).toISOString();
    const past = new Date(now - 75 * 60 * 1000).toISOString();
    expect(formatSlaCountdown(future, "processing", now)).toEqual({
      text: "Còn 1 giờ 30 phút",
      overdue: false,
    });
    expect(formatSlaCountdown(past, "processing", now)).toEqual({
      text: "Quá hạn 1 giờ 15 phút",
      overdue: true,
    });
  });
});

describe("W8 workflow builder & inactive form field helpers", () => {
  it("skips deactivated form fields in validateDynamicForm", () => {
    const fields: WfFormField[] = [
      { id: 10, label: "Trường cũ", type: "text", required: true, placeholder: "", options: null, order: 1, is_active: false },
      { id: 11, label: "Trường mới", type: "text", required: true, placeholder: "", options: null, order: 2, is_active: true },
    ];
    expect(validateDynamicForm(fields, { "11": "OK" })).toEqual({});
    expect(validateDynamicForm(fields, {})).toEqual({ "11": 'Vui lòng nhập "Trường mới"' });
  });

  it("validates builder drafts for stages, SLA, select options, members and admin role", () => {
    const baseDraft: WfBuilderDraft = {
      name: "",
      slug: "",
      category: "",
      description: "",
      guide_html: "",
      visibility: "followers_only",
      status: "active",
      stages: [
        {
          localKey: "s1",
          name: "Tiếp nhận",
          type: "normal",
          sla_hours: 0,
          assign_mode: "all",
          guide_html: "",
          member_ids: [],
          is_active: true,
        },
        {
          localKey: "s2",
          name: "tiếp nhận",
          type: "sign",
          sla_hours: 4,
          assign_mode: "all",
          guide_html: "",
          member_ids: [2],
          is_active: true,
        },
      ],
      form_fields: [
        {
          localKey: "f1",
          label: "Mức độ",
          type: "select",
          required: false,
          placeholder: "",
          options: [],
          is_active: true,
        },
      ],
      roles: [{ user_id: 2, role: "creator" }],
      failure_reasons: [],
    };
    const errs = validateWorkflowDraft(baseDraft, true);
    expect(errs.some((e) => e.includes("tên quy trình"))).toBe(true);
    expect(errs.some((e) => e.includes("trùng lặp"))).toBe(true);
    expect(errs.some((e) => e.includes("SLA"))).toBe(true);
    expect(errs.some((e) => e.includes("thành viên"))).toBe(true);
    expect(errs.some((e) => e.includes("lựa chọn"))).toBe(true);
    expect(errs.some((e) => e.includes("admin"))).toBe(true);

    const validDraft: WfBuilderDraft = {
      ...baseDraft,
      name: "Quy trình chuẩn",
      status: "closed",
      stages: [
        {
          localKey: "s1",
          name: "Bước 1",
          type: "normal",
          sla_hours: 8,
          assign_mode: "all",
          guide_html: "",
          member_ids: [],
          is_active: true,
        },
      ],
      form_fields: [],
      roles: [{ user_id: 1, role: "admin" }],
    };
    expect(validateWorkflowDraft(validDraft, true)).toEqual([]);
  });
});
