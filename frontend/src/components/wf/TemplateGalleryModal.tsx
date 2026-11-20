import React, { useEffect, useMemo, useState } from "react";
import { fetchTemplates, type WfTemplate } from "../../lib/wfApi";
import { SafeHtml } from "./SafeHtml";
import { StatusPill } from "./StatusPill";

interface Props {
  open: boolean;
  onClose: () => void;
  onUseTemplate: (tpl: WfTemplate) => void;
}

export function TemplateGalleryModal({ open, onClose, onUseTemplate }: Props) {
  const [templates, setTemplates] = useState<WfTemplate[]>([]);
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setLoading(true);
    setErr("");
    fetchTemplates()
      .then((items) => {
        if (!alive) return;
        setTemplates(items);
        if (items.length > 0) {
          setSelectedId((prev) => (prev && items.some((t) => t.id === prev) ? prev : items[0].id));
        }
      })
      .catch((ex) => {
        if (!alive) return;
        setErr(ex instanceof Error ? ex.message : "Không tải được thư viện mẫu quy trình");
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [open]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return templates;
    return templates.filter(
      (t) =>
        t.name.toLowerCase().includes(q) ||
        (t.category || "").toLowerCase().includes(q) ||
        (t.description || "").toLowerCase().includes(q),
    );
  }, [templates, search]);

  const grouped = useMemo(() => {
    const map = new Map<string, WfTemplate[]>();
    for (const t of filtered) {
      const cat = (t.category || "").trim() || "Khác";
      const arr = map.get(cat) || [];
      arr.push(t);
      map.set(cat, arr);
    }
    return Array.from(map.entries());
  }, [filtered]);

  const selected = useMemo(
    () => filtered.find((t) => t.id === selectedId) || filtered[0] || null,
    [filtered, selectedId],
  );

  if (!open) return null;

  const totalSla = selected
    ? selected.definition.stages.reduce((acc, s) => acc + (s.sla_hours && s.sla_hours > 0 ? s.sla_hours : 0), 0)
    : 0;

  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="Thư viện mẫu quy trình"
      data-testid="template-gallery-modal"
    >
      <div className="modal-card xwide stack">
        <div className="preview-toolbar">
          <div>
            <h3 style={{ margin: 0 }}>Thư viện mẫu quy trình nội bộ</h3>
            <div className="muted">Chọn một mẫu để xem trước các giai đoạn, biểu mẫu và nạp vào trình thiết kế.</div>
          </div>
          <button type="button" className="btn tiny" onClick={onClose} data-testid="tpl-close-btn">
            Đóng
          </button>
        </div>

        {err && <div className="alert error">{err}</div>}

        <div className="wf-tpl-split">
          <div className="wf-tpl-list stack">
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Tìm kiếm mẫu quy trình..."
              data-testid="tpl-search"
            />
            {loading ? (
              <div className="muted">Đang tải danh sách mẫu...</div>
            ) : grouped.length === 0 ? (
              <div className="muted">Không có mẫu nào phù hợp.</div>
            ) : (
              grouped.map(([cat, list]) => (
                <div key={cat} className="stack" style={{ gap: 4 }}>
                  <div className="wf-sidebar-title" style={{ paddingLeft: 0 }}>{cat}</div>
                  {list.map((tpl) => {
                    const active = selected?.id === tpl.id;
                    return (
                      <button
                        key={tpl.id}
                        type="button"
                        className={`wf-tpl-item ${active ? "active" : ""}`}
                        onClick={() => setSelectedId(tpl.id)}
                        data-testid={`tpl-item-${tpl.slug}`}
                      >
                        <div className="row" style={{ justifyContent: "space-between", width: "100%" }}>
                          <strong>{tpl.name}</strong>
                          <span className={`badge ${tpl.is_builtin ? "ok" : "muted"}`}>
                            {tpl.is_builtin ? "Mẫu sẵn" : "Tự tạo"}
                          </span>
                        </div>
                        <div className="muted">
                          {tpl.definition.stages.length} bước · {tpl.definition.form_fields.length} trường form
                        </div>
                      </button>
                    );
                  })}
                </div>
              ))
            )}
          </div>

          <div className="wf-tpl-preview stack" data-testid="tpl-preview-pane">
            {!selected ? (
              <div className="muted">Chọn một mẫu ở cột bên trái để xem chi tiết.</div>
            ) : (
              <>
                <div className="row wrap" style={{ justifyContent: "space-between" }}>
                  <div className="stack" style={{ gap: 4 }}>
                    <div className="row wrap">
                      <h3 style={{ margin: 0 }}>{selected.name}</h3>
                      <span className="badge muted">{selected.category || "Chưa phân loại"}</span>
                      <span className="badge muted">
                        {selected.definition.visibility === "all_visible" ? "Mọi vai trò thấy" : "Theo người tham gia"}
                      </span>
                    </div>
                    {selected.description && <div className="muted">{selected.description}</div>}
                  </div>
                </div>

                {selected.definition.guide_html && (
                  <div className="card" style={{ padding: 10, background: "#f8fafc" }}>
                    <SafeHtml html={selected.definition.guide_html} />
                  </div>
                )}

                <div className="stack" style={{ gap: 6 }}>
                  <div className="row" style={{ justifyContent: "space-between" }}>
                    <strong>Chuỗi giai đoạn ({selected.definition.stages.length} bước)</strong>
                    <span className="muted">Tổng SLA: {totalSla} giờ</span>
                  </div>
                  <div className="stack" style={{ gap: 6 }}>
                    {selected.definition.stages.map((st, idx) => (
                      <div key={idx} className="wf-tpl-stage-row">
                        <div className="row wrap">
                          <span className="badge muted">Bước {idx + 1}</span>
                          <strong>{st.name}</strong>
                          <StatusPill kind="stage" status={st.type} />
                        </div>
                        <div className="muted">
                          {st.sla_hours ? `SLA ${st.sla_hours}h` : "Không giới hạn"} ·{" "}
                          {st.assign_mode === "any" ? "Bất kỳ 1 người" : "Tất cả thành viên"}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                {selected.definition.form_fields.length > 0 && (
                  <div className="stack" style={{ gap: 4 }}>
                    <strong>Biểu mẫu động ({selected.definition.form_fields.length} trường)</strong>
                    <div className="row wrap">
                      {selected.definition.form_fields.map((f, idx) => (
                        <span key={idx} className="wf-chip">
                          {f.label} ({f.type}){f.required ? " *" : ""}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {selected.definition.failure_reasons.length > 0 && (
                  <div className="stack" style={{ gap: 4 }}>
                    <strong>Lý do thất bại ({selected.definition.failure_reasons.length})</strong>
                    <div className="row wrap">
                      {selected.definition.failure_reasons.map((r, idx) => (
                        <span key={idx} className="badge muted">{r}</span>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        </div>

        <div className="row" style={{ justifyContent: "flex-end", paddingTop: 8, borderTop: "1px solid var(--border)" }}>
          <button type="button" className="btn small" onClick={onClose}>
            Đóng
          </button>
          <button
            type="button"
            className="btn small primary"
            disabled={!selected}
            onClick={() => {
              if (selected) onUseTemplate(selected);
            }}
            data-testid="tpl-use-btn"
          >
            Dùng mẫu này
          </button>
        </div>
      </div>
    </div>
  );
}
