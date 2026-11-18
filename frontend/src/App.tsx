import React, { useEffect, useState } from "react";
import { AdminView } from "./components/AdminView";
import { DocumentsView } from "./components/DocumentsView";
import { LoginView } from "./components/LoginView";
import { SignWorkspace } from "./components/SignWorkspace";
import { VerifyView } from "./components/VerifyView";
import { apiJson, setCsrfToken, type MeResponse, type StoredFile } from "./lib/api";

export function App() {
  const [me, setMe] = useState<MeResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<"sign" | "docs" | "verify" | "admin">("sign");
  const [verifyFileId, setVerifyFileId] = useState<string | null>(null);
  const [openSignFile, setOpenSignFile] = useState<StoredFile | null>(null);

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

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="row">
          <strong className="brand-title">esign</strong>
          <nav className="nav-tabs">
            <button
              type="button"
              className={`btn small ${tab === "sign" ? "primary" : "ghost"}`}
              onClick={() => setTab("sign")}
              data-testid="tab-sign"
            >
              Ký số PDF
            </button>
            <button
              type="button"
              className={`btn small ${tab === "docs" ? "primary" : "ghost"}`}
              onClick={() => setTab("docs")}
              data-testid="tab-docs"
            >
              Tài liệu
            </button>
            <button
              type="button"
              className={`btn small ${tab === "verify" ? "primary" : "ghost"}`}
              onClick={() => {
                setVerifyFileId(null);
                setTab("verify");
              }}
              data-testid="tab-verify"
            >
              Xác minh
            </button>
            {me.role === "admin" && (
              <button
                type="button"
                className={`btn small ${tab === "admin" ? "primary" : "ghost"}`}
                onClick={() => setTab("admin")}
                data-testid="tab-admin"
              >
                Quản trị
              </button>
            )}
          </nav>
        </div>
        <div className="row">
          <span className="muted">{me.display_name || me.username} ({me.role})</span>
          <button type="button" className="btn small" onClick={handleLogout} data-testid="logout-btn">
            Đăng xuất
          </button>
        </div>
      </header>

      <main className="main-wrap">
        {tab === "sign" && (
          <SignWorkspace
            initialFile={openSignFile}
            onGoVerify={(fid) => {
              setVerifyFileId(fid);
              setTab("verify");
            }}
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
        {tab === "admin" && me.role === "admin" && <AdminView />}
      </main>
    </div>
  );
}
