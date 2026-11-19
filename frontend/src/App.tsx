import React, { useEffect, useState } from "react";
import { AccountSettingsView } from "./components/AccountSettingsView";
import { AdminView } from "./components/AdminView";
import { DocumentsView } from "./components/DocumentsView";
import { LoginView } from "./components/LoginView";
import { NotificationBell } from "./components/NotificationBell";
import { OnboardingWizard } from "./components/OnboardingWizard";
import { SignWorkspace } from "./components/SignWorkspace";
import { VerifyView } from "./components/VerifyView";
import { WorkflowView } from "./components/WorkflowView";
import { apiJson, setCsrfToken, type MeResponse, type StoredFile } from "./lib/api";

const DOC_PATH_RE = /^\/documents\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\/?$/i;

function clearDocumentPath() {
  if (typeof window !== "undefined" && window.location.pathname !== "/") {
    window.history.replaceState(null, "", "/");
  }
}

export function App() {
  const [me, setMe] = useState<MeResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<"sign" | "workflow" | "docs" | "verify" | "account" | "admin">("sign");
  const [verifyFileId, setVerifyFileId] = useState<string | null>(null);
  const [openSignFile, setOpenSignFile] = useState<StoredFile | null>(null);
  const [openWorkflowDocId, setOpenWorkflowDocId] = useState<string | null>(null);
  const [pendingUrlDocId, setPendingUrlDocId] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    const m = window.location.pathname.match(DOC_PATH_RE);
    return m ? m[1] : null;
  });
  const [workflowResetTick, setWorkflowResetTick] = useState(0);
  const [notifTick, setNotifTick] = useState(0);

  async function refreshMe() {
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
  }

  useEffect(() => {
    refreshMe();
  }, []);

  useEffect(() => {
    if (me && me.mfa_verified && !me.onboarding_required && pendingUrlDocId) {
      setOpenWorkflowDocId(pendingUrlDocId);
      setTab("workflow");
      setPendingUrlDocId(null);
    }
  }, [me, pendingUrlDocId]);

  function switchTab(next: "sign" | "workflow" | "docs" | "verify" | "account" | "admin") {
    clearDocumentPath();
    setTab(next);
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

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="row">
          <strong className="brand-title">esign</strong>
          <nav className="nav-tabs">
            <button
              type="button"
              className={`btn small ${tab === "sign" ? "primary" : "ghost"}`}
              onClick={() => switchTab("sign")}
              data-testid="tab-sign"
            >
              Ký số PDF
            </button>
            <button
              type="button"
              className={`btn small ${tab === "workflow" ? "primary" : "ghost"}`}
              onClick={() => {
                clearDocumentPath();
                setOpenWorkflowDocId(null);
                setWorkflowResetTick((n) => n + 1);
                setTab("workflow");
              }}
              data-testid="tab-workflow"
            >
              Luồng ký
            </button>
            <button
              type="button"
              className={`btn small ${tab === "docs" ? "primary" : "ghost"}`}
              onClick={() => switchTab("docs")}
              data-testid="tab-docs"
            >
              Tài liệu
            </button>
            <button
              type="button"
              className={`btn small ${tab === "verify" ? "primary" : "ghost"}`}
              onClick={() => {
                clearDocumentPath();
                setVerifyFileId(null);
                setTab("verify");
              }}
              data-testid="tab-verify"
            >
              Xác minh
            </button>
            <button
              type="button"
              className={`btn small ${tab === "account" ? "primary" : "ghost"}`}
              onClick={() => switchTab("account")}
              data-testid="tab-account"
            >
              Tài khoản{!me.has_signing_pin ? " (!)" : ""}
            </button>
            {me.role === "admin" && (
              <button
                type="button"
                className={`btn small ${tab === "admin" ? "primary" : "ghost"}`}
                onClick={() => switchTab("admin")}
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
              clearDocumentPath();
              setOpenWorkflowDocId(docId);
              setTab("workflow");
            }}
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

      <main className="main-wrap">
        {tab === "sign" && (
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
        {tab === "workflow" && (
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
        {tab === "docs" && (
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
        {tab === "verify" && <VerifyView initialFileId={verifyFileId} />}
        {tab === "account" && <AccountSettingsView me={me} onUpdated={refreshMe} />}
        {tab === "admin" && me.role === "admin" && <AdminView currentUserId={me.id} />}
      </main>
    </div>
  );
}
