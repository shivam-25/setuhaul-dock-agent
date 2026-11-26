"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { audit } from "../../lib/api";
import { useSession } from "../../lib/session";
import { useTableControls, Toolbar } from "../../components/table";

export default function Audit() {
  const { user } = useSession();
  const [log, setLog] = useState([]);
  const [err, setErr] = useState(null);
  useEffect(() => { if (user) audit().then(setLog).catch((e) => setErr(String(e))); }, [user]);

  const tc = useTableControls(log, {
    searchKeys: ["decision", "shipment_id", "box", "detail_json", "thread_id"],
    filters: [
      { key: "decision", label: "Decision",
        options: [...new Set(log.map((a) => a.decision))].sort(),
        test: (a, v) => a.decision === v },
      { key: "box", label: "Step",
        options: [...new Set(log.map((a) => String(a.box)))].sort((x, y) => x - y),
        test: (a, v) => String(a.box) === v },
    ],
    sorts: [
      { key: "recent", label: "Most recent", value: (a) => -(a.audit_id || 0) },
      { key: "ship",   label: "Shipment",    value: (a) => a.shipment_id || "zz" },
      { key: "dec",    label: "Decision",    value: (a) => a.decision },
    ],
  });

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
        <Toolbar ctl={tc} noun="decisions" placeholder="Search decision, shipment, detail…" />
        <div className="scrollx">
          <table className="grid">
            <thead><tr><th>Time</th><th>Step</th><th>Decision</th><th>Shipment</th>
                       <th>Detail</th></tr></thead>
            <tbody>
              {tc.rows.map((a) => (
                <tr key={a.audit_id}>
                  <td className="mono">{(a.ts || a.at || "").slice(11, 19)}</td>
                  <td className="mono">{a.box}</td>
                  <td><span className={`state d-${String(a.decision).replace(/[^a-z]/gi, "")}`}>
                    {a.decision}</span></td>
                  <td className="mono">{a.shipment_id || "—"}</td>
                  <td className="detail">{a.detail_json || ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {tc.rows.length === 0 && (
          <p className="empty">{log.length ? "Nothing matches that." : "Nothing recorded yet."}</p>
        )}
      </section>
    </main>
  );
}
