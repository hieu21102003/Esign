import React from "react";
import type { TimelineEvent } from "../../lib/api";
import { formatDateTime } from "../../lib/wfApi";

const ACTION_LABELS: Record<string, string> = {
  task_created: "Tạo nhiệm vụ",
  stage_entered: "Chuyển vào giai đoạn",
  todo_assigned: "Giao việc cần làm",
  todo_completed: "Hoàn thành việc cần làm",
  todo_failed: "Thất bại ở việc cần làm",
  todo_cancelled: "Hủy việc cần làm mở",
  task_completed: "Nhiệm vụ hoàn thành",
  task_failed: "Nhiệm vụ thất bại",
  task_cancelled: "Hủy nhiệm vụ",
  task_edited: "Cập nhật nhiệm vụ",
  task_archived: "Lưu trữ nhiệm vụ",
  task_restored: "Khôi phục nhiệm vụ",
  follower_added: "Thêm người theo dõi",
  follower_removed: "Gỡ người theo dõi",
  task_document_attached: "Đính kèm tài liệu",
  doc_create: "Tạo tài liệu ký",
  doc_assign: "Mở bước ký số",
  doc_sign: "Ký số tài liệu",
  doc_reject: "Từ chối ký tài liệu",
  doc_complete: "Hoàn tất chữ ký tài liệu",
  doc_cancel: "Hủy bước ký tài liệu",
  download: "Tải xuống tài liệu",
  verify: "Xác minh chữ ký PDF",
  sign_failed: "Ký số thất bại",
  sign_denied: "Từ chối quyền ký",
};

function summarizeMeta(ev: TimelineEvent): string {
  const m = ev.metadata || {};
  const parts: string[] = [];
  if (typeof m.stage_name === "string" && m.stage_name) parts.push(`Bước: ${m.stage_name}`);
  if (typeof m.decision === "string" && m.decision) {
    parts.push(`Quyết định: ${m.decision === "approve" ? "Phê duyệt" : m.decision === "reject" ? "Từ chối" : m.decision}`);
  }
  if (typeof m.version_no === "number") parts.push(`Phiên bản: v${m.version_no}`);
  if (typeof m.failure_reason === "string" && m.failure_reason) parts.push(`Lý do: ${m.failure_reason}`);
  if (ev.cert_key_ref) parts.push(`Cert: ${ev.cert_key_ref}`);
  if (ev.detail) parts.push(ev.detail);
  return parts.join(" · ");
}

export function ActivityTimeline({ events }: { events: TimelineEvent[] }) {
  if (events.length === 0) {
    return <div className="muted">Chưa có sự kiện lịch sử nào.</div>;
  }

  return (
    <div className="wf-activity-list" data-testid="activity-timeline">
      {events.map((ev) => {
        const metaText = summarizeMeta(ev);
        const isErr = ev.status !== "ok" || ev.action.includes("fail") || ev.action.includes("reject") || ev.action.includes("cancel");
        return (
          <div key={ev.id} className={`wf-activity-item ${isErr ? "warn" : ""}`}>
            <div className="wf-activity-dot" />
            <div className="wf-activity-content">
              <div className="row wrap" style={{ justifyContent: "space-between" }}>
                <strong>{ACTION_LABELS[ev.action] || ev.action}</strong>
                <span className="muted mono">{formatDateTime(ev.ts)}</span>
              </div>
              <div className="muted">
                Thực hiện: <strong>{ev.username || "Hệ thống"}</strong>
                {ev.ip ? ` · IP ${ev.ip}` : ""}
              </div>
              {metaText && <div className="wf-activity-detail">{metaText}</div>}
            </div>
          </div>
        );
      })}
    </div>
  );
}
