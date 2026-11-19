import React, { useEffect, useRef, useState } from "react";

interface Props {
  open: boolean;
  title?: string;
  description?: string;
  busy?: boolean;
  error?: string;
  pinErrorKind?: string;
  hasSigningPin?: boolean;
  onClose: () => void;
  onConfirm: (pin: string) => void | Promise<void>;
  onGoSettings?: () => void;
}

export function PinModal({
  open,
  title = "Xác thực PIN ký số",
  description = "Nhập mã PIN ký 6 chữ số của bạn để xác nhận thao tác.",
  busy = false,
  error = "",
  pinErrorKind,
  hasSigningPin = true,
  onClose,
  onConfirm,
  onGoSettings,
}: Props) {
  const [pin, setPin] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) {
      setPin("");
      return;
    }
    setPin("");
    const timer = window.setTimeout(() => {
      inputRef.current?.focus();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [open]);

  useEffect(() => {
    if (open && error) {
      setPin("");
      inputRef.current?.focus();
    }
  }, [open, error]);

  useEffect(() => {
    if (!open) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape" && !busy) {
        onClose();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, busy, onClose]);

  if (!open) return null;

  const needSetup = !hasSigningPin || pinErrorKind === "pin_not_set";
  const displayError = needSetup
    ? error || "Bạn chưa đặt mã PIN ký 6 chữ số. Vui lòng đặt PIN trong phần Tài khoản trước khi ký."
    : error;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (busy || needSetup || pin.length !== 6) return;
    const submitted = pin;
    setPin("");
    onConfirm(submitted);
  }

  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      data-testid="pin-modal"
    >
      <form className="modal-card stack" onSubmit={handleSubmit}>
        <div className="preview-toolbar">
          <h3>{title}</h3>
          <button
            type="button"
            className="btn tiny"
            disabled={busy}
            onClick={onClose}
            data-testid="pin-modal-close"
          >
            Đóng
          </button>
        </div>
        <p className="muted" style={{ margin: 0 }}>{description}</p>

        {displayError && (
          <div className="alert error" role="alert" data-testid="pin-modal-error">
            {displayError}
          </div>
        )}

        {needSetup ? (
          <div className="row wrap" style={{ justifyContent: "flex-end" }}>
            <button type="button" className="btn small" onClick={onClose}>
              Hủy
            </button>
            {onGoSettings && (
              <button
                type="button"
                className="btn small primary"
                onClick={() => {
                  onClose();
                  onGoSettings();
                }}
                data-testid="pin-modal-go-settings"
              >
                Đi tới Cài đặt tài khoản
              </button>
            )}
          </div>
        ) : (
          <>
            <label className="field">
              <span>Mã PIN ký (6 chữ số)</span>
              <input
                ref={inputRef}
                type="password"
                inputMode="numeric"
                pattern="[0-9]*"
                maxLength={6}
                autoComplete="off"
                value={pin}
                onChange={(e) => setPin(e.target.value.replace(/\D/g, "").slice(0, 6))}
                placeholder="••••••"
                disabled={busy}
                data-testid="pin-modal-input"
              />
            </label>
            <div className="row" style={{ justifyContent: "flex-end" }}>
              <button
                type="button"
                className="btn small"
                disabled={busy}
                onClick={onClose}
                data-testid="pin-modal-cancel"
              >
                Hủy
              </button>
              <button
                type="submit"
                className="btn small primary"
                disabled={busy || pin.length !== 6}
                data-testid="pin-modal-confirm"
              >
                {busy ? "Đang xác thực..." : "Xác nhận"}
              </button>
            </div>
          </>
        )}
      </form>
    </div>
  );
}
