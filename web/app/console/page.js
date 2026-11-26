"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { opsOverview, pending, decide } from "../../lib/api";
import { useSession } from "../../lib/session";
import { BarChart, Split, Stat, DemoClock } from "../../components/charts";
import { useTableControls, Toolbar, PRIORITIES, PRANK } from "../../components/table";

const FAC_COLOURS = ["var(--fac-a)", "var(--fac-b)", "var(--busy)", "var(--free)"];
function facColour(fid, all) {
  const ids = [...new Set((all || []).map((k) => k.facility_id))].sort();
  return FAC_COLOURS[ids.indexOf(fid) % FAC_COLOURS.length];
}

function Pri({ p }) {
  return <span className={`pri p-${(p || "").toLowerCase()}`}>{p}</span>;
}

function DockRow({ k, withFac, colour }) {
  return (
    <tr>
      {withFac && <td className="facn"><i className="fdot" style={{ background: colour }} />
        {k.facility_id}</td>}
      <td className="mono strong">{k.dock_code}</td>
      <td className="muted">{k.dock_type}</td>
      <td>
        {k.outage ? <span className="state s-down">{k.outage.split(",")[0]}</span>
          : k.busy_with ? <span className="state s-busy">unloading {k.busy_with}</span>
          : <span className="state s-free">free</span>}
      </td>
      <td className="mono">{k.upcoming}</td>
    </tr>
  );
}

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

  const att = useTableControls(d?.attention || [], {
    searchKeys: ["shipment_id", "driver", "customer", "priority", "why", "facility"],
    filters: [
      { key: "priority", label: "Priority", options: PRIORITIES,
        test: (r, v) => r.priority === v },
      { key: "why", label: "Reason",
        options: [...new Set((d?.attention || []).map((r) => r.why))],
        test: (r, v) => r.why === v },
    ],
    sorts: [
      { key: "urgency", label: "Most urgent", value: (r) => r.rank * 10 + (PRANK[r.priority] ?? 9) },
      { key: "delay",   label: "Most delayed", value: (r) => -(r.delay_min || 0) },
      { key: "eta",     label: "Earliest ETA", value: (r) => r.eta || "99:99" },
      { key: "ship",    label: "Shipment",     value: (r) => r.shipment_id },
    ],
  });

  const dk = useTableControls(d?.docks || [], {
    searchKeys: ["dock_code", "dock_type", "facility_name", "busy_with", "outage"],
    filters: [
      { key: "fac", label: "Facility",
        options: [...new Set((d?.docks || []).map((k) => k.facility_id))],
        test: (r, v) => r.facility_id === v },
      { key: "type", label: "Type",
        options: [...new Set((d?.docks || []).map((k) => k.dock_type))],
        test: (r, v) => r.dock_type === v },
      { key: "state", label: "State",
        options: [{ value: "free", label: "Free" }, { value: "busy", label: "Unloading" },
                  { value: "out", label: "Out of service" }],
        test: (r, v) => v === "out" ? !!r.outage
                      : v === "busy" ? !!r.busy_with && !r.outage
                      : !r.outage && !r.busy_with },
    ],
    sorts: [
      { key: "code", label: "Dock",         value: (r) => `${r.facility_id}${r.dock_code}` },
      { key: "load", label: "Busiest",      value: (r) => -(r.upcoming || 0) },
      { key: "open", label: "Most free",    value: (r) => -(r.open_slots || 0) },
    ],
  });

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
        <DemoClock now={d.now} day={d.day} />
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
          <Toolbar ctl={dk} noun="docks" placeholder="Search docks…" />
          {/* Both facilities have a D1, a D2 and a D3, so a dock code alone is ambiguous.
              Grouping by facility makes it unique again - but only while the list is in
              its natural order. Once someone sorts by busiest, a facility heading would
              be a lie, so the facility moves into its own column instead. */}
          <div className="scrollx">
          <table className="grid">
            <thead><tr>{dk.sort !== "code" || dk.dir !== "asc" ? <th>Facility</th> : null}
              <th>Dock</th><th>Type</th><th>State</th><th>Booked ahead</th></tr></thead>
            <tbody>
              {dk.sort === "code" && dk.dir === "asc"
                ? [...new Set(dk.rows.map((k) => k.facility_id))].flatMap((fid) => {
                    const inFac = dk.rows.filter((k) => k.facility_id === fid);
                    return [
                      <tr key={fid} className="grouphead">
                        <th colSpan={4}>
                          <i className="fdot" style={{ background: facColour(fid, d.docks) }} />
                          {inFac[0].facility_name} <em>{fid}</em>
                        </th>
                      </tr>,
                      ...inFac.map((k) => <DockRow k={k} key={k.dock_id} />),
                    ];
                  })
                : dk.rows.map((k) => <DockRow k={k} key={k.dock_id} withFac
                                              colour={facColour(k.facility_id, d.docks)} />)}
            </tbody>
          </table>
          </div>
          {dk.rows.length === 0 && <p className="empty">No dock matches that.</p>}
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
                  <td><Pri p={q.priority_code} /></td>
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
        <Toolbar ctl={att} noun="trucks" placeholder="Search driver, customer, shipment…" />
        {att.rows.length === 0
          ? <p className="empty">
              {d.attention.length ? "Nothing matches that filter." : "Nothing outstanding."}
            </p>
          : <div className="scrollx"><table className="grid">
              <thead><tr><th>Shipment</th><th>Driver</th><th>Customer</th><th>Priority</th>
                         <th>ETA</th><th>Delay</th><th>Why</th></tr></thead>
              <tbody>
                {att.rows.map((a) => (
                  <tr key={a.shipment_id}>
                    <td className="mono">{a.shipment_id}</td>
                    <td>{a.driver}</td>
                    <td className="trunc">{a.customer}</td>
                    <td><Pri p={a.priority} /></td>
                    <td className="mono">{a.eta}</td>
                    <td className={"mono " + (a.delay_min >= 30 ? "warnfg" : "")}>
                      {a.delay_min ? `+${a.delay_min}m` : "—"}</td>
                    <td><span className={`why r${a.rank}`}>{a.why}</span></td>
                  </tr>
                ))}
              </tbody>
            </table></div>}
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
