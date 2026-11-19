import React, { useState } from "react";
import { apiJson, type MeResponse } from "../lib/api";

interface Props {
  me: MeResponse;
  onUpdated: () => Promise<void> | void;
}

export function AccountSettingsView({ me, onUpdated }: Props) {
  const [newPin, setNewPin] = useState("");
  const [confirmPin, setConfirmPin] = useState("");
  const [authMethod, setAuthMethod] = useState<"totp" | "recovery">("totp");
  const [mfaCode, setMfaCode] = useState("");
  const [pinBusy, setPinBusy] = useState(false);
  const [pinMsg, setPinMsg] = useState("");
  const [pinErr, setPinErr] = useState("");

  const [curPw, setCurPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [pwBusy, setPwBusy] = useState(false);
  const [pwMsg, setPwMsg] = useState("");
  const [pwErr, setPwErr] = useState("");

  async function handleSavePin(e: React.FormEvent) {
    e.preventDefault();
    setPinErr("");
    setPinMsg("");
    if (newPin.length !== 6) {
      setPinErr("Mã PIN ký phải gồm đúng 6 chữ số.");
      return;
    }
    if (newPin !== confirmPin) {
      setPinErr("Xác nhận mã PIN không khớp.");
      return;
    }
    const codeVal = mfaCode.trim();
    if (!codeVal) {
      setPinErr("Bắt buộc phải nhập mã TOTP hoặc mã khôi phục.");
      return;
    }

    const body: Record<string, string> = { new_pin: newPin };
    if (authMethod === "totp") {
      body.totp_code = codeVal;
    } else {
      body.recovery_code = codeVal;
    }

    setNewPin("");
    setConfirmPin("");
    setMfaCode("");
    setPinBusy(true);
    try {
      await apiJson("/api/auth/signing-pin", {
        method: "POST",
        body: JSON.stringify(body),
      });
      await onUpdated();
      setPinMsg(me.has_signing_pin ? "Đã đổi mã PIN ký thành công." : "Đã thiết lập mã PIN ký thành công.");
    } catch (ex) {
      setPinErr(ex instanceof Error ? ex.message : "Không lưu được mã PIN ký");
    } finally {
      setPinBusy(false);
    }
  }

  async function handleChangePassword(e: React.FormEvent) {
    e.preventDefault();
    setPwErr("");
    setPwMsg("");
    if (newPw !== confirmPw) {
      setPwErr("Xác nhận mật khẩu mới không khớp.");
      return;
    }
    const c = curPw;
    const n = newPw;
    setCurPw("");
    setNewPw("");
    setConfirmPw("");
    setPwBusy(true);
    try {
      await apiJson("/api/auth/password", {
        method: "POST",
        body: JSON.stringify({ current_password: c, new_password: n }),
      });
      setPwMsg("Đã đổi mật khẩu đăng nhập.");
    } catch (ex) {
      setPwErr(ex instanceof Error ? ex.message : "Đổi mật khẩu thất bại");
    } finally {
      setPwBusy(false);
    }
  }

  return (
    <div className="two-col" style={{ maxWidth: 960, margin: "0 auto", gap: 16, alignItems: "start" }}>
      <div className="card stack">
        <div className="preview-toolbar">
          <h3>Mã PIN ký số (6 chữ số)</h3>
          {me.pin_locked ? (
            <span className="badge bad" data-testid="pin-status-badge">Đang tạm khóa ký</span>
          ) : me.has_signing_pin ? (
            <span className="badge ok" data-testid="pin-status-badge">Đã thiết lập</span>
          ) : (
            <span className="badge warn" data-testid="pin-status-badge">Chưa đặt PIN</span>
          )}
        </div>
        <p className="muted" style={{ margin: 0 }}>
          PIN ký là lớp xác thực riêng cho mọi thao tác ký, từ chối và hủy luồng. Khi đặt hoặc đổi PIN bắt buộc nhập kèm mã 2FA hiện tại.
        </p>
        {me.pin_set_at && (
          <div className="muted">Cập nhật PIN gần nhất: {me.pin_set_at.slice(0, 19).replace("T", " ")} UTC</div>
        )}
        {me.pin_locked && (
          <div className="alert error" data-testid="pin-locked-banner">
            Chức năng ký đang tạm khóa do nhập sai PIN quá số lần cho phép
            {me.pin_locked_until ? ` (tới ${me.pin_locked_until.slice(0, 19).replace("T", " ")} UTC)` : ""}. Liên hệ quản trị viên nếu cần mở khóa sớm.
          </div>
        )}
        {pinErr && <div className="alert error" role="alert" data-testid="pin-settings-error">{pinErr}</div>}
        {pinMsg && <div className="alert ok" role="status" data-testid="pin-settings-ok">{pinMsg}</div>}

        <form className="stack" onSubmit={handleSavePin} data-testid="pin-settings-form">
          <label className="field">
            <span>{me.has_signing_pin ? "Mã PIN ký mới (6 chữ số)" : "Mã PIN ký (6 chữ số)"}</span>
            <input
              type="password"
              inputMode="numeric"
              pattern="[0-9]*"
              maxLength={6}
              autoComplete="off"
              value={newPin}
              onChange={(e) => setNewPin(e.target.value.replace(/\D/g, "").slice(0, 6))}
              placeholder="Tránh 123456, 000000, dãy tăng giảm..."
              required
              disabled={pinBusy}
              data-testid="account-pin-new"
            />
          </label>
          <label className="field">
            <span>Nhập lại mã PIN ký</span>
            <input
              type="password"
              inputMode="numeric"
              pattern="[0-9]*"
              maxLength={6}
              autoComplete="off"
              value={confirmPin}
              onChange={(e) => setConfirmPin(e.target.value.replace(/\D/g, "").slice(0, 6))}
              placeholder="••••••"
              required
              disabled={pinBusy}
              data-testid="account-pin-confirm"
            />
          </label>
          <div className="field">
            <span>Phương thức xác thực 2FA</span>
            <select
              value={authMethod}
              onChange={(e) => {
                setAuthMethod(e.target.value as "totp" | "recovery");
                setMfaCode("");
              }}
              data-testid="account-pin-method"
            >
              <option value="totp">Mã TOTP (6 chữ số từ ứng dụng)</option>
              <option value="recovery">Mã khôi phục dự phòng (dùng 1 lần)</option>
            </select>
          </div>
          <label className="field">
            <span>{authMethod === "totp" ? "Mã TOTP hiện tại" : "Mã khôi phục"}</span>
            <input
              type="text"
              autoComplete="off"
              value={mfaCode}
              onChange={(e) => setMfaCode(e.target.value)}
              placeholder={authMethod === "totp" ? "Nhập 6 chữ số TOTP" : "VD: a1b2c-3d4e5"}
              required
              disabled={pinBusy}
              data-testid="account-pin-mfa"
            />
          </label>
          <button type="submit" className="btn primary" disabled={pinBusy} data-testid="account-pin-submit">
            {pinBusy ? "Đang lưu..." : me.has_signing_pin ? "Đổi mã PIN ký" : "Đặt mã PIN ký"}
          </button>
        </form>
      </div>

      <div className="card stack">
        <h3>Đổi mật khẩu đăng nhập</h3>
        {pwErr && <div className="alert error" role="alert">{pwErr}</div>}
        {pwMsg && <div className="alert ok" role="status">{pwMsg}</div>}
        <form className="stack" onSubmit={handleChangePassword}>
          <label className="field">
            <span>Mật khẩu hiện tại</span>
            <input
              type="password"
              autoComplete="current-password"
              value={curPw}
              onChange={(e) => setCurPw(e.target.value)}
              required
              disabled={pwBusy}
            />
          </label>
          <label className="field">
            <span>Mật khẩu mới (&gt;= 10 ký tự, hoa/thường/số)</span>
            <input
              type="password"
              autoComplete="new-password"
              value={newPw}
              onChange={(e) => setNewPw(e.target.value)}
              required
              disabled={pwBusy}
            />
          </label>
          <label className="field">
            <span>Nhập lại mật khẩu mới</span>
            <input
              type="password"
              autoComplete="new-password"
              value={confirmPw}
              onChange={(e) => setConfirmPw(e.target.value)}
              required
              disabled={pwBusy}
            />
          </label>
          <button type="submit" className="btn" disabled={pwBusy}>
            {pwBusy ? "Đang cập nhật..." : "Cập nhật mật khẩu"}
          </button>
        </form>
      </div>
    </div>
  );
}
