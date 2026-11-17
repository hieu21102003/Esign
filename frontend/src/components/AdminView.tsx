import React, { useEffect, useState } from "react";
import { apiJson } from "../lib/api";

interface AdminUser {
  id: number;
  username: string;
  display_name: string;
  role: "admin" | "user";
  is_active: boolean;
  totp_enabled: boolean;
  locked: boolean;
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
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  async function loadAll() {
    const [u, c, av, au, ew] = await Promise.all([
      apiJson<AdminUser[]>("/api/admin/users"),
      apiJson<AdminCert[]>("/api/admin/certificates"),
      apiJson<AvailCert[]>("/api/admin/certificates/available"),
      apiJson<AuditItem[]>("/api/admin/audit?limit=50"),
      apiJson<{ warnings: ExpiryItem[] }>("/api/admin/expiry-warnings?days=30").catch(() => ({ warnings: [] })),
    ]);
    setUsers(u);
    setCerts(c);
    setAvail(av);
    setAudit(au);
    setWarnings(ew.warnings || []);
  }

  useEffect(() => {
    loadAll().catch((ex) => setErr(ex instanceof Error ? ex.message : "Không tải được trang quản trị"));
  }, []);

  async function handleCreateUser(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    setMsg("");
    try {
      await apiJson("/api/admin/users", {
        method: "POST",
        body: JSON.stringify({ username, display_name: displayName, password, role: "user" }),
      });
      setUsername("");
      setDisplayName("");
      setPassword("");
      setMsg("Đã tạo người dùng mới");
      await loadAll();
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Lỗi tạo người dùng");
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
        <h3>Tạo người dùng</h3>
        <form onSubmit={handleCreateUser} className="row wrap">
          <input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="username" required />
          <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="Tên hiển thị" />
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Mật khẩu (>=10 ký tự)" required />
          <button type="submit" className="btn primary small">Tạo user</button>
        </form>
        <table className="tbl">
          <thead>
            <tr><th>User</th><th>Vai trò</th><th>2FA</th><th>Trạng thái</th><th>Thao tác</th></tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id}>
                <td>{u.username}</td>
                <td>{u.role}</td>
                <td>{u.totp_enabled ? "Bật" : "Chưa"}</td>
                <td>{u.is_active ? (u.locked ? "Đang khóa" : "Hoạt động") : "Vô hiệu"}</td>
                <td className="row">
                  {certs.map((c) => (
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
        <h3>Chứng thư từ service signer</h3>
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
