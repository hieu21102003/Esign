import React, { useCallback, useEffect, useState } from "react";
import { AccountSettingsView } from "./components/AccountSettingsView";
import { AdminView } from "./components/AdminView";
import { DocumentsView } from "./components/DocumentsView";
import { LoginView } from "./components/LoginView";
import { NotificationBell } from "./components/NotificationBell";
import { OnboardingWizard } from "./components/OnboardingWizard";
import { SignWorkspace } from "./components/SignWorkspace";
import { VerifyView } from "./components/VerifyView";
import { WorkflowView } from "./components/WorkflowView";
import { JobsView } from "./components/wf/JobsView";
import { TaskDetailView } from "./components/wf/TaskDetailView";
import { TodosView } from "./components/wf/TodosView";
import { WorkflowBoardView } from "./components/wf/WorkflowBoardView";
import { WorkflowBuilderView } from "./components/wf/WorkflowBuilderView";
import { WorkflowPeopleView } from "./components/wf/WorkflowPeopleView";
import { WorkflowsListView } from "./components/wf/WorkflowsListView";
import { apiJson, setCsrfToken, type MeResponse, type StoredFile } from "./lib/api";
import { navigateTo, parseRoute, type AppRoute } from "./lib/router";
import { useVisibilityPolling } from "./lib/usePolling";
import type { WfSummary, WfTemplate } from "./lib/wfApi";

type LegacyTab = "sign" | "workflow" | "docs" | "verify" | "account" | "admin";

export function App() {
  const [me, setMe] = useState<MeResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [route, setRoute] = useState<AppRoute>(() =>
    typeof window !== "undefined" ? parseRoute(window.location.pathname) : { kind: "legacy" },
  );
  const [tab, setTab] = useState<LegacyTab>("sign");
  const [verifyFileId, setVerifyFileId] = useState<string | null>(null);
  const [openSignFile, setOpenSignFile] = useState<StoredFile | null>(null);
  const [openWorkflowDocId, setOpenWorkflowDocId] = useState<string | null>(null);
  const [pendingUrlDocId, setPendingUrlDocId] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    const init = parseRoute(window.location.pathname);
    return init.kind === "legacy" ? (init.docId || null) : null;
  });
  const [workflowResetTick, setWorkflowResetTick] = useState(0);
  const [notifTick, setNotifTick] = useState(0);
  const [sidebarWorkflows, setSidebarWorkflows] = useState<WfSummary[]>([]);
  const [pendingTemplate, setPendingTemplate] = useState<WfTemplate | null>(null);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  const refreshMe = useCallback(async () => {
    try {
      const data = await apiJson<MeResponse>("/api/auth/me");
      setCsrfToken(data.csrf_token);
      setMe(data);
    } catch {
      setCsrfToken("");
      setMe(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadSidebarWorkflows = useCallback(async () => {
    try {
      const res = await apiJson<{ items: WfSummary[] }>("/api/workflows");
      setSidebarWorkflows((res.items || []).filter((w) => w.status === "active"));
    } catch {
      // ignore transient errors
    }
  }, []);

  useEffect(() => {
    refreshMe();
  }, [refreshMe]);

  useEffect(() => {
    function onPopState() {
      const next = parseRoute(window.location.pathname);
      setRoute(next);
      if (next.kind === "legacy" && next.legacyTab === "workflow") {
        setTab("workflow");
        if (next.docId) setOpenWorkflowDocId(next.docId);
      }
      setMobileNavOpen(false);
    }
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const authed = Boolean(me && me.mfa_verified && !me.onboarding_required);

  useEffect(() => {
    if (authed) {
      loadSidebarWorkflows();
    } else {
      setSidebarWorkflows([]);
    }
  }, [authed, loadSidebarWorkflows]);

  useVisibilityPolling(loadSidebarWorkflows, 30000, authed);

  useEffect(() => {
    if (authed && pendingUrlDocId) {
      setOpenWorkflowDocId(pendingUrlDocId);
      setTab("workflow");
      setRoute({ kind: "legacy", legacyTab: "workflow", docId: pendingUrlDocId });
      setPendingUrlDocId(null);
    }
  }, [authed, pendingUrlDocId]);

  function goPath(path: string) {
    navigateTo(path);
    setRoute(parseRoute(path.split("?")[0]));
    setMobileNavOpen(false);
  }

  function switchLegacyTab(next: LegacyTab) {
    if (typeof window !== "undefined" && window.location.pathname !== "/") {
      navigateTo("/", true);
    }
    setRoute({ kind: "legacy" });
    setTab(next);
    setMobileNavOpen(false);
  }

  async function handleLogout() {
    try {
      await apiJson("/api/auth/logout", { method: "POST" });
    } catch {
      // ignore
    }
    setCsrfToken("");
    setMe(null);
  }

  if (loading) {
    return <div className="auth-wrap"><div className="muted">Đang tải...</div></div>;
  }

  if (!me || !me.mfa_verified) {
    const initialStep = !me ? "login" : me.totp_enrolled ? "totp_verify" : "totp_setup";
    return <LoginView key={initialStep} initialStep={initialStep} onAuthed={refreshMe} />;
  }

  if (me.onboarding_required) {
    return <OnboardingWizard me={me} onCompleted={refreshMe} onLogout={handleLogout} />;
  }

  const isLegacy = route.kind === "legacy";

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="row wrap">
          <button
            type="button"
            className="btn small wf-mobile-nav-btn"
            onClick={() => setMobileNavOpen((o) => !o)}
            aria-label="Mở menu quy trình"
            data-testid="mobile-nav-toggle"
          >
            ☰ Quy trình
          </button>
          <strong className="brand-title">esign</strong>
          <nav className="nav-tabs">
            <button
              type="button"
              className={`btn small ${isLegacy && tab === "sign" ? "primary" : "ghost"}`}
              onClick={() => switchLegacyTab("sign")}
              data-testid="tab-sign"
            >
              Ký số PDF
            </button>
            <button
              type="button"
              className={`btn small ${isLegacy && tab === "workflow" ? "primary" : "ghost"}`}
              onClick={() => {
                setOpenWorkflowDocId(null);
                setWorkflowResetTick((n) => n + 1);
                switchLegacyTab("workflow");
              }}
              data-testid="tab-workflow"
            >
              Luồng ký
            </button>
            <button
              type="button"
              className={`btn small ${isLegacy && tab === "docs" ? "primary" : "ghost"}`}
              onClick={() => switchLegacyTab("docs")}
              data-testid="tab-docs"
            >
              Tài liệu
            </button>
            <button
              type="button"
              className={`btn small ${isLegacy && tab === "verify" ? "primary" : "ghost"}`}
              onClick={() => {
                setVerifyFileId(null);
                switchLegacyTab("verify");
              }}
              data-testid="tab-verify"
            >
              Xác minh
            </button>
            <button
              type="button"
              className={`btn small ${isLegacy && tab === "account" ? "primary" : "ghost"}`}
              onClick={() => switchLegacyTab("account")}
              data-testid="tab-account"
            >
              Tài khoản{!me.has_signing_pin ? " (!)" : ""}
            </button>
            {me.role === "admin" && (
              <button
                type="button"
                className={`btn small ${isLegacy && tab === "admin" ? "primary" : "ghost"}`}
                onClick={() => switchLegacyTab("admin")}
                data-testid="tab-admin"
              >
                Quản trị
              </button>
            )}
          </nav>
        </div>

        <div className="row">
          <NotificationBell
            refreshSignal={notifTick}
            onOpenDocument={(docId) => {
              navigateTo("/", true);
              setRoute({ kind: "legacy" });
              setOpenWorkflowDocId(docId);
              setTab("workflow");
            }}
            onOpenTask={(tid) => goPath(`/tasks/${tid}`)}
          />
          <span className="muted" data-testid="topbar-user-info">
            {me.display_name || me.username}
            {(me.job_title || me.department) ? ` · ${[me.job_title, me.department].filter(Boolean).join(" / ")}` : ""}
            {` (${me.role})`}
          </span>
          <button type="button" className="btn small" onClick={handleLogout} data-testid="logout-btn">
            Đăng xuất
          </button>
        </div>
      </header>

      <div className="app-body-grid">
        {mobileNavOpen && (
          <div
            className="wf-sidebar-backdrop"
            onClick={() => setMobileNavOpen(false)}
          />
        )}
        <aside className={`wf-sidebar ${mobileNavOpen ? "open" : ""}`} data-testid="wf-sidebar">
          <div className="wf-sidebar-group">
            <div className="wf-sidebar-title">Quy trình</div>
            <button
              type="button"
              className={`wf-nav-link ${route.kind === "jobs" ? "active" : ""}`}
              onClick={() => goPath("/jobs")}
              data-testid="nav-wf-jobs"
            >
              Nhiệm vụ của tôi
            </button>
            <button
              type="button"
              className={`wf-nav-link ${route.kind === "todos" ? "active" : ""}`}
              onClick={() => goPath("/todos")}
              data-testid="nav-wf-todos"
            >
              Công việc của tôi
            </button>
            <button
              type="button"
              className={`wf-nav-link ${route.kind === "workflows" || route.kind === "workflow_new" ? "active" : ""}`}
              onClick={() => goPath("/workflows")}
              data-testid="nav-wf-workflows"
            >
              Quản lý quy trình
            </button>
          </div>

          {sidebarWorkflows.length > 0 && (
            <div className="wf-sidebar-group">
              <div className="wf-sidebar-title">Quy trình hoạt động</div>
              {sidebarWorkflows.map((wf) => {
                const active =
                  (route.kind === "workflow_board" ||
                    route.kind === "workflow_archive" ||
                    route.kind === "workflow_edit" ||
                    route.kind === "workflow_people") &&
                  route.workflowId === wf.id;
                return (
                  <button
                    key={wf.id}
                    type="button"
                    className={`wf-nav-link sub ${active ? "active" : ""}`}
                    onClick={() => goPath(`/workflows/${wf.id}`)}
                    data-testid={`nav-wf-board-${wf.slug}`}
                  >
                    {wf.name}
                  </button>
                );
              })}
            </div>
          )}
        </aside>

        <main className="main-wrap">
          {route.kind === "jobs" && (
            <JobsView onOpenTask={(tid) => goPath(`/tasks/${tid}`)} />
          )}
          {route.kind === "todos" && (
            <TodosView onOpenTask={(tid) => goPath(`/tasks/${tid}`)} />
          )}
          {route.kind === "workflows" && (
            <WorkflowsListView
              me={me}
              onOpenBoard={(wid) => goPath(`/workflows/${wid}`)}
              onCreateWorkflow={() => {
                setPendingTemplate(null);
                goPath("/workflows/new");
              }}
              onCreateFromTemplate={(tpl) => {
                setPendingTemplate(tpl);
                goPath("/workflows/new");
              }}
            />
          )}
          {route.kind === "workflow_new" && (
            <WorkflowBuilderView
              mode="create"
              me={me}
              initialTemplate={pendingTemplate}
              onBack={() => {
                setPendingTemplate(null);
                goPath("/workflows");
              }}
              onOpenBoard={(wid) => {
                setPendingTemplate(null);
                goPath(`/workflows/${wid}`);
              }}
              onOpenPeople={(wid) => goPath(`/workflows/${wid}/people`)}
              onWorkflowsChanged={loadSidebarWorkflows}
            />
          )}
          {route.kind === "workflow_edit" && (
            <WorkflowBuilderView
              mode="edit"
              me={me}
              workflowId={route.workflowId}
              onBack={() => goPath(`/workflows/${route.workflowId}`)}
              onOpenBoard={(wid) => goPath(`/workflows/${wid}`)}
              onOpenPeople={(wid) => goPath(`/workflows/${wid}/people`)}
              onWorkflowsChanged={loadSidebarWorkflows}
            />
          )}
          {route.kind === "workflow_people" && (
            <WorkflowPeopleView
              workflowId={route.workflowId}
              onBackBoard={(wid) => goPath(`/workflows/${wid}`)}
              onEditWorkflow={(wid) => goPath(`/workflows/${wid}/edit`)}
              onWorkflowsChanged={loadSidebarWorkflows}
            />
          )}
          {(route.kind === "workflow_board" || route.kind === "workflow_archive") && (
            <WorkflowBoardView
              workflowId={route.workflowId}
              archiveMode={route.kind === "workflow_archive"}
              onBackWorkflows={() => goPath("/workflows")}
              onOpenBoard={(wid) => goPath(`/workflows/${wid}`)}
              onOpenArchive={(wid) => goPath(`/workflows/${wid}/archive`)}
              onEditWorkflow={(wid) => goPath(`/workflows/${wid}/edit`)}
              onOpenPeople={(wid) => goPath(`/workflows/${wid}/people`)}
              onOpenTask={(tid) => goPath(`/tasks/${tid}`)}
              onNotifyRefresh={() => setNotifTick((n) => n + 1)}
              onWorkflowsChanged={loadSidebarWorkflows}
            />
          )}
          {route.kind === "task_detail" && (
            <TaskDetailView
              me={me}
              taskId={route.taskId}
              onOpenWorkflow={(wid) => goPath(`/workflows/${wid}`)}
              onGoAccount={() => switchLegacyTab("account")}
              onRefreshMe={refreshMe}
              onNotifyRefresh={() => setNotifTick((n) => n + 1)}
            />
          )}
          {route.kind === "legacy" && tab === "sign" && (
            <SignWorkspace
              me={me}
              initialFile={openSignFile}
              onGoVerify={(fid) => {
                setVerifyFileId(fid);
                setTab("verify");
              }}
              onGoAccount={() => setTab("account")}
              onRefreshMe={refreshMe}
            />
          )}
          {route.kind === "legacy" && tab === "workflow" && (
            <WorkflowView
              me={me}
              openDocId={openWorkflowDocId}
              resetSignal={workflowResetTick}
              onDocOpened={() => setOpenWorkflowDocId(null)}
              onNotifyRefresh={() => setNotifTick((n) => n + 1)}
              onGoAccount={() => setTab("account")}
              onRefreshMe={refreshMe}
            />
          )}
          {route.kind === "legacy" && tab === "docs" && (
            <DocumentsView
              onOpenSign={(f) => {
                setOpenSignFile(f);
                setTab("sign");
              }}
              onGoVerify={(fid) => {
                setVerifyFileId(fid);
                setTab("verify");
              }}
            />
          )}
          {route.kind === "legacy" && tab === "verify" && <VerifyView initialFileId={verifyFileId} />}
          {route.kind === "legacy" && tab === "account" && <AccountSettingsView me={me} onUpdated={refreshMe} />}
          {route.kind === "legacy" && tab === "admin" && me.role === "admin" && <AdminView currentUserId={me.id} />}
        </main>
      </div>
    </div>
  );
}
