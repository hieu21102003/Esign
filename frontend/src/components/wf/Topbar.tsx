import React, { useEffect, useRef, useState } from "react";

export interface TopbarViewTab {
  id: string;
  label: string;
  testId?: string;
}

export interface TopbarMenuItem {
  id: string;
  label: string;
  onSelect: () => void;
  danger?: boolean;
  testId?: string;
}

interface TopbarProps {
  title: string;
  subtitle?: string;
  backLabel?: string;
  onBack?: () => void;
  viewTabs?: TopbarViewTab[];
  activeViewTab?: string;
  onViewTabChange?: (id: string) => void;
  counter?: string;
  search?: string;
  searchPlaceholder?: string;
  onSearchChange?: (val: string) => void;
  filterCount?: number;
  onOpenFilter?: () => void;
  primaryAction?: {
    label: string;
    onClick: () => void;
    disabled?: boolean;
    testId?: string;
  } | null;
  secondaryActions?: React.ReactNode;
  menuItems?: TopbarMenuItem[];
}

export function Topbar({
  title,
  subtitle,
  backLabel,
  onBack,
  viewTabs,
  activeViewTab,
  onViewTabChange,
  counter,
  search,
  searchPlaceholder = "Tìm kiếm...",
  onSearchChange,
  filterCount,
  onOpenFilter,
  primaryAction,
  secondaryActions,
  menuItems,
}: TopbarProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!menuOpen) return;
    function onDown(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [menuOpen]);

  return (
    <div className="wf-topbar card" data-testid="wf-topbar">
      <div className="wf-topbar-left">
        {onBack && (
          <button
            type="button"
            className="btn small"
            onClick={onBack}
            data-testid="wf-topbar-back"
          >
            ← {backLabel || "Quay lại"}
          </button>
        )}
        <div className="wf-topbar-titles">
          <div className="row wrap">
            <h2 style={{ margin: 0 }}>{title}</h2>
            {counter && <span className="badge muted" data-testid="wf-topbar-counter">{counter}</span>}
          </div>
          {subtitle && <div className="muted">{subtitle}</div>}
        </div>
        {viewTabs && viewTabs.length > 0 && onViewTabChange && (
          <div className="wf-segmented" role="tablist">
            {viewTabs.map((t) => (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={activeViewTab === t.id}
                className={`btn tiny ${activeViewTab === t.id ? "primary" : "ghost"}`}
                onClick={() => onViewTabChange(t.id)}
                data-testid={t.testId || `wf-view-tab-${t.id}`}
              >
                {t.label}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="wf-topbar-right">
        {onSearchChange !== undefined && (
          <input
            type="search"
            className="wf-topbar-search"
            value={search ?? ""}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder={searchPlaceholder}
            aria-label={searchPlaceholder}
            data-testid="wf-topbar-search"
          />
        )}
        {onOpenFilter && (
          <button
            type="button"
            className={`btn small ${filterCount && filterCount > 0 ? "mode" : ""}`}
            onClick={onOpenFilter}
            data-testid="wf-open-filter-btn"
          >
            Bộ lọc{filterCount && filterCount > 0 ? ` (${filterCount})` : ""}
          </button>
        )}
        {secondaryActions}
        {primaryAction && (
          <button
            type="button"
            className="btn small primary"
            disabled={primaryAction.disabled}
            onClick={primaryAction.onClick}
            data-testid={primaryAction.testId || "wf-primary-action"}
          >
            {primaryAction.label}
          </button>
        )}
        {menuItems && menuItems.length > 0 && (
          <div className="wf-menu-wrap" ref={menuRef}>
            <button
              type="button"
              className="btn small"
              aria-label="Menu tùy chọn"
              onClick={() => setMenuOpen((o) => !o)}
              data-testid="wf-topbar-menu-btn"
            >
              ⋯
            </button>
            {menuOpen && (
              <div className="wf-menu-dropdown" data-testid="wf-topbar-menu">
                {menuItems.map((it) => (
                  <button
                    key={it.id}
                    type="button"
                    className={`wf-menu-item ${it.danger ? "danger" : ""}`}
                    onClick={() => {
                      setMenuOpen(false);
                      it.onSelect();
                    }}
                    data-testid={it.testId || `wf-menu-${it.id}`}
                  >
                    {it.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
