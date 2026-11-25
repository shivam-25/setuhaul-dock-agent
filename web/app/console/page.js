"use client";
import { useEffect, useState } from "react";
import { board, why, audit, drivers } from "../../lib/api";

const hm = (ts) => (ts || "").slice(11, 16);

function state(r) {
  if (r.outage) return ["outage", "var(--out)"];
  if (r.slot_status !== "OPEN") return ["blocked", "var(--out)"];
  if (r.appt_status === "CONFIRMED" || r.appt_status === "IN_PROGRESS") return ["booked", "var(--booked)"];
  if (r.appt_status) return ["pending", "var(--hold)"];
  if (r.held_by) return ["held", "var(--hold)"];
  return ["open", "var(--ok)"];
}

export default function Console() {
  const [rows, setRows] = useState([]);
  const [log, setLog] = useState([]);
  const [ds, setDs] = useState([]);
  const [pick, setPick] = useState("");
  const [w, setW] = useState(null);
  const [err, setErr] = useState(null);

  const refresh = () => {
    board().then(setRows).catch((e) => setErr(String(e)));
    audit().then(setLog).catch(() => {});
  };
  useEffect(() => { refresh(); drivers().then(setDs).catch(() => {}); }, []);
  useEffect(() => { if (pick) why(pick).then(setW).catch((e) => setErr(String(e))); }, [pick]);

  const facs = [...new Set(rows.map((r) => r.facility_id))];
  const times = [...new Set(rows.map((r) => hm(r.slot_start_ts)))].sort();

  return (
    <main className="wrap" style={{ paddingBlock: 22, display: "flex", flexDirection: "column",
                                    gap: 20 }}>
      <div style={{ display: "flex", gap: 12, alignItems: "flex-end", flexWrap: "wrap" }}>
        <div><p className="eyebrow">coordinator</p><h1>Dock board</h1></div>
        <button onClick={refresh} style={{ marginLeft: "auto" }}>Refresh</button>
      </div>

      {err && <div className="card" style={{ borderLeft: "3px solid var(--stop)" }}>
        <strong>API error.</strong> <span className="mono" style={{ fontSize: 12 }}>{err}</span></div>}

      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        {[["open", "var(--ok)"], ["held 120s", "var(--hold)"], ["booked", "var(--booked)"],
          ["blocked or dock down", "var(--out)"]].map(([l, c]) => (
          <span key={l} style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 12,
                                 color: "var(--muted)" }}>
            <i style={{ width: 11, height: 11, background: c, borderRadius: 2 }} />{l}</span>))}
      </div>

      {facs.map((f) => {
        const docks = [...new Set(rows.filter((r) => r.facility_id === f)
                                      .map((r) => `${r.dock_code}|${r.dock_type}`))];
        return (
          <section key={f} className="card scroll">
            <h3 style={{ marginBottom: 10 }}>{f}</h3>
            <table style={{ minWidth: 760 }}>
              <thead><tr><th>dock</th>{times.map((t) => <th key={t} className="mono">{t}</th>)}</tr></thead>
              <tbody>
                {docks.map((d) => {
                  const [code, type] = d.split("|");
                  return (
                    <tr key={d}>
                      <td className="mono" style={{ whiteSpace: "nowrap" }}>
                        <strong>{code}</strong> <span style={{ color: "var(--muted)" }}>{type}</span>
                      </td>
                      {times.map((t) => {
                        const r = rows.find((x) => x.facility_id === f && x.dock_code === code
                                                && hm(x.slot_start_ts) === t);
                        if (!r) return <td key={t} />;
                        const [lbl, col] = state(r);
                        return (
                          <td key={t} title={`${r.slot_id} — ${lbl}${r.appt_shipment ? " " + r.appt_shipment : ""}${r.outage ? " " + r.outage : ""}`}
                              style={{ padding: 3 }}>
                            <div style={{ background: col, opacity: lbl === "open" ? .22 : .85,
                                          borderRadius: 2, height: 24, display: "flex",
                                          alignItems: "center", justifyContent: "center",
                                          fontSize: 9.5, fontFamily: "var(--ff-mono)",
                                          color: lbl === "open" ? "var(--fg)" : "#fff" }}>
                              {r.appt_shipment ? r.appt_shipment.replace("SHP", "") : lbl === "open" ? "" : lbl[0].toUpperCase()}
                            </div>
                          </td>);
                      })}
                    </tr>);
                })}
              </tbody>
            </table>
          </section>);
      })}

      <section className="card" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <div>
          <p className="eyebrow">why this answer</p>
          <h2>Every candidate, and why each was refused</h2>
        </div>
        <select value={pick} onChange={(e) => setPick(e.target.value)} style={{ maxWidth: 420 }}>
          <option value="">— pick a shipment —</option>
          {ds.flatMap((d) => d.shipments).map((s) => <option key={s.shipment_id} value={s.shipment_id}>
            {s.shipment_id} · {s.priority_code} · {s.required_dock_type} · {s.expected_unload_min} min
          </option>)}
        </select>
        {w && <>
          <p className="mono" style={{ fontSize: 12, color: "var(--muted)", margin: 0 }}>
            eta {hm(w.effective_eta + ":00")} {w.confidence} · ready {w.ready_at} ·
            {" "}{w.required_dock_type} · {w.unload_minutes} min · {w.priority} ·
            {" "}<strong style={{ color: "var(--fg)" }}>{w.feasible} of {w.considered} feasible</strong>
          </p>
          {w.accepted.length > 0 && <div style={{ display: "flex", gap: 7, flexWrap: "wrap" }}>
            {w.accepted.map((a, i) => <span key={a.label} className={`tag ${i === 0 ? "t-ok" : "t-muted"}`}>
              {i === 0 ? "would offer → " : ""}{a.dock} {a.start}–{a.end}</span>)}
          </div>}
          {w.feasible === 0 && <p className="tag t-stop" style={{ alignSelf: "flex-start" }}>
            nothing feasible — must escalate</p>}
          <div className="scroll"><table>
            <thead><tr><th>candidate</th><th>refused because</th></tr></thead>
            <tbody>{w.rejected.map((r) => <tr key={r.label}>
              <td className="mono" style={{ whiteSpace: "nowrap" }}>{r.dock} {r.start}</td>
              <td>{r.reasons.join(" · ")}</td></tr>)}</tbody>
          </table></div>
        </>}
      </section>

      <section className="card scroll">
        <p className="eyebrow">audit trail</p>
        <h2 style={{ marginBottom: 10 }}>Append-only decision log</h2>
        <table><thead><tr><th>when</th><th>box</th><th>decision</th><th>shipment</th>
          <th>thread</th><th>request</th></tr></thead>
          <tbody>{log.map((a) => <tr key={a.audit_id}>
            <td className="mono">{hm(a.decided_at || a.created_at)}</td>
            <td className="mono">{a.box}</td>
            <td><span className={`tag ${a.decision === "escalated" || a.decision === "refused_injection"
                   ? "t-stop" : a.decision === "auto" ? "t-ok" : "t-hold"}`}>{a.decision}</span></td>
            <td className="mono">{a.shipment_id || ""}</td>
            <td className="mono">{a.thread_id || ""}</td>
            <td className="mono" style={{ fontSize: 10.5, color: "var(--muted)" }}>{a.request_id}</td>
          </tr>)}</tbody></table>
        {log.length === 0 && <p style={{ fontSize: 13, color: "var(--muted)" }}>
          No decisions yet. Send a driver message first.</p>}
      </section>
    </main>
  );
}
