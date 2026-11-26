"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { changePw } from "../../lib/api";
import { useSession } from "../../lib/session";

export default function Account() {
  const { user, refresh, signOut } = useSession();
  const [cur, setCur] = useState("");
  const [nw, setNw] = useState("");
  const [again, setAgain] = useState("");
  const [msg, setMsg] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const router = useRouter();

  const mismatch = again.length > 0 && nw !== again;

  async function submit(e) {
    e.preventDefault();
    setErr(null); setMsg(null); setBusy(true);
    try {
      await changePw({ current_password: cur, new_password: nw });
      await refresh();
      setMsg("Password changed. Every other session has been signed out.");
      setCur(""); setNw(""); setAgain("");
      setTimeout(() => router.replace(user?.role === "driver" ? "/" : "/console"), 900);
    } catch (ex) { setErr(ex.message); }
    finally { setBusy(false); }
  }

  if (!user) return null;
  return (
    <main className="wrap auth">
      <form className="card auth-card" onSubmit={submit}>
        <h1 className="disp">Your password</h1>
        <p className="muted small">
          Signed in as <b>{user.display_name}</b> ({user.username}, {user.role}).
        </p>
        {user.must_change_password && (
          <p className="notice">
            This is still the password you were given, so other people have seen it.
            Choose your own before going further.
          </p>
        )}
        <label className="fld"><span>Current password</span>
          <input type="password" value={cur} onChange={(e) => setCur(e.target.value)}
                 autoComplete="current-password" required /></label>
        <label className="fld"><span>New password</span>
          <input type="password" value={nw} onChange={(e) => setNw(e.target.value)}
                 autoComplete="new-password" required />
          <em className="hint">{nw.length === 0 ? "At least 12 characters."
            : nw.length < 12 ? `${12 - nw.length} more character${12 - nw.length === 1 ? "" : "s"} needed.`
            : "Long enough."}</em></label>
        <label className="fld"><span>New password again</span>
          <input type="password" value={again} onChange={(e) => setAgain(e.target.value)}
                 autoComplete="new-password" required />
          {mismatch && <em className="hint bad">These two do not match.</em>}</label>
        {err && <p className="err" role="alert">{err}</p>}
        {msg && <p className="good" role="status">{msg}</p>}
        <button className="primary" disabled={busy || nw.length < 12 || mismatch || !cur}>
          {busy ? "Saving…" : "Change password"}
        </button>
        <button type="button" className="ghost" onClick={signOut}>Sign out</button>
      </form>
    </main>
  );
}
