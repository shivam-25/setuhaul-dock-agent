"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { audit } from "../../lib/api";
import { useSession } from "../../lib/session";

export default function Audit() {
  const { user } = useSession();
  const [log, setLog] = useState([]);
  const [err, setErr] = useState(null);
  useEffect(() => { if (user) audit().then(setLog).catch((e) => setErr(String(e))); }, [user]);

  if (!user) return null;
  if (err) return <main className="pad"><p className="empty">{err}</p></main>;

  return (
    <main className="pad">
      <header className="phead">
        <div>
          <p className="eyebrow">Decision log</p>
          <h1>Why the agent did that</h1>
        </div>
        {user.role !== "driver" && <Link href="/console" className="ghost small">← Overview</Link>}
      </header>
      <section className="card">
        <p className="sub">
          Every decision the agent took, in order, with what it was looking at.
          {user.role === "driver" && " Only your own trips appear here."}
        </p>
        <div className="scrollx">
          <table className="grid">
            <thead><tr><th>Time</th><th>Step</th><th>Decision</th><th>Shipment</th>
                       <th>Detail</th></tr></thead>
            <tbody>
              {log.map((a) => (
                <tr key={a.audit_id}>
                  <td className="mono">{(a.ts || a.at || "").slice(11, 19)}</td>
                  <td className="mono">{a.box}</td>
                  <td><span className="pill p-plain">{a.decision}</span></td>
                  <td className="mono">{a.shipment_id || "—"}</td>
                  <td className="detail">{a.detail_json || ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {log.length === 0 && <p className="empty">Nothing recorded yet.</p>}
      </section>
    </main>
  );
}
