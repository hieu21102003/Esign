import React from "react";

interface Props {
  title: string;
  description?: string;
  actionLabel?: string;
  onAction?: () => void;
  testId?: string;
}

export function EmptyState({ title, description, actionLabel, onAction, testId }: Props) {
  return (
    <div className="wf-empty-state card" data-testid={testId || "wf-empty-state"}>
      <strong>{title}</strong>
      {description && <p className="muted" style={{ margin: 0 }}>{description}</p>}
      {actionLabel && onAction && (
        <button type="button" className="btn small primary" onClick={onAction}>
          {actionLabel}
        </button>
      )}
    </div>
  );
}
