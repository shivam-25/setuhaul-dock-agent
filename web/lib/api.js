// Same-origin by design: /api is rewritten to the service in next.config.mjs, so the
// browser attaches the HttpOnly session cookie automatically and no token is ever held
// in JavaScript. Nothing here reads or writes the cookie - it cannot, which is the point.
export const API = "";

async function j(path, opts) {
  const r = await fetch(`${API}${path}`, {
    ...opts,
    credentials: "same-origin",
    headers: { "content-type": "application/json", ...(opts?.headers || {}) },
  });
  if (r.status === 401) {
    const e = new Error("not signed in"); e.unauthorized = true; throw e;
  }
  if (!r.ok) {
    let detail = await r.text();
    try { detail = JSON.parse(detail).detail ?? detail; } catch {}
    const e = new Error(detail || `${r.status}`); e.status = r.status; throw e;
  }
  return r.json();
}

export const send    = (b) => j("/api/chat", { method: "POST", body: JSON.stringify(b) });
export const reset   = ()  => j("/api/reset", { method: "POST" });
export const board   = ()  => j("/api/board");
export const why     = (s) => j(`/api/why/${s}`);
export const audit   = ()  => j("/api/audit?limit=40");
export const drivers = ()  => j("/api/drivers");
export const meta    = ()  => j("/api/meta");
export const pending = ()  => j("/api/pending");
export const decide  = (b) => j("/api/decide", { method: "POST", body: JSON.stringify(b) });
export const alerts  = ()  => j("/api/alerts");
export const metrics = ()  => j("/api/metrics");

export const login    = (b) => j("/api/auth/login",  { method: "POST", body: JSON.stringify(b) });
export const logout   = ()  => j("/api/auth/logout", { method: "POST" });
export const me       = ()  => j("/api/auth/me");
export const changePw = (b) => j("/api/auth/change-password",
                                 { method: "POST", body: JSON.stringify(b) });
export const issueReset = (b) => j("/api/auth/reset", { method: "POST", body: JSON.stringify(b) });

export const myTrips    = ()  => j("/api/me/trips");
export const opsOverview = () => j("/api/ops/overview");
