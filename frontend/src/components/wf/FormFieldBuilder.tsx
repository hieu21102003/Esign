import React, { useEffect, useState } from "react";
import type { AssignableUser } from "../../lib/api";
import type { FormFieldType, WfBuilderFieldDraft } from "../../lib/wfApi";

interface Props {
  fields: WfBuilderFieldDraft[];
  users: AssignableUser[];
  onChange: (next: WfBuilderFieldDraft[]) => void;
}

const FIELD_TYPE_LABELS: Array<{ value: FormFieldType; label: string }> = [
  { value: "text", label: "Chữ ngắn (text)" },
  { value: "textarea", label: "Đoạn văn (textarea)" },
  { value: "number", label: "Số (number)" },
  { value: "date", label: "Ngày (date)" },
  { value: "select", label: "Danh sách chọn (select)" },
  { value: "file", label: "Tệp đính kèm (file)" },
  { value: "user", label: "Người dùng (user)" },
];

export function createEmptyFieldDraft(index: number): WfBuilderFieldDraft {
  return {
    localKey: `fld-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    label: `Trường ${index}`,
    type: "text",
    required: false,
    placeholder: "",
    options: [],
    is_active: true,
  };
}

function SelectOptionsInput({
  options,
  testId,
  onCommit,
}: {
  options: string[];
  testId: string;
  onCommit: (next: string[]) => void;
}) {
  const [raw, setRaw] = useState(() => options.join(", "));
  const joined = options.join(", ");

  useEffect(() => {
    const normalizedCurrent = raw
      .split(/[,\n]/)
      .map((s) => s.trim())
      .filter(Boolean)
      .join(", ");
    const normalizedProp = options.map((s) => s.trim()).filter(Boolean).join(", ");
    if (normalizedCurrent !== normalizedProp) {
      setRaw(joined);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [joined]);

  return (
    <input
      value={raw}
      onChange={(e) => {
        const val = e.target.value;
        setRaw(val);
        onCommit(
          val
            .split(/[,\n]/)
            .map((x) => x.trim())
            .filter(Boolean),
        );
      }}
      placeholder="Ngăn cách bằng dấu phẩy, ví dụ: Khẩn, Bình thường"
      data-testid={testId}
    />
  );
}

export function FormFieldBuilder({ fields, users, onChange }: Props) {
  function updateField(localKey: string, patch: Partial<WfBuilderFieldDraft>) {
    onChange(fields.map((f) => (f.localKey === localKey ? { ...f, ...patch } : f)));
  }

  function moveField(idx: number, delta: -1 | 1) {
    const target = idx + delta;
    if (target < 0 || target >= fields.length) return;
    const copy = [...fields];
    const tmp = copy[idx];
    copy[idx] = copy[target];
    copy[target] = tmp;
    onChange(copy);
  }

  function removeField(f: WfBuilderFieldDraft) {
    onChange(fields.filter((x) => x.localKey !== f.localKey));
  }

  const activeFields = fields.filter((f) => f.is_active !== false);

  return (
    <div className="card stack" data-testid="form-field-builder">
      <div className="row wrap" style={{ justifyContent: "space-between" }}>
        <div>
          <h3 style={{ margin: 0 }}>Biểu mẫu tạo nhiệm vụ ({activeFields.length} trường hoạt động)</h3>
          <div className="muted">
            Trường đã có dữ liệu trên công việc cũ nên chuyển sang "Vô hiệu hóa" thay vì xóa hoặc đổi kiểu.
          </div>
        </div>
        <button
          type="button"
          className="btn small"
          onClick={() => onChange([...fields, createEmptyFieldDraft(fields.length + 1)])}
          data-testid="field-add-btn"
        >
          + Thêm trường
        </button>
      </div>

      {fields.length === 0 ? (
        <div className="muted">Quy trình chưa có trường biểu mẫu riêng (chỉ dùng tên, mô tả và tệp đính kèm mặc định).</div>
      ) : (
        <div className="stack">
          {fields.map((f, idx) => (
            <div
              key={f.localKey}
              className={`wf-builder-field-card stack ${f.is_active === false ? "inactive" : ""}`}
              data-testid={`field-card-${idx}`}
            >
              <div className="row wrap" style={{ justifyContent: "space-between" }}>
                <div className="row wrap">
                  <span className="badge muted">#{idx + 1}</span>
                  {f.has_data && <span className="badge mode">Đã có dữ liệu</span>}
                  {f.is_active === false && <span className="badge bad">Đã vô hiệu hóa</span>}
                </div>
                <div className="row wrap" style={{ gap: 6 }}>
                  <button
                    type="button"
                    className="btn tiny"
                    disabled={idx === 0}
                    onClick={() => moveField(idx, -1)}
                    aria-label={`Đưa trường ${idx + 1} lên`}
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    className="btn tiny"
                    disabled={idx === fields.length - 1}
                    onClick={() => moveField(idx, 1)}
                    aria-label={`Đưa trường ${idx + 1} xuống`}
                  >
                    ↓
                  </button>
                  {f.id !== undefined && (
                    <button
                      type="button"
                      className="btn tiny"
                      onClick={() => updateField(f.localKey, { is_active: f.is_active === false })}
                      data-testid={`field-toggle-active-${idx}`}
                    >
                      {f.is_active === false ? "Kích hoạt lại" : "Vô hiệu hóa"}
                    </button>
                  )}
                  <button
                    type="button"
                    className="btn tiny danger"
                    onClick={() => removeField(f)}
                    data-testid={`field-remove-${idx}`}
                  >
                    Xóa
                  </button>
                </div>
              </div>

              <div className="wf-builder-field-grid">
                <label className="field">
                  <span>Nhãn hiển thị *</span>
                  <input
                    value={f.label}
                    onChange={(e) => updateField(f.localKey, { label: e.target.value })}
                    placeholder="Ví dụ: Đơn vị gửi"
                    data-testid={`field-label-${idx}`}
                  />
                </label>
                <label className="field">
                  <span>Kiểu dữ liệu</span>
                  <select
                    value={f.type}
                    onChange={(e) => {
                      const nextType = e.target.value as FormFieldType;
                      updateField(f.localKey, {
                        type: nextType,
                        options: nextType === "select" ? (f.options.length > 0 ? f.options : ["Mức 1", "Mức 2"]) : [],
                      });
                    }}
                    data-testid={`field-type-${idx}`}
                  >
                    {FIELD_TYPE_LABELS.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>Placeholder</span>
                  <input
                    value={f.placeholder}
                    onChange={(e) => updateField(f.localKey, { placeholder: e.target.value })}
                    placeholder="Gợi ý nhập..."
                    data-testid={`field-placeholder-${idx}`}
                  />
                </label>
                <label className="check-item" style={{ alignSelf: "end", paddingBottom: 6 }}>
                  <input
                    type="checkbox"
                    checked={f.required}
                    onChange={(e) => updateField(f.localKey, { required: e.target.checked })}
                    data-testid={`field-required-${idx}`}
                  />
                  <span>Bắt buộc</span>
                </label>
              </div>

              {f.type === "select" && (
                <label className="field">
                  <span>Danh sách lựa chọn *</span>
                  <SelectOptionsInput
                    options={f.options}
                    testId={`field-options-${idx}`}
                    onCommit={(opts) => updateField(f.localKey, { options: opts })}
                  />
                </label>
              )}
            </div>
          ))}
        </div>
      )}

      {activeFields.length > 0 && (
        <div className="wf-form-preview stack" data-testid="form-builder-preview">
          <strong className="muted">Xem trước form tạo công việc (chỉ gồm các trường đang hoạt động)</strong>
          <div className="two-col">
            {activeFields.map((f, idx) => (
              <label key={f.localKey} className="field">
                <span>
                  {f.label || `Trường ${idx + 1}`}
                  {f.required && <span className="text-danger"> *</span>}
                </span>
                {f.type === "textarea" ? (
                  <textarea rows={2} disabled placeholder={f.placeholder || "Đoạn văn bản..."} />
                ) : f.type === "select" ? (
                  <select disabled>
                    <option>{f.placeholder || "-- Chọn --"}</option>
                    {f.options.map((o, i) => (
                      <option key={i}>{o}</option>
                    ))}
                  </select>
                ) : f.type === "user" ? (
                  <select disabled>
                    <option>{f.placeholder || `-- Chọn người dùng (${users.length} tài khoản) --`}</option>
                  </select>
                ) : f.type === "file" ? (
                  <input type="text" disabled value="Khung tải tệp đính kèm..." readOnly />
                ) : (
                  <input
                    type={f.type === "date" ? "date" : f.type === "number" ? "number" : "text"}
                    disabled
                    placeholder={f.placeholder || undefined}
                  />
                )}
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
