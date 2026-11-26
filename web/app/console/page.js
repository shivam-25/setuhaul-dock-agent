"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { opsOverview, pending, decide } from "../../lib/api";
import { useSession } from "../../lib/session";
import { BarChart, Split, Stat } from "../../components/charts";

const SEV = { critical: "Act now", high: "Act now", medium: "Watch", low: "For info" };

export default function Console() {
  const { user } = useSession();
  const [d, setD] = useState(null);
  const [queue, setQueue] = useState([]);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState("");

  const load = () => {
    opsOverview().then(setD).catch((e) => setErr(e.message));
    pending().then(setQueue).catch(() => {});
  };
  useEffect(() => { if (user && user.role !== "driver") load(); }, [user]);

  if (!user) return null;
  if (user.role === "driver")
    return <main className="pad"><p className="empty">This page is for coordinators.</p></main>;
  if (err) return <main className="pad"><p className="empty">{err}</p></main>;
  if (!d) return <main className="pad"><p className="empty">Loading operations…</p></main>;

  async function act(appointment_id, accept) {
    setBusy(appointment_id);
    try { await decide({ appointment_id, accept }); load(); }
    catch (e) { setErr(e.message); }
    finally { setBusy(""); }
  }

  const m = d.metrics || {};
  return (
    <main className="pad">
      <header className="phead">
        <div>
          <p className="eyebrow">Operations · {user.display_name}</p>
          <h1>Today at {d.scope}</h1>
        </div>
        <p className="clock">{d.now}</p>
      </header>

      <section className="statrow">
        <Stat n={d.attention_total} label="trucks needing a decision"
              tone={d.attention_total ? "warn" : "ok"} />
        <Stat n={queue.length} label="awaiting your acceptance" tone={queue.length ? "warn" : ""} />
        <Stat n={d.dock_summary.free} label="docks free now" tone="ok" />
        <Stat n={d.dock_summary.out} label="docks out of service"
              tone={d.dock_summary.out ? "bad" : ""} />
      </section>

      <div className="twocol">
        <section className="card">
          <h2>Dock capacity right now</h2>
          <Split total={d.dock_summary.total} parts={[
            { label: "unloading", value: d.dock_summary.busy, color: "var(--booked)" },
            { label: "free",      value: d.dock_summary.free, color: "var(--ok)" },
            { label: "out",       value: d.dock_summary.out,  color: "var(--out)" },
          ]} />
          {/* Both facilities have a D1, a D2 and a D3, so a dock code alone is ambiguous -
              the table listed each twice with nothing to tell them apart. Group by
              facility and the code is unique again within its group. */}
          <table className="grid">
            <thead><tr><th>Dock</th><th>Type</th><th>State</th><th>Booked ahead</th></tr></thead>
            <tbody>
              {[...new Set(d.docks.map((k) => k.facility_id))].flatMap((fid) => {
                const inFac = d.docks.filter((k) => k.facility_id === fid);
                return [
                  <tr key={fid} className="grouphead">
                    <th colSpan={4}>{inFac[0].facility_name} <em>{fid}</em></th>
                  </tr>,
                  ...inFac.map((k) => (
                <tr key={k.dock_id}>
                  <td className="mono">{k.dock_code}</td>
                  <td>{k.dock_type}</td>
                  <td>
                    {k.outage ? <span className="pill p-out">{k.outage}</span>
                      : k.busy_with ? <span className="pill p-busy">unloading {k.busy_with}</span>
                      : <span className="pill p-ok">free</span>}
                  </td>
                  <td>{k.upcoming}</td>
                </tr>
              ))];
              })}
            </tbody>
          </table>
        </section>

        <section className="card">
          <h2>When the day is busy</h2>
          <p className="sub">Appointments already committed, by start hour.</p>
          <BarChart data={d.load_chart} />
        </section>
      </div>

      {queue.length > 0 && (
        <section className="card">
          <h2>Waiting for you to accept</h2>
          <p className="sub">
            The driver has been told these are held, not confirmed. Nothing is promised
            until you accept.
          </p>
          <table className="grid">
            <thead><tr><th>Shipment</th><th>Driver</th><th>Dock</th><th>Window</th><th>Priority</th><th></th></tr></thead>
            <tbody>
              {queue.map((q) => (
                <tr key={q.appointment_id}>
                  <td className="mono">{q.shipment_id}</td>
                  <td className="mono">{q.driver_id}</td>
                  <td className="mono">{q.dock_code}</td>
                  <td>{(q.starts || "").slice(11, 16)}–{(q.ends || "").slice(11, 16)}</td>
                  <td><span className={`pill p-${(q.priority_code||"").toLowerCase()}`}>{q.priority_code}</span></td>
                  <td className="actions">
                    <button className="primary small" disabled={busy === q.appointment_id}
                            onClick={() => act(q.appointment_id, true)}>Accept</button>
                    <button className="ghost small" disabled={busy === q.appointment_id}
                            onClick={() => act(q.appointment_id, false)}>Reject</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <section className="card">
        <h2>Trucks needing a decision</h2>
        <p className="sub">Worst first: a passed window, then no dock, then waiting on you.</p>
        {d.attention.length === 0
          ? <p className="empty">Nothing outstanding.</p>
          : <table className="grid">
              <thead><tr><th>Shipment</th><th>Driver</th><th>Customer</th><th>Priority</th>
                         <th>ETA</th><th>Why</th></tr></thead>
              <tbody>
                {d.attention.map((a) => (
                  <tr key={a.shipment_id}>
                    <td className="mono">{a.shipment_id}</td>
                    <td>{a.driver}</td>
                    <td className="trunc">{a.customer}</td>
                    <td><span className={`pill p-${a.priority.toLowerCase()}`}>{a.priority}</span></td>
                    <td className="mono">{a.eta}</td>
                    <td>{a.why}</td>
                  </tr>
                ))}
              </tbody>
            </table>}
      </section>

      {d.alerts?.length > 0 && (
        <section className="card alerts">
          <h2>From the agent</h2>
          <p className="sub">Every one of these is a decision the agent declined to take alone.</p>
          {d.alerts.map((a, i) => (
            <div className={`alert ${a.severity === "critical" || a.severity === "high"
                               ? "critical" : a.severity === "medium" ? "warn" : "info"}`} key={a.audit_id}>
              <div className="atop">
                <span className="tag">{SEV[a.severity] || "For info"}</span>
                <b>{a.label}</b>
                <span className="muted">{a.shipment_id} · {(a.at || "").slice(11, 16)}</span>
              </div>
              <p>
                {a.route_blocker
                  ? `Nobody will be told: ${a.route_blocker}. This needs a person now.`
                  : a.reason || "Handed over for a person to decide."}
              </p>
            </div>
          ))}
        </section>
      )}

      <section className="card">
        <h2>How the service is doing today</h2>
        <div className="mrow">
          <Stat n={m.automation_share != null
                     ? `${Math.round(m.automation_share * 100)}%` : "—"}
                label="resolved without a person" />
          <Stat n={m.handed_to_human ?? "—"} label="passed to a person" />
          <Stat n={m.conflicting_allocations ?? 0} label="double bookings"
                tone={m.conflicting_allocations ? "bad" : "ok"} />
          <Stat n={m.slot_utilisation != null
                     ? `${Math.round(m.slot_utilisation * 100)}%` : "—"}
                label="slots in use" />
          <Stat n={m.wait_avg_gate_to_dock_min ?? "—"} label="avg gate→dock (min)" />
          <Stat n={m.duplicates_replayed ?? "—"} label="duplicates replayed" />
        </div>
        <p className="sub">
          Full board and the decision log: <Link href="/board">dock board</Link> ·{" "}
          <Link href="/audit">audit trail</Link>
        </p>
      </section>
    </main>
  );
}
