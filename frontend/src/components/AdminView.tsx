import React, { useEffect, useState } from "react";
import { apiJson } from "../lib/api";

interface AdminUser {
  id: number;
  username: string;
  display_name: string;
  email: string | null;
  role: "admin" | "user";
  is_active: boolean;
  totp_enabled: boolean;
  locked: boolean;
  cert_refs?: string[];
}

interface AdminCert {
  id: number;
  key_ref: string;
  subject: string;
  serial: string;
  not_after: string;
  active: boolean;
}

interface AvailCert {
  key_ref: string;
  subject?: string;
  registered: boolean;
  error?: string;
}

interface AuditItem {
  id: number;
  ts: string;
  username: string;
  action: string;
  status: string;
  ip: string;
  cert_key_ref: string | null;
  detail: string | null;
}

interface ExpiryItem {
  kind: string;
  name: string;
  expires_at: string;
  days_left: number;
  status: string;
}

export function AdminView() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [certs, setCerts] = useState<AdminCert[]>([]);
  const [avail, setAvail] = useState<AvailCert[]>([]);
  const [audit, setAudit] = useState<AuditItem[]>([]);
  const [warnings, setWarnings] = useState<ExpiryItem[]>([]);
  const [caEnabled, setCaEnabled] = useState(false);
  const [caHealthy, setCaHealthy] = useState(false);
  const [caStatusInited, setCaStatusInited] = useState(false);
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [issueCert, setIssueCert] = useState(false);
  const [certDays, setCertDays] = useState(365);
  const [busy, setBusy] = useState(false);
  const [editEmailUid, setEditEmailUid] = useState<number | null>(null);
  const [editEmailVal, setEditEmailVal] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  async function loadAll() {
    const [u, c, av, au, ew, ca] = await Promise.all([
      apiJson<AdminUser[]>("/api/admin/users"),
      apiJson<AdminCert[]>("/api/admin/certificates"),
      apiJson<AvailCert[]>("/api/admin/certificates/available"),
      apiJson<AuditItem[]>("/api/admin/audit?limit=50"),
      apiJson<{ warnings: ExpiryItem[] }>("/api/admin/expiry-warnings?days=30").catch(() => ({ warnings: [] })),
      apiJson<{ enabled: boolean; healthy: boolean }>("/api/admin/ca/status").catch(() => ({
        enabled: false,
        healthy: false,
      })),
    ]);
    setUsers(u);
    setCerts(c);
    setAvail(av);
    setAudit(au);
    setWarnings(ew.warnings || []);
    setCaEnabled(Boolean(ca.enabled));
    setCaHealthy(Boolean(ca.healthy));
    if (!caStatusInited) {
      setIssueCert(Boolean(ca.enabled && ca.healthy));
      setCaStatusInited(true);
    }
  }

  useEffect(() => {
    loadAll().catch((ex) => setErr(ex instanceof Error ? ex.message : "Không tải được trang quản trị"));
  }, []);

  async function handleCreateUser(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    setMsg("");
    setBusy(true);
    try {
      const res = await apiJson<AdminUser & { certificate?: AdminCert }>("/api/admin/users", {
        method: "POST",
        body: JSON.stringify({
          username,
          display_name: displayName,
          email: email.trim() || null,
          password,
          role: "user",
          issue_cert: issueCert,
          cert_days: certDays,
        }),
      });
      setUsername("");
      setDisplayName("");
      setEmail("");
      setPassword("");
      setMsg(
        res.certificate
          ? `Đã tạo user "${res.username}" và tự động cấp chứng thư "${res.certificate.key_ref}"`
          : "Đã tạo người dùng mới",
      );
      await loadAll();
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Lỗi tạo người dùng");
    } finally {
      setBusy(false);
    }
  }

  async function issueUserCert(u: AdminUser) {
    setErr("");
    setMsg("");
    setBusy(true);
    try {
      const res = await apiJson<{ certificate: AdminCert }>(`/api/admin/users/${u.id}/issue-certificate`, {
        method: "POST",
        body: JSON.stringify({ days: certDays }),
      });
      setMsg(`Đã cấp và gán chứng thư "${res.certificate.key_ref}" cho ${u.username}`);
      await loadAll();
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Cấp chứng thư thất bại");
    } finally {
      setBusy(false);
    }
  }

  async function revokeCert(c: AdminCert) {
    if (!window.confirm(`Thu hồi vĩnh viễn chứng thư "${c.key_ref}" (${c.subject})?`)) return;
    setErr("");
    setMsg("");
    setBusy(true);
    try {
      await apiJson(`/api/admin/certificates/${c.id}/revoke`, { method: "POST" });
      setMsg(`Đã thu hồi chứng thư "${c.key_ref}" và cập nhật CRL`);
      await loadAll();
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Thu hồi chứng thư thất bại");
    } finally {
      setBusy(false);
    }
  }

  async function saveUserEmail(uid: number) {
    setErr("");
    setMsg("");
    try {
      await apiJson(`/api/admin/users/${uid}/email`, {
        method: "PUT",
        body: JSON.stringify({ email: editEmailVal.trim() || null }),
      });
      setEditEmailUid(null);
      setMsg("Đã cập nhật email");
      await loadAll();
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Lỗi cập nhật email");
    }
  }

  async function registerCert(keyRef: string) {
    setErr("");
    await apiJson("/api/admin/certificates", { method: "POST", body: JSON.stringify({ key_ref: keyRef }) });
    await loadAll();
  }

  async function assignCert(uid: number, cid: number) {
    setErr("");
    await apiJson(`/api/admin/users/${uid}/certificates`, { method: "PUT", body: JSON.stringify({ cert_ids: [cid] }) });
    setMsg("Đã gán chứng thư");
    await loadAll();
  }

  return (
    <div className="stack">
      {err && <div className="alert error">{err}</div>}
      {msg && <div className="alert ok">{msg}</div>}

      {warnings.length > 0 && (
        <div className="card stack" data-testid="expiry-warnings">
          <h3>Cảnh báo chứng thư & CRL sắp hết hạn (&lt;= 30 ngày)</h3>
          {warnings.map((w, idx) => (
            <div key={idx} className="alert error">
              [{w.kind}] {w.name}: còn {w.days_left} ngày (hết hạn: {w.expires_at.slice(0, 19)})
            </div>
          ))}
        </div>
      )}

      <div className="card stack">
        <div className="preview-toolbar">
          <h3>Tạo người dùng</h3>
          <span className={`badge ${caEnabled ? (caHealthy ? "ok" : "bad") : "muted"}`} data-testid="admin-ca-status">
            {caEnabled ? (caHealthy ? "CA tự động: Sẵn sàng" : "CA tự động: Không kết nối") : "CA tự động: Chưa cấu hình"}
          </span>
        </div>
        <form onSubmit={handleCreateUser} className="row wrap">
          <input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="username" required style={{ maxWidth: 160 }} />
          <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="Tên hiển thị" style={{ maxWidth: 180 }} />
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Email (tùy chọn)" style={{ maxWidth: 210 }} />
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Mật khẩu (>=10 ký tự)" required style={{ maxWidth: 200 }} />
          {caEnabled && (
            <>
              <label className="check-item" style={{ fontWeight: 600 }}>
                <input
                  type="checkbox"
                  checked={issueCert}
                  onChange={(e) => setIssueCert(e.target.checked)}
                  data-testid="admin-issue-cert-check"
                />
                <span>Tự động cấp &amp; gán chứng thư (.p12)</span>
              </label>
              <select
                value={certDays}
                onChange={(e) => setCertDays(Number(e.target.value))}
                style={{ width: "auto" }}
                data-testid="admin-cert-days"
              >
                <option value={365}>1 năm (365 ngày)</option>
                <option value={730}>2 năm (730 ngày)</option>
              </select>
            </>
          )}
          <button type="submit" className="btn primary small" disabled={busy}>
            {busy ? "Đang xử lý..." : "Tạo user"}
          </button>
        </form>
        <table className="tbl">
          <thead>
            <tr><th>User</th><th>Email</th><th>Chứng thư</th><th>Vai trò</th><th>2FA</th><th>Trạng thái</th><th>Thao tác</th></tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id}>
                <td>
                  <strong>{u.username}</strong>
                  {u.display_name && <div className="muted">{u.display_name}</div>}
                </td>
                <td>
                  {editEmailUid === u.id ? (
                    <div className="row">
                      <input
                        type="email"
                        value={editEmailVal}
                        onChange={(e) => setEditEmailVal(e.target.value)}
                        placeholder="email@domain"
                        style={{ minWidth: 160 }}
                      />
                      <button type="button" className="btn tiny primary" onClick={() => saveUserEmail(u.id)}>Lưu</button>
                      <button type="button" className="btn tiny" onClick={() => setEditEmailUid(null)}>Hủy</button>
                    </div>
                  ) : (
                    <div className="row">
                      <span>{u.email || "-"}</span>
                      <button
                        type="button"
                        className="btn tiny"
                        onClick={() => {
                          setEditEmailUid(u.id);
                          setEditEmailVal(u.email || "");
                        }}
                      >
                        Sửa
                      </button>
                    </div>
                  )}
                </td>
                <td>
                  {u.cert_refs && u.cert_refs.length > 0 ? (
                    <div className="row wrap">
                      {u.cert_refs.map((kr) => (
                        <span key={kr} className="badge ok">{kr}</span>
                      ))}
                    </div>
                  ) : (
                    <span className="muted">Chưa có</span>
                  )}
                </td>
                <td>{u.role}</td>
                <td>{u.totp_enabled ? "Bật" : "Chưa"}</td>
                <td>{u.is_active ? (u.locked ? "Đang khóa" : "Hoạt động") : "Vô hiệu"}</td>
                <td className="row wrap">
                  {caEnabled && (
                    <button
                      type="button"
                      className="btn tiny primary"
                      disabled={busy}
                      onClick={() => issueUserCert(u)}
                      data-testid={`admin-issue-user-cert-${u.username}`}
                    >
                      Cấp cert mới
                    </button>
                  )}
                  {certs.filter((c) => c.active).map((c) => (
                    <button key={c.id} type="button" className="btn tiny" onClick={() => assignCert(u.id, c.id)}>
                      Gán {c.key_ref}
                    </button>
                  ))}
                  {u.locked && (
                    <button type="button" className="btn tiny" onClick={() => apiJson(`/api/admin/users/${u.id}/unlock`, { method: "POST" }).then(loadAll)}>
                      Mở khóa
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card stack">
        <h3>Chứng thư đã đăng ký ({certs.length})</h3>
        {certs.length > 0 && (
          <table className="tbl">
            <thead>
              <tr><th>key_ref</th><th>Chủ thể (CN)</th><th>Serial</th><th>Hết hạn</th><th>Trạng thái</th><th>Thao tác</th></tr>
            </thead>
            <tbody>
              {certs.map((c) => (
                <tr key={c.id}>
                  <td className="mono">{c.key_ref}</td>
                  <td>{c.subject}</td>
                  <td className="mono">{c.serial}</td>
                  <td className="mono">{c.not_after.slice(0, 10)}</td>
                  <td>
                    <span className={`badge ${c.active ? "ok" : "bad"}`}>
                      {c.active ? "Hoạt động" : "Đã thu hồi / vô hiệu"}
                    </span>
                  </td>
                  <td>
                    {caEnabled && c.active && (
                      <button
                        type="button"
                        className="btn tiny danger"
                        disabled={busy}
                        onClick={() => revokeCert(c)}
                        data-testid={`admin-revoke-cert-${c.key_ref}`}
                      >
                        Thu hồi (Revoke)
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <h4 style={{ margin: "6px 0 0 0", fontSize: 13 }}>Chứng thư từ service signer (đăng ký thủ công)</h4>
        <div className="row wrap">
          {avail.map((a) => (
            <div key={a.key_ref} className="badge-box">
              <span>{a.key_ref} ({a.subject || a.error})</span>
              {!a.registered && !a.error && (
                <button type="button" className="btn tiny" onClick={() => registerCert(a.key_ref)}>
                  Đăng ký
                </button>
              )}
            </div>
          ))}
        </div>
      </div>

      <div className="card stack">
        <h3>Nhật ký kiểm toán (Audit Log — chỉ đọc)</h3>
        <table className="tbl">
          <thead>
            <tr><th>Thời gian</th><th>User</th><th>Hành động</th><th>Trạng thái</th><th>Chứng thư</th><th>IP</th></tr>
          </thead>
          <tbody>
            {audit.map((a) => (
              <tr key={a.id}>
                <td className="mono">{a.ts.slice(0, 19)}</td>
                <td>{a.username}</td>
                <td>{a.action}</td>
                <td>{a.status}</td>
                <td>{a.cert_key_ref || "-"}</td>
                <td className="mono">{a.ip}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
