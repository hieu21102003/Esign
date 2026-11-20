import React, { useMemo, useState } from "react";
import type { UserBrief } from "../../lib/api";
import { initialsOf, userDisplayName } from "../../lib/wfApi";

export type PickerUser = {
  id: number | null;
  username: string;
  display_name: string;
  job_title?: string;
  department?: string;
};

interface Props {
  options: PickerUser[];
  selectedIds: number[];
  onChange: (ids: number[]) => void;
  multiple?: boolean;
  placeholder?: string;
  emptyText?: string;
  testIdPrefix?: string;
}

export function UserPicker({
  options,
  selectedIds,
  onChange,
  multiple = true,
  placeholder = "Tìm theo tên hoặc tài khoản...",
  emptyText = "Không có người dùng phù hợp.",
  testIdPrefix = "wf-user-picker",
}: Props) {
  const [query, setQuery] = useState("");
  const validOptions = useMemo(
    () => options.filter((u): u is PickerUser & { id: number } => typeof u.id === "number"),
    [options],
  );
  const byId = useMemo(() => new Map(validOptions.map((u) => [u.id, u])), [validOptions]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return validOptions;
    return validOptions.filter(
      (u) =>
        (u.display_name || "").toLowerCase().includes(q) ||
        u.username.toLowerCase().includes(q) ||
        (u.department || "").toLowerCase().includes(q),
    );
  }, [validOptions, query]);

  function toggle(uid: number) {
    if (!multiple) {
      onChange(selectedIds.includes(uid) ? [] : [uid]);
      return;
    }
    onChange(selectedIds.includes(uid) ? selectedIds.filter((x) => x !== uid) : [...selectedIds, uid]);
  }

  return (
    <div className="wf-user-picker">
      {selectedIds.length > 0 && (
        <div className="row wrap" style={{ marginBottom: 6 }}>
          {selectedIds.map((uid) => {
            const u = byId.get(uid);
            const label = u ? (u.display_name || u.username) : `#${uid}`;
            return (
              <span key={uid} className="wf-chip">
                <span>{label}</span>
                <button
                  type="button"
                  className="wf-chip-x"
                  aria-label={`Bỏ chọn ${label}`}
                  onClick={() => toggle(uid)}
                >
                  ×
                </button>
              </span>
            );
          })}
        </div>
      )}
      {validOptions.length > 5 && (
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={placeholder}
          style={{ marginBottom: 6 }}
          data-testid={`${testIdPrefix}-search`}
        />
      )}
      <div className="wf-picker-list">
        {filtered.length === 0 ? (
          <div className="muted" style={{ padding: 4 }}>{emptyText}</div>
        ) : (
          filtered.map((u) => {
            const checked = selectedIds.includes(u.id);
            return (
              <label key={u.id} className="wf-picker-item">
                <input
                  type={multiple ? "checkbox" : "radio"}
                  checked={checked}
                  onChange={() => toggle(u.id)}
                  data-testid={`${testIdPrefix}-${u.username}`}
                />
                <span className="wf-avatar tiny">{initialsOf(u.display_name || u.username)}</span>
                <span>
                  <strong>{userDisplayName(u as UserBrief)}</strong>
                  {(u.job_title || u.department) && (
                    <small className="muted"> · {[u.job_title, u.department].filter(Boolean).join(" / ")}</small>
                  )}
                </span>
              </label>
            );
          })
        )}
      </div>
    </div>
  );
}
