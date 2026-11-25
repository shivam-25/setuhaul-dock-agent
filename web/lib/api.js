export const API = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8080";

const TOKEN = process.env.NEXT_PUBLIC_DEMO_TOKEN || "";

async function j(path, opts) {
  const r = await fetch(`${API}${path}`, {
    ...opts,
    headers: {
      "content-type": "application/json",
      ...(TOKEN ? { "x-demo-token": TOKEN } : {}),
      ...(opts?.headers || {}),
    },
  });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

export const send   = (body) => j("/api/chat", { method: "POST", body: JSON.stringify(body) });
export const reset  = ()     => j("/api/reset", { method: "POST" });
export const board  = ()     => j("/api/board");
export const why    = (s)    => j(`/api/why/${s}`);
export const audit  = ()     => j("/api/audit?limit=40");
export const drivers= ()     => j("/api/drivers");
export const meta   = ()     => j("/api/meta");
