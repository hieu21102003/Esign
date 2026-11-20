import React, { useCallback, useEffect, useMemo, useState } from "react";
import { apiJson, type MeResponse } from "../../lib/api";
import { useVisibilityPolling } from "../../lib/usePolling";
import { fetchWorkflowStats, type WfStats, type WfSummary, type WfTemplate } from "../../lib/wfApi";
import { EmptyState } from "./EmptyState";
import { ProgressBar } from "./ProgressBar";
import { StatusPill } from "./StatusPill";
import { TemplateGalleryModal } from "./TemplateGalleryModal";
import { Topbar } from "./Topbar";

interface Props {
  me: MeResponse;
  onOpenBoard: (workflowId: number) => void;
  onCreateWorkflow: () => void;
  onCreateFromTemplate: (tpl: WfTemplate) => void;
}

export function WorkflowsListView({ me, onOpenBoard, onCreateWorkflow, onCreateFromTemplate }: Props) {
  const [tab, setTab] = useState<"active" | "closed" | "mine">("active");
  const [search, setSearch] = useState("");
  const [items, setItems] = useState<WfSummary[]>([]);
  const [statsById, setStatsById] = useState<Record<number, WfStats>>({});
  const [tplOpen, setTplOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

  const loadAll = useCallback(async () => {
    setErr("");
    try {
      const res = await apiJson<{ items: WfSummary[] }>("/api/workflows");
      const list = res.items || [];
      setItems(list);
      const entries = await Promise.all(
        list.map(async (w) => {
          try {
            return [w.id, await fetchWorkflowStats(w.id)] as const;
          } catch {
            return [w.id, { processing: 0, completed: 0, failed: 0, overdue: 0, total: 0 }] as const;
          }
        }),
      );
      setStatsById(Object.fromEntries(entries));
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Không tải được danh sách quy trình");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  useVisibilityPolling(loadAll, 30000);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return items.filter((w) => {
      if (tab === "active" && w.status !== "active") return false;
      if (tab === "closed" && w.status !== "closed") return false;
      if (tab === "mine" && w.creator_id !== me.id) return false;
      if (q && !w.name.toLowerCase().includes(q) && !(w.category || "").toLowerCase().includes(q) && !(w.description || "").toLowerCase().includes(q)) {
        return false;
      }
      return true;
    });
  }, [items, tab, search, me.id]);

  const grouped = useMemo(() => {
    const map = new Map<string, WfSummary[]>();
    for (const w of filtered) {
      const cat = (w.category || "").trim() || "Chưa phân loại";
      const arr = map.get(cat) || [];
      arr.push(w);
      map.set(cat, arr);
    }
    return Array.from(map.entries());
  }, [filtered]);

  return (
    <div className="stack" data-testid="workflows-list-view">
      <Topbar
        title="Quản lý quy trình"
        counter={`${filtered.length} quy trình`}
        search={search}
        searchPlaceholder="Tìm tên quy trình hoặc phân loại..."
        onSearchChange={setSearch}
        viewTabs={[
          { id: "active", label: "Đang khả dụng", testId: "wf-list-tab-active" },
          { id: "closed", label: "Đã đóng", testId: "wf-list-tab-closed" },
          { id: "mine", label: "Tạo bởi tôi", testId: "wf-list-tab-mine" },
        ]}
        activeViewTab={tab}
        onViewTabChange={(v) => setTab(v as "active" | "closed" | "mine")}
        secondaryActions={
          me.role === "admin" ? (
            <button
              type="button"
              className="btn small"
              onClick={() => setTplOpen(true)}
              data-testid="wf-open-templates-btn"
            >
              Thư viện mẫu
            </button>
          ) : undefined
        }
        primaryAction={
          me.role === "admin"
            ? {
                label: "+ Tạo quy trình",
                onClick: onCreateWorkflow,
                testId: "wf-create-btn",
              }
            : null
        }
      />

      {err && <div className="alert error">{err}</div>}

      {loading && items.length === 0 ? (
        <div className="card muted">Đang tải danh sách quy trình...</div>
      ) : grouped.length === 0 ? (
        <EmptyState
          title="Chưa có quy trình nào"
          description="Không có quy trình phù hợp mục bạn đang xem."
        />
      ) : (
        grouped.map(([category, wfs]) => (
          <section key={category} className="stack">
            <div className="row">
              <h3 style={{ margin: 0 }}>{category}</h3>
              <span className="badge muted">{wfs.length}</span>
            </div>
            <div className="wf-card-grid">
              {wfs.map((w) => {
                const st = statsById[w.id] || { processing: 0, completed: 0, failed: 0, overdue: 0, total: 0 };
                return (
                  <button
                    key={w.id}
                    type="button"
                    className="card wf-summary-card"
                    onClick={() => onOpenBoard(w.id)}
                    data-testid={`wf-card-${w.slug}`}
                  >
                    <div className="row" style={{ justifyContent: "space-between", width: "100%" }}>
                      <strong className="wf-summary-title">{w.name}</strong>
                      <StatusPill kind="workflow" status={w.status} />
                    </div>
                    {w.description && <div className="muted wf-line-clamp">{w.description}</div>}
                    <div className="row wrap muted">
                      <span>{w.stage_count} bước</span>
                      <span>·</span>
                      <span>{w.visibility === "followers_only" ? "Theo người tham gia" : "Mọi vai trò thấy"}</span>
                    </div>
                    <div className="wf-stat-line">
                      <span>Tổng: <strong>{st.total}</strong></span>
                      <span>Hoàn thành: <strong className="text-ok">{st.completed}</strong></span>
                      <span>Thất bại: <strong className="text-danger">{st.failed}</strong></span>
                      {st.overdue > 0 && <span className="badge bad">{st.overdue} quá hạn</span>}
                    </div>
                    <ProgressBar value={st.completed} max={st.total} failed={st.failed} />
                  </button>
                );
              })}
            </div>
          </section>
        ))
      )}

      <TemplateGalleryModal
        open={tplOpen}
        onClose={() => setTplOpen(false)}
        onUseTemplate={(tpl) => {
          setTplOpen(false);
          onCreateFromTemplate(tpl);
        }}
      />
    </div>
  );
}
