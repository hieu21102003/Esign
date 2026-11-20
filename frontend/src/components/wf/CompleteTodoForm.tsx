import React, { useState } from "react";
import type { WfStage } from "../../lib/wfApi";
import { UserPicker } from "./UserPicker";

interface Props {
  nextStage: WfStage | null;
  busy?: boolean;
  onSubmit: (payload: { result: string; next_assignee_ids?: number[] }) => Promise<void> | void;
}

export function CompleteTodoForm({ nextStage, busy = false, onSubmit }: Props) {
  const [result, setResult] = useState("");
  const [nextIds, setNextIds] = useState<number[]>([]);
  const nextMembers = nextStage?.members || [];

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    await onSubmit({
      result: result.trim(),
      next_assignee_ids: nextIds.length > 0 ? nextIds : undefined,
    });
  }

  return (
    <form className="stack" onSubmit={handleSubmit} data-testid="complete-todo-form">
      <label className="field">
        <span>Kết quả công việc</span>
        <textarea
          rows={3}
          value={result}
          onChange={(e) => setResult(e.target.value)}
          placeholder="Nhập kết quả xử lý bước hiện tại (không bắt buộc)..."
          disabled={busy}
          data-testid="complete-todo-result"
        />
      </label>

      {nextStage && nextMembers.length > 1 && (
        <div className="field">
          <span>Chọn người nhận bước tiếp theo: {nextStage.name} (tùy chọn — bỏ trống để giao cả nhóm)</span>
          <UserPicker
            options={nextMembers}
            selectedIds={nextIds}
            onChange={setNextIds}
            multiple={nextStage.assign_mode === "all"}
            testIdPrefix="complete-next-assignee"
          />
        </div>
      )}

      <div className="row" style={{ justifyContent: "flex-end" }}>
        <button
          type="submit"
          className="btn small primary"
          disabled={busy}
          data-testid="complete-todo-submit"
        >
          {busy ? "Đang xử lý..." : "Hoàn thành"}
        </button>
      </div>
    </form>
  );
}
