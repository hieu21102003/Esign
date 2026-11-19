import React, { useEffect, useRef, useState } from "react";
import { apiJson, type NotificationItem, type NotificationsResponse } from "../lib/api";

interface Props {
  refreshSignal: number;
  onOpenDocument: (docId: string) => void;
}

export function NotificationBell({ refreshSignal, onOpenDocument }: Props) {
  const [open, setOpen] = useState(false);
  const [unreadCount, setUnreadCount] = useState(0);
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [loading, setLoading] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  async function refreshCount() {
    try {
      const res = await apiJson<{ unread_count: number }>("/api/notifications/unread-count");
      setUnreadCount(res.unread_count);
    } catch {
      // ignore transient polling failure
    }
  }

  async function loadList() {
    setLoading(true);
    try {
      const res = await apiJson<NotificationsResponse>("/api/notifications?limit=30");
      setUnreadCount(res.unread_count);
      setItems(res.items);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refreshCount();
    const timer = window.setInterval(refreshCount, 30000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (refreshSignal > 0) {
      refreshCount();
      if (open) {
        loadList().catch(() => {});
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshSignal]);

  useEffect(() => {
    function onDocMouseDown(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    if (open) {
      document.addEventListener("mousedown", onDocMouseDown);
      return () => document.removeEventListener("mousedown", onDocMouseDown);
    }
  }, [open]);

  function toggleDropdown() {
    const next = !open;
    setOpen(next);
    if (next) {
      loadList().catch(() => {});
    }
  }

  async function handleReadAll() {
    try {
      await apiJson("/api/notifications/read-all", { method: "POST" });
      setUnreadCount(0);
      setItems((prev) => prev.map((it) => ({ ...it, is_read: true })));
    } catch {
      // ignore
    }
  }

  async function handleItemClick(item: NotificationItem) {
    if (!item.is_read) {
      try {
        const res = await apiJson<{ unread_count: number }>(`/api/notifications/${item.id}/read`, {
          method: "POST",
        });
        setUnreadCount(res.unread_count);
        setItems((prev) => prev.map((it) => (it.id === item.id ? { ...it, is_read: true } : it)));
      } catch {
        // ignore
      }
    }
    setOpen(false);
    if (item.document_id) {
      onOpenDocument(item.document_id);
    }
  }

  return (
    <div className="notif-wrap" ref={wrapRef}>
      <button
        type="button"
        className="btn small notif-btn"
        onClick={toggleDropdown}
        data-testid="notif-bell"
        aria-label="Thông báo"
      >
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
          <path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.73 21a2 2 0 0 1-3.46 0" />
        </svg>
        {unreadCount > 0 && (
          <span className="notif-badge" data-testid="notif-badge">
            {unreadCount > 99 ? "99+" : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div className="notif-dropdown" data-testid="notif-dropdown">
          <div className="notif-header">
            <strong>Thông báo ({unreadCount} chưa đọc)</strong>
            <button
              type="button"
              className="btn tiny"
              onClick={handleReadAll}
              disabled={unreadCount === 0}
              data-testid="notif-read-all"
            >
              Đánh dấu đã đọc tất cả
            </button>
          </div>
          <div className="notif-list">
            {loading && items.length === 0 ? (
              <div className="notif-empty muted">Đang tải thông báo...</div>
            ) : items.length === 0 ? (
              <div className="notif-empty muted">Chưa có thông báo nào</div>
            ) : (
              items.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`notif-item ${item.is_read ? "" : "unread"}`}
                  onClick={() => handleItemClick(item)}
                  data-testid={`notif-item-${item.id}`}
                >
                  <div className="notif-item-title">
                    <span>{item.title}</span>
                    {!item.is_read && <span className="notif-dot" />}
                  </div>
                  <div className="notif-item-msg">{item.message}</div>
                  <div className="muted mono">{item.created_at.slice(0, 16).replace("T", " ")}</div>
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
