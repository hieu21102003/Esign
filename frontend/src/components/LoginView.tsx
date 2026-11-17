import React, { useState } from "react";
import QRCode from "qrcode";
import { apiJson, setCsrfToken } from "../lib/api";

interface Props {
  initialStep?: "login" | "totp_setup" | "totp_verify";
  onAuthed: () => Promise<void>;
}

export function LoginView({ initialStep = "login", onAuthed }: Props) {
  const [step, setStep] = useState<"login" | "totp_setup" | "totp_verify" | "recovery_saved">(initialStep);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [useRecovery, setUseRecovery] = useState(false);
  const [secret, setSecret] = useState("");
  const [qrDataUrl, setQrDataUrl] = useState("");
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      const res = await apiJson<{ mfa_required: boolean; totp_enrolled: boolean; csrf_token: string }>(
        "/api/auth/login",
        { method: "POST", body: JSON.stringify({ username, password }) },
      );
      setCsrfToken(res.csrf_token);
      setPassword("");
      if (!res.totp_enrolled) {
        const setup = await apiJson<{ secret: string; otpauth_uri: string }>("/api/auth/totp/setup", {
          method: "POST",
        });
        setSecret(setup.secret);
        setQrDataUrl(await QRCode.toDataURL(setup.otpauth_uri, { width: 180, margin: 1 }));
        setStep("totp_setup");
      } else {
        setStep("totp_verify");
      }
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Đăng nhập thất bại");
    } finally {
      setBusy(false);
    }
  }

  async function handleConfirmSetup(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      const res = await apiJson<{ recovery_codes: string[]; csrf_token: string }>("/api/auth/totp/confirm", {
        method: "POST",
        body: JSON.stringify({ code }),
      });
      setCsrfToken(res.csrf_token);
      setRecoveryCodes(res.recovery_codes);
      setCode("");
      setStep("recovery_saved");
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Mã TOTP không đúng");
    } finally {
      setBusy(false);
    }
  }

  async function handleVerifyTotp(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      const payload = useRecovery ? { recovery_code: code } : { code };
      const res = await apiJson<{ ok: boolean; csrf_token: string }>("/api/auth/totp/verify", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      setCsrfToken(res.csrf_token);
      setCode("");
      await onAuthed();
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Mã xác thực không đúng");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-wrap">
      <div className="card auth-card">
        <div className="brand-title">esign</div>
        <p className="muted">Hệ thống ký số PDF nội bộ (PAdES)</p>
        {err && <div className="alert error" role="alert">{err}</div>}

        {step === "login" && (
          <form onSubmit={handleLogin} className="stack">
            <label className="field">
              <span>Tên đăng nhập</span>
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                required
              />
            </label>
            <label className="field">
              <span>Mật khẩu</span>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
            </label>
            <button type="submit" className="btn primary" disabled={busy}>
              {busy ? "Đang kiểm tra..." : "Đăng nhập"}
            </button>
          </form>
        )}

        {step === "totp_setup" && (
          <form onSubmit={handleConfirmSetup} className="stack">
            <h3>Thiết lập xác thực 2 bước (TOTP)</h3>
            <p className="muted">Quét mã QR bằng ứng dụng Authenticator hoặc nhập mã bí mật bên dưới:</p>
            {qrDataUrl && <img src={qrDataUrl} alt="QR TOTP" className="qr-img" />}
            <code className="secret-box" data-testid="totp-secret">{secret}</code>
            <label className="field">
              <span>Mã 6 chữ số từ ứng dụng</span>
              <input
                value={code}
                onChange={(e) => setCode(e.target.value)}
                inputMode="numeric"
                placeholder="123456"
                required
              />
            </label>
            <button type="submit" className="btn primary" disabled={busy}>
              Xác nhận kích hoạt 2FA
            </button>
          </form>
        )}

        {step === "recovery_saved" && (
          <div className="stack">
            <h3>Lưu mã khôi phục dùng một lần</h3>
            <p className="muted">Hãy lưu lại các mã này ở nơi an toàn trước khi tiếp tục:</p>
            <div className="recovery-grid">
              {recoveryCodes.map((c) => (
                <code key={c}>{c}</code>
              ))}
            </div>
            <button type="button" className="btn primary" onClick={() => onAuthed()}>
              Đã lưu, vào hệ thống
            </button>
          </div>
        )}

        {step === "totp_verify" && (
          <form onSubmit={handleVerifyTotp} className="stack">
            <h3>Xác thực 2FA</h3>
            <label className="field">
              <span>{useRecovery ? "Mã khôi phục (1 lần)" : "Mã TOTP 6 chữ số"}</span>
              <input
                value={code}
                onChange={(e) => setCode(e.target.value)}
                placeholder={useRecovery ? "xxxxx-xxxxx" : "123456"}
                required
              />
            </label>
            <button type="submit" className="btn primary" disabled={busy}>
              {busy ? "Đang xác thực..." : "Xác thực"}
            </button>
            <button
              type="button"
              className="btn ghost"
              onClick={() => {
                setUseRecovery(!useRecovery);
                setCode("");
                setErr("");
              }}
            >
              {useRecovery ? "Dùng mã TOTP 6 chữ số" : "Dùng mã khôi phục"}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
