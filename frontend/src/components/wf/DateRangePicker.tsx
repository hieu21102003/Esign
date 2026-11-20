import React from "react";

interface Props {
  label: string;
  from: string;
  to: string;
  onChange: (from: string, to: string) => void;
  testIdPrefix?: string;
}

export function DateRangePicker({ label, from, to, onChange, testIdPrefix }: Props) {
  return (
    <div className="field">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span>{label}</span>
        {(from || to) && (
          <button
            type="button"
            className="btn tiny ghost"
            onClick={() => onChange("", "")}
          >
            Xóa
          </button>
        )}
      </div>
      <div className="two-col">
        <input
          type="date"
          value={from}
          onChange={(e) => onChange(e.target.value, to)}
          aria-label={`${label} từ ngày`}
          data-testid={testIdPrefix ? `${testIdPrefix}-from` : undefined}
        />
        <input
          type="date"
          value={to}
          onChange={(e) => onChange(from, e.target.value)}
          aria-label={`${label} đến ngày`}
          data-testid={testIdPrefix ? `${testIdPrefix}-to` : undefined}
        />
      </div>
    </div>
  );
}
