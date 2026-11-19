import React, { useEffect, useRef, useState } from "react";
import {
  ApiError,
  apiJson,
  type ManagerSuggestion,
  type MeResponse,
  type OnboardingStatus,
  type OnboardingTerms,
} from "../lib/api";

interface Props {
  me: MeResponse;
  onCompleted: () => Promise<void> | void;
  onLogout: () => Promise<void> | void;
}

export function OnboardingWizard({ me, onCompleted, onLogout }: Props) {
  const [status, setStatus] = useState<OnboardingStatus | null>(null);
  const [step, setStep] = useState<number>(me.onboarding_step || 1);
  const [managers, setManagers] = useState<ManagerSuggestion[]>([]);
  const [terms, setTerms] = useState<OnboardingTerms | null>(null);

  // Bước 1: Thông tin cá nhân
  const [displayName, setDisplayName] = useState(me.display_name || "");
  const [jobTitle, setJobTitle] = useState(me.job_title || "");
  const [department, setDepartment] = useState(me.department || "");
  const [managerName, setManagerName] = useState(me.manager_name || "");
  const [managerUserId, setManagerUserId] = useState<number | null>(me.manager_user_id);
  const [showMgrList, setShowMgrList] = useState(false);
  const [email, setEmail] = useState(me.email || "");

  // Bước 2: Điều khoản
  const termsBoxRef = useRef<HTMLDivElement>(null);
  const [scrolledToBottom, setScrolledToBottom] = useState(false);
  const [agreed, setAgreed] = useState(false);
  const [termsOtp, setTermsOtp] = useState("");
  const [termsUseRecovery, setTermsUseRecovery] = useState(false);

  // Bước 3: Ảnh chữ ký
  const [sigFile, setSigFile] = useState<File | null>(null);
  const [removeBg, setRemoveBg] = useState(true);
  const [sensitivity, setSensitivity] = useState(55);
  const [rotate, setRotate] = useState<number>(0);
  const [previewUrl, setPreviewUrl] = useState<string>("");
  const [previewMeta, setPreviewMeta] = useState<{ width: number; height: number; size: number } | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);

  // Bước 4: PIN ký & kích hoạt
  const [newPin, setNewPin] = useState("");
  const [confirmPin, setConfirmPin] = useState("");
  const [completeOtp, setCompleteOtp] = useState("");
  const [completeUseRecovery, setCompleteUseRecovery] = useState(false);
  const [forceOtp, setForceOtp] = useState(false);

  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  async function loadInitial() {
    setLoading(true);
    setErr("");
    try {
      const [st, mgrs, trm] = await Promise.all([
        apiJson<OnboardingStatus>("/api/onboarding/status"),
        apiJson<ManagerSuggestion[]>("/api/onboarding/managers"),
        apiJson<OnboardingTerms>("/api/onboarding/terms"),
      ]);
      setStatus(st);
      setManagers(mgrs);
      setTerms(trm);
      setDisplayName(st.profile.display_name || "");
      setJobTitle(st.profile.job_title || "");
      setDepartment(st.profile.department || "");
      setManagerName(st.profile.manager_name || "");
      setManagerUserId(st.profile.manager_user_id);
      setEmail(st.profile.email || "");
      setStep(st.terms_reaccept_only ? 2 : st.current_step || 1);
      if (st.has_signature_image) {
        setPreviewUrl("/api/onboarding/signature-image");
      }
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Không tải được thông tin kích hoạt tài khoản");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadInitial();
  }, []);

  useEffect(() => {
    if (step !== 2 || !terms) return;
    setScrolledToBottom(false);
    setAgreed(false);
    const id = window.setTimeout(() => {
      const el = termsBoxRef.current;
      if (el && el.scrollHeight <= el.clientHeight + 4) {
        setScrolledToBottom(true);
      }
    }, 30);
    return () => window.clearTimeout(id);
  }, [step, terms?.version]);

  function handleTermsScroll() {
    const el = termsBoxRef.current;
    if (!el) return;
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 8) {
      setScrolledToBottom(true);
    }
  }

  async function requestPreview(file: File, rmBg: boolean, sens: number, rot: number) {
    setErr("");
    setPreviewBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("remove_bg", rmBg ? "true" : "false");
      fd.append("sensitivity", String(sens));
      fd.append("rotate", String(rot));
      const res = await apiJson<{ preview_data_url: string; width: number; height: number; size: number }>(
        "/api/onboarding/signature-image/preview",
        { method: "POST", body: fd },
      );
      setPreviewUrl(res.preview_data_url);
      setPreviewMeta({ width: res.width, height: res.height, size: res.size });
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Không xử lý được ảnh chữ ký");
    } finally {
      setPreviewBusy(false);
    }
  }

  async function handleSigFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    setSigFile(f);
    setRotate(0);
    await requestPreview(f, removeBg, sensitivity, 0);
  }

  async function handleSaveStep1(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    setMsg("");
    setBusy(true);
    try {
      const st = await apiJson<OnboardingStatus>("/api/onboarding/profile", {
        method: "POST",
        body: JSON.stringify({
          display_name: displayName.trim(),
          job_title: jobTitle.trim(),
          department: department.trim(),
          manager_name: managerName.trim(),
          manager_user_id: managerUserId,
          email: email.trim(),
        }),
      });
      setStatus(st);
      setStep(2);
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Không lưu được thông tin cá nhân");
    } finally {
      setBusy(false);
    }
  }

  async function handleSendEmailOtp(target: "terms" | "complete") {
    setErr("");
    setMsg("");
    setBusy(true);
    try {
      const res = await apiJson<{ ok: boolean; expires_in_minutes: number }>("/api/onboarding/terms/send-otp", {
        method: "POST",
      });
      setMsg(`Đã gửi mã OTP 6 chữ số tới email (${res.expires_in_minutes} phút hiệu lực).`);
      if (target === "terms") setTermsOtp("");
      else setCompleteOtp("");
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Gửi mã OTP email thất bại");
    } finally {
      setBusy(false);
    }
  }

  async function handleAcceptTerms(e: React.FormEvent) {
    e.preventDefault();
    if (!terms) return;
    setErr("");
    setMsg("");
    const code = termsOtp.trim();
    setTermsOtp("");
    setBusy(true);
    try {
      const body: Record<string, unknown> = {
        terms_version: terms.version,
        scrolled_to_bottom: scrolledToBottom,
        agreed,
      };
      if (termsUseRecovery) body.recovery_code = code;
      else body.otp_code = code;

      const st = await apiJson<OnboardingStatus>("/api/onboarding/terms/accept", {
        method: "POST",
        body: JSON.stringify(body),
      });
      setStatus(st);
      if (!st.onboarding_required) {
        await onCompleted();
        return;
      }
      setForceOtp(false);
      setStep(st.has_signature_image ? st.current_step : 3);
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Xác nhận điều khoản thất bại");
    } finally {
      setBusy(false);
    }
  }

  async function handleSaveSignatureImage() {
    if (!sigFile) return;
    setErr("");
    setMsg("");
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", sigFile);
      fd.append("remove_bg", removeBg ? "true" : "false");
      fd.append("sensitivity", String(sensitivity));
      fd.append("rotate", String(rotate));
      const res = await apiJson<{ file_id: string; status: OnboardingStatus }>(
        "/api/onboarding/signature-image/save",
        { method: "POST", body: fd },
      );
      setStatus(res.status);
      setSigFile(null);
      setStep(4);
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Lưu ảnh chữ ký thất bại");
    } finally {
      setBusy(false);
    }
  }

  async function handleComplete(e: React.FormEvent) {
    e.preventDefault();
    if (!status) return;
    setErr("");
    setMsg("");
    const needPin = !status.has_signing_pin;
    if (needPin) {
      if (newPin.length !== 6) {
        setErr("Mã PIN ký phải gồm đúng 6 chữ số.");
        return;
      }
      if (newPin !== confirmPin) {
        setErr("Xác nhận mã PIN không khớp.");
        return;
      }
    }
    const payload: Record<string, string> = {};
    if (needPin) {
      payload.new_pin = newPin;
    }
    const otpVal = completeOtp.trim();
    if (otpVal) {
      if (completeUseRecovery) payload.recovery_code = otpVal;
      else payload.otp_code = otpVal;
    }

    setNewPin("");
    setConfirmPin("");
    setCompleteOtp("");
    setBusy(true);
    try {
      await apiJson("/api/onboarding/complete", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      await onCompleted();
    } catch (ex) {
      if (ex instanceof ApiError && ex.onboardingOtpRequired) {
        setForceOtp(true);
      }
      setErr(ex instanceof Error ? ex.message : "Hoàn tất kích hoạt thất bại");
    } finally {
      setBusy(false);
    }
  }

  const filteredManagers = managers.filter((m) => {
    const q = managerName.trim().toLowerCase();
    if (!q) return true;
    return (
      m.display_name.toLowerCase().includes(q) ||
      m.username.toLowerCase().includes(q) ||
      m.department.toLowerCase().includes(q)
    );
  });

  const stepLabels = [
    [1, "1. Thông tin cá nhân"],
    [2, "2. Điều khoản cam kết"],
    [3, "3. Ảnh chữ ký"],
    [4, "4. Tạo PIN & Cấp cert"],
  ] as const;

  return (
    <div className="onboarding-wrap" data-testid="onboarding-wizard">
      <div className="onboarding-card stack">
        <div className="preview-toolbar">
          <div>
            <strong style={{ fontSize: 17 }}>Kích hoạt tài khoản chữ ký số nội bộ</strong>
            <div className="muted">
              Xin chào <strong>{me.username}</strong> — vui lòng hoàn tất thiết lập bắt buộc trước khi sử dụng hệ thống.
            </div>
          </div>
          <button
            type="button"
            className="btn small"
            onClick={onLogout}
            disabled={busy}
            data-testid="onboarding-logout-btn"
          >
            Đăng xuất
          </button>
        </div>

        {status?.terms_reaccept_only ? (
          <div className="alert ok">
            Điều khoản sử dụng chữ ký số đã được cập nhật lên phiên bản <strong>{status.terms_version}</strong>. Vui lòng đọc và xác nhận lại để tiếp tục.
          </div>
        ) : (
          <div className="stepper">
            {stepLabels.map(([n, label]) => (
              <div
                key={n}
                className={`step-pill ${step === n ? "active" : step > n ? "done" : ""}`}
                data-testid={`onboarding-stepper-${n}`}
              >
                {label}
              </div>
            ))}
          </div>
        )}

        {err && <div className="alert error" role="alert" data-testid="onboarding-error">{err}</div>}
        {msg && <div className="alert ok" role="status" data-testid="onboarding-msg">{msg}</div>}

        {loading || !status ? (
          <div className="muted">Đang tải thông tin kích hoạt...</div>
        ) : (
          <>
            {step === 1 && !status.terms_reaccept_only && (
              <form className="stack" onSubmit={handleSaveStep1} data-testid="onboarding-step1-form">
                <h3>Bước 1: Thông tin cá nhân</h3>
                <div className="two-col">
                  <label className="field">
                    <span>Họ và tên (hiển thị trên chữ ký &amp; chứng thư) *</span>
                    <input
                      value={displayName}
                      onChange={(e) => setDisplayName(e.target.value)}
                      placeholder="VD: Nguyễn Văn A"
                      required
                      disabled={busy}
                      data-testid="onboarding-display-name"
                    />
                  </label>
                  <label className="field">
                    <span>Email công ty *</span>
                    <input
                      type="email"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      placeholder="ten.ban@congty.vn"
                      required
                      disabled={busy}
                      data-testid="onboarding-email"
                    />
                  </label>
                  <label className="field">
                    <span>Chức danh *</span>
                    <input
                      value={jobTitle}
                      onChange={(e) => setJobTitle(e.target.value)}
                      placeholder="VD: Trưởng phòng Kỹ thuật"
                      required
                      disabled={busy}
                      data-testid="onboarding-job-title"
                    />
                  </label>
                  <label className="field">
                    <span>Phòng ban *</span>
                    <input
                      value={department}
                      onChange={(e) => setDepartment(e.target.value)}
                      placeholder="VD: Phòng Kỹ thuật"
                      required
                      disabled={busy}
                      data-testid="onboarding-department"
                    />
                  </label>
                </div>

                <div className="field">
                  <span>Quản lý trực tiếp * (chọn từ gợi ý hoặc nhập tay)</span>
                  <input
                    value={managerName}
                    onFocus={() => setShowMgrList(true)}
                    onChange={(e) => {
                      setManagerName(e.target.value);
                      setManagerUserId(null);
                      setShowMgrList(true);
                    }}
                    placeholder="Nhập tên hoặc chọn quản lý trực tiếp"
                    required
                    disabled={busy}
                    data-testid="onboarding-manager-input"
                  />
                  {showMgrList && filteredManagers.length > 0 && (
                    <div className="mgr-suggestions">
                      {filteredManagers.slice(0, 8).map((m) => (
                        <button
                          key={m.id}
                          type="button"
                          className="mgr-suggestion-item"
                          data-testid={`onboarding-manager-option-${m.username}`}
                          onClick={() => {
                            setManagerName(m.display_name || m.username);
                            setManagerUserId(m.id);
                            setShowMgrList(false);
                          }}
                        >
                          <strong>{m.display_name || m.username}</strong> (@{m.username})
                          {(m.job_title || m.department) && (
                            <span className="muted"> · {[m.job_title, m.department].filter(Boolean).join(" - ")}</span>
                          )}
                        </button>
                      ))}
                    </div>
                  )}
                </div>

                <div className="row" style={{ justifyContent: "flex-end" }}>
                  <button type="submit" className="btn primary" disabled={busy} data-testid="onboarding-step1-next">
                    {busy ? "Đang lưu..." : "Lưu & Tiếp tục"}
                  </button>
                </div>
              </form>
            )}

            {step === 2 && terms && (
              <form className="stack" onSubmit={handleAcceptTerms} data-testid="onboarding-step2-form">
                <div className="preview-toolbar">
                  <h3>Bước 2: {terms.title}</h3>
                  <span className="badge muted" data-testid="onboarding-terms-version">Phiên bản: {terms.version}</span>
                </div>

                <div
                  ref={termsBoxRef}
                  onScroll={handleTermsScroll}
                  className="onboarding-terms-box"
                  data-testid="onboarding-terms-box"
                >
                  {terms.content}
                </div>

                {!scrolledToBottom && (
                  <div className="muted" data-testid="onboarding-scroll-hint">
                    Vui lòng cuộn xuống cuối văn bản điều khoản để bật nút xác nhận.
                  </div>
                )}

                <label className="check-item">
                  <input
                    type="checkbox"
                    checked={agreed}
                    disabled={!scrolledToBottom || busy}
                    onChange={(e) => setAgreed(e.target.checked)}
                    data-testid="onboarding-terms-agree"
                  />
                  <span>Tôi đã đọc kỹ, hiểu rõ và đồng ý tuân thủ toàn bộ điều khoản sử dụng chữ ký số nội bộ.</span>
                </label>

                {status.otp_method === "totp" && (
                  <div className="stack">
                    <div className="row wrap">
                      <label className="field" style={{ flex: 1 }}>
                        <span>{termsUseRecovery ? "Mã khôi phục dự phòng" : "Mã TOTP hiện tại (6 chữ số)"} *</span>
                        <input
                          type="text"
                          autoComplete="off"
                          value={termsOtp}
                          onChange={(e) => setTermsOtp(e.target.value)}
                          placeholder={termsUseRecovery ? "VD: a1b2c-3d4e5" : "Nhập 6 chữ số từ ứng dụng Authenticator"}
                          required
                          disabled={busy}
                          data-testid="onboarding-terms-otp"
                        />
                      </label>
                      <button
                        type="button"
                        className="btn small"
                        style={{ marginTop: 18 }}
                        onClick={() => {
                          setTermsUseRecovery((v) => !v);
                          setTermsOtp("");
                        }}
                      >
                        {termsUseRecovery ? "Dùng mã TOTP" : "Dùng mã khôi phục"}
                      </button>
                    </div>
                  </div>
                )}

                {status.otp_method === "email_otp" && (
                  <div className="row wrap">
                    <label className="field" style={{ flex: 1 }}>
                      <span>Mã OTP gửi tới email ({email}) *</span>
                      <input
                        type="text"
                        autoComplete="off"
                        value={termsOtp}
                        onChange={(e) => setTermsOtp(e.target.value)}
                        placeholder="Nhập 6 chữ số OTP email"
                        required
                        disabled={busy}
                        data-testid="onboarding-terms-otp"
                      />
                    </label>
                    <button
                      type="button"
                      className="btn small"
                      style={{ marginTop: 18 }}
                      disabled={busy}
                      onClick={() => handleSendEmailOtp("terms")}
                      data-testid="onboarding-send-email-otp"
                    >
                      Gửi mã OTP
                    </button>
                  </div>
                )}

                {status.otp_method === "none" && (
                  <div className="alert error">
                    Tài khoản chưa bật 2FA TOTP và hệ thống chưa cấu hình SMTP để gửi OTP email. Vui lòng liên hệ quản trị viên.
                  </div>
                )}

                <div className="row" style={{ justifyContent: "space-between" }}>
                  {!status.terms_reaccept_only ? (
                    <button type="button" className="btn small" disabled={busy} onClick={() => setStep(1)}>
                      ← Quay lại
                    </button>
                  ) : (
                    <span />
                  )}
                  <button
                    type="submit"
                    className="btn primary"
                    disabled={busy || !scrolledToBottom || !agreed || status.otp_method === "none"}
                    data-testid="onboarding-terms-confirm"
                  >
                    {busy ? "Đang xác nhận..." : "Xác nhận điều khoản"}
                  </button>
                </div>
              </form>
            )}

            {step === 3 && !status.terms_reaccept_only && (
              <div className="stack" data-testid="onboarding-step3">
                <h3>Bước 3: Upload và chuẩn hóa ảnh chữ ký</h3>
                <p className="muted" style={{ margin: 0 }}>
                  Hệ thống tự động xóa ảnh nền thành trong suốt, xóa EXIF và cắt gọn sát nét chữ ngay trên server (không gửi ra bên ngoài).
                </p>

                <div className="row wrap">
                  <label className="btn primary small file-btn">
                    <span>{previewBusy ? "Đang xử lý..." : sigFile ? "Chọn ảnh khác (PNG/JPG)" : "Chọn ảnh chữ ký (PNG/JPG)"}</span>
                    <input
                      type="file"
                      accept="image/png,image/jpeg"
                      onChange={handleSigFileChange}
                      disabled={busy || previewBusy}
                      data-testid="onboarding-sig-file"
                    />
                  </label>

                  {sigFile && (
                    <>
                      <label className="check-item">
                        <input
                          type="checkbox"
                          checked={removeBg}
                          disabled={previewBusy}
                          onChange={(e) => {
                            const v = e.target.checked;
                            setRemoveBg(v);
                            requestPreview(sigFile, v, sensitivity, rotate);
                          }}
                          data-testid="onboarding-sig-remove-bg"
                        />
                        <span>Xóa nền thành trong suốt</span>
                      </label>
                      <button
                        type="button"
                        className="btn small"
                        disabled={previewBusy}
                        onClick={() => {
                          const nr = (rotate + 270) % 360;
                          setRotate(nr);
                          requestPreview(sigFile, removeBg, sensitivity, nr);
                        }}
                        data-testid="onboarding-sig-rotate-left"
                      >
                        ↺ Xoay trái 90°
                      </button>
                      <button
                        type="button"
                        className="btn small"
                        disabled={previewBusy}
                        onClick={() => {
                          const nr = (rotate + 90) % 360;
                          setRotate(nr);
                          requestPreview(sigFile, removeBg, sensitivity, nr);
                        }}
                        data-testid="onboarding-sig-rotate-right"
                      >
                        ↻ Xoay phải 90°
                      </button>
                    </>
                  )}
                </div>

                {sigFile && removeBg && (
                  <label className="field">
                    <span>Độ nhạy xóa nền: {sensitivity}%</span>
                    <input
                      type="range"
                      min={0}
                      max={100}
                      value={sensitivity}
                      onChange={(e) => {
                        const v = Number(e.target.value);
                        setSensitivity(v);
                        requestPreview(sigFile, removeBg, v, rotate);
                      }}
                      data-testid="onboarding-sig-sensitivity"
                    />
                  </label>
                )}

                {previewUrl && (
                  <div className="sig-preview-grid">
                    <div className="stack">
                      <span className="muted">
                        Xem trước trên nền trắng
                        {previewMeta ? ` (${previewMeta.width}×${previewMeta.height}px, ${(previewMeta.size / 1024).toFixed(1)} KB)` : ""}
                      </span>
                      <div className="sig-preview-white">
                        <img src={previewUrl} alt="Xem trước chữ ký" data-testid="onboarding-sig-preview-img" />
                      </div>
                    </div>
                    <div className="stack">
                      <span className="muted">Xem trước trên mẫu ô ký PDF</span>
                      <div className="sig-preview-sample" data-testid="onboarding-sig-sample-box">
                        <img src={previewUrl} alt="Chữ ký mẫu" />
                        <div>
                          <div><strong>Ký bởi: {displayName}</strong></div>
                          <div>Chức danh: {jobTitle}</div>
                          <div>Phòng ban: {department}</div>
                          <div className="muted">Ngày ký: 2026-11-19</div>
                          <div className="muted">Lý do: Đã duyệt nội dung</div>
                        </div>
                      </div>
                    </div>
                  </div>
                )}

                <div className="row" style={{ justifyContent: "space-between" }}>
                  <button type="button" className="btn small" disabled={busy} onClick={() => setStep(2)}>
                    ← Quay lại
                  </button>
                  <div className="row">
                    {status.has_signature_image && !sigFile && (
                      <button
                        type="button"
                        className="btn primary"
                        disabled={busy}
                        onClick={() => setStep(4)}
                        data-testid="onboarding-sig-keep-existing"
                      >
                        Tiếp tục với ảnh đã lưu →
                      </button>
                    )}
                    {sigFile && (
                      <button
                        type="button"
                        className="btn primary"
                        disabled={busy || previewBusy || !previewUrl}
                        onClick={handleSaveSignatureImage}
                        data-testid="onboarding-sig-save"
                      >
                        {busy ? "Đang lưu..." : "Dùng ảnh này"}
                      </button>
                    )}
                  </div>
                </div>
              </div>
            )}

            {step === 4 && !status.terms_reaccept_only && (
              <form className="stack" onSubmit={handleComplete} data-testid="onboarding-step4-form">
                <h3>Bước 4: Tạo mã PIN ký &amp; Cấp chứng thư số</h3>

                {status.has_signing_pin ? (
                  <div className="alert ok" data-testid="onboarding-pin-existing">
                    Tài khoản của bạn đã có mã PIN ký 6 chữ số (giữ nguyên PIN hiện tại).
                  </div>
                ) : (
                  <div className="stack">
                    <p className="muted" style={{ margin: 0 }}>
                      Mã PIN 6 chữ số được hỏi mỗi khi bạn ký số, từ chối hoặc hủy luồng. Tránh 123456, 000000, dãy tăng/giảm, mẫu lặp hoặc trùng mật khẩu đăng nhập.
                    </p>
                    <div className="two-col">
                      <label className="field">
                        <span>Mã PIN ký mới (6 chữ số) *</span>
                        <input
                          type="password"
                          inputMode="numeric"
                          pattern="[0-9]*"
                          maxLength={6}
                          autoComplete="off"
                          value={newPin}
                          onChange={(e) => setNewPin(e.target.value.replace(/\D/g, "").slice(0, 6))}
                          placeholder="••••••"
                          required
                          disabled={busy}
                          data-testid="onboarding-pin-new"
                        />
                      </label>
                      <label className="field">
                        <span>Nhập lại mã PIN ký *</span>
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
                          disabled={busy}
                          data-testid="onboarding-pin-confirm"
                        />
                      </label>
                    </div>

                    {status.otp_reuse_valid && !forceOtp ? (
                      <div className="alert ok" data-testid="onboarding-otp-reused-notice">
                        Dùng lại xác thực OTP từ Bước 2 (còn hiệu lực trong {status.otp_reuse_minutes} phút) — không cần nhập lại OTP.
                      </div>
                    ) : (
                      <div className="row wrap">
                        <label className="field" style={{ flex: 1 }}>
                          <span>
                            {status.otp_method === "email_otp"
                              ? "Phiên xác thực OTP đã hết hạn — nhập mã OTP email mới *"
                              : completeUseRecovery
                                ? "Mã khôi phục dự phòng *"
                                : "Phiên xác thực OTP đã hết hạn — nhập lại mã TOTP hiện tại *"}
                          </span>
                          <input
                            type="text"
                            autoComplete="off"
                            value={completeOtp}
                            onChange={(e) => setCompleteOtp(e.target.value)}
                            placeholder={status.otp_method === "email_otp" ? "Nhập 6 chữ số OTP email" : "Nhập mã 6 chữ số"}
                            required
                            disabled={busy}
                            data-testid="onboarding-complete-otp"
                          />
                        </label>
                        {status.otp_method === "totp" ? (
                          <button
                            type="button"
                            className="btn small"
                            style={{ marginTop: 18 }}
                            onClick={() => {
                              setCompleteUseRecovery((v) => !v);
                              setCompleteOtp("");
                            }}
                          >
                            {completeUseRecovery ? "Dùng mã TOTP" : "Dùng mã khôi phục"}
                          </button>
                        ) : (
                          status.otp_method === "email_otp" && (
                            <button
                              type="button"
                              className="btn small"
                              style={{ marginTop: 18 }}
                              disabled={busy}
                              onClick={() => handleSendEmailOtp("complete")}
                            >
                              Gửi mã OTP
                            </button>
                          )
                        )}
                      </div>
                    )}
                  </div>
                )}

                <div className="card" style={{ background: "#f8fafc" }}>
                  <strong>Chứng thư số ký nội bộ:</strong>{" "}
                  {status.has_active_cert && status.active_cert ? (
                    <span data-testid="onboarding-cert-existing">
                      Giữ nguyên chứng thư đã cấp (<code>{status.active_cert.key_ref}</code>, serial{" "}
                      <code>{status.active_cert.serial}</code>).
                    </span>
                  ) : (
                    <span>Hệ thống sẽ tự động cấp mới chứng thư số từ CA nội bộ (hiệu lực 365 ngày) cho bạn khi bấm Hoàn tất.</span>
                  )}
                </div>

                <div className="row" style={{ justifyContent: "space-between" }}>
                  <button type="button" className="btn small" disabled={busy} onClick={() => setStep(3)}>
                    ← Quay lại
                  </button>
                  <button
                    type="submit"
                    className="btn primary"
                    disabled={busy}
                    data-testid="onboarding-complete-btn"
                  >
                    {busy ? "Đang kích hoạt & cấp chứng thư..." : "Hoàn tất kích hoạt"}
                  </button>
                </div>
              </form>
            )}
          </>
        )}
      </div>
    </div>
  );
}
