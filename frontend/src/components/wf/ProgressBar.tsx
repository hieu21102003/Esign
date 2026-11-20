import React from "react";

interface Props {
  value: number;
  max: number;
  failed?: number;
  label?: string;
}

export function ProgressBar({ value, max, failed = 0, label }: Props) {
  const safeMax = max > 0 ? max : 1;
  const pct = max > 0 ? Math.min(100, Math.round((value / safeMax) * 100)) : 0;
  const failPct = max > 0 ? Math.min(100 - pct, Math.round((failed / safeMax) * 100)) : 0;
  return (
    <div className="wf-progress-wrap">
      {label && <div className="muted">{label}</div>}
      <div
        className="wf-progress-track"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={max}
        aria-valuenow={value}
      >
        {pct > 0 && <div className="wf-progress-fill ok" style={{ width: `${pct}%` }} />}
        {failPct > 0 && <div className="wf-progress-fill bad" style={{ width: `${failPct}%` }} />}
      </div>
    </div>
  );
}
