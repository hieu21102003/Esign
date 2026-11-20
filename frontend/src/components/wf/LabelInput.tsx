import React, { useState } from "react";

interface Props {
  labels: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  maxLabels?: number;
  testId?: string;
}

export function LabelInput({
  labels,
  onChange,
  placeholder = "Nhập nhãn rồi nhấn Enter...",
  maxLabels = 20,
  testId = "wf-label-input",
}: Props) {
  const [draft, setDraft] = useState("");

  function commitDraft() {
    const clean = draft.trim();
    if (!clean) return;
    if (labels.includes(clean) || labels.length >= maxLabels) {
      setDraft("");
      return;
    }
    onChange([...labels, clean]);
    setDraft("");
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      commitDraft();
    } else if (e.key === "Backspace" && !draft && labels.length > 0) {
      onChange(labels.slice(0, -1));
    }
  }

  return (
    <div className="wf-label-box">
      <div className="row wrap">
        {labels.map((lb) => (
          <span key={lb} className="wf-chip">
            <span>{lb}</span>
            <button
              type="button"
              className="wf-chip-x"
              aria-label={`Xóa nhãn ${lb}`}
              onClick={() => onChange(labels.filter((x) => x !== lb))}
            >
              ×
            </button>
          </span>
        ))}
      </div>
      <div className="row">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={commitDraft}
          placeholder={placeholder}
          data-testid={testId}
        />
      </div>
    </div>
  );
}
