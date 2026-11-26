"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { login } from "../../lib/api";
import { useSession } from "../../lib/session";

export default function Login() {
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const router = useRouter();
  const { refresh } = useSession();

  async function submit(e) {
    e.preventDefault();
    setErr(null); setBusy(true);
    try {
      const r = await login({ username: u.trim().toLowerCase(), password: p });
      await refresh();
      router.replace(r.must_change_password ? "/account"
                   : r.role === "driver" ? "/" : "/console");
    } catch (ex) { setErr(ex.message || "sign-in failed"); }
    finally { setBusy(false); }
  }

  return (
    <main className="wrap auth">
      <form className="card auth-card" onSubmit={submit}>
        <h1 className="disp">Sign in</h1>
        <p className="muted small">
          Drivers sign in with their driver id, for example <code>drv006</code>.
          SetuHaul operations use <code>ops</code>. A warehouse uses <code>wh-jai</code>.
        </p>
        <label className="fld">
          <span>Username</span>
          <input value={u} onChange={(e) => setU(e.target.value)} autoComplete="username"
                 autoFocus required placeholder="drv006" />
        </label>
        <label className="fld">
          <span>Password</span>
          <input type="password" value={p} onChange={(e) => setP(e.target.value)}
                 autoComplete="current-password" required />
        </label>
        {err && <p className="err" role="alert">{err}</p>}
        <button className="primary" disabled={busy || !u || !p}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
