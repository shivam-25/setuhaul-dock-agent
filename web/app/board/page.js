"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { board } from "../../lib/api";
import { useSession } from "../../lib/session";
import { useTableControls, Toolbar } from "../../components/table";

const hm = (t) => (t || "").slice(11, 16);

export default function Board() {
  const { user } = useSession();
  const [rows, setRows] = useState([]);
  const [err, setErr] = useState(null);
  useEffect(() => {
    if (user && user.role !== "driver") board().then(setRows).catch((e) => setErr(String(e)));
  }, [user]);

  // The board is a grid, not a list. A search collapses it to the matching slots AND
  // their time columns together, so what is left is a found-location view rather than a
  // timetable with holes in it - a gap left behind by a hidden slot would read as "free",
  // which is the one thing a dock board must never say by accident. Matches are also
  // outlined, so a search that returns several is still scannable.
  const tc = useTableControls(rows, {
    searchKeys: ["dock_code", "dock_type", "facility_id", "appt_shipment", "slot_status",
                 "outage"],
    filters: [
      { key: "fac", label: "Facility",
        options: [...new Set(rows.map((r) => r.facility_id))],
        test: (r, v) => r.facility_id === v },
      { key: "type", label: "Dock type",
        options: [...new Set(rows.map((r) => r.dock_type))],
        test: (r, v) => r.dock_type === v },
    ],
  });

  if (!user) return null;
  if (user.role === "driver")
    return <main className="pad"><p className="empty">This page is for coordinators.</p></main>;
  if (err) return <main className="pad"><p className="empty">{err}</p></main>;

  const shown = tc.rows;
  const hit = new Set(tc.q.trim() ? shown.map((r) => r.slot_id) : []);
  const facs = [...new Set(shown.map((r) => r.facility_id))];
  const times = [...new Set(shown.map((r) => hm(r.slot_start_ts)))].sort();

  const cell = (r) => {
    if (!r) return { cls: "c-none", txt: "" };
    if (r.outage) return { cls: "c-out", txt: r.outage.split(",")[0].slice(0, 8) };
    if (r.appt_status) return { cls: "c-booked", txt: r.appt_shipment?.slice(-4) || "booked" };
    if (r.held_by) return { cls: "c-held", txt: "held" };
    if (r.slot_status === "OPEN") return { cls: "c-open", txt: "open" };
    return { cls: "c-none", txt: r.slot_status?.slice(0, 6).toLowerCase() || "" };
  };

  return (
    <main className="pad">
      <header className="phead">
        <div><p className="eyebrow">Operations</p><h1>Dock board</h1></div>
        <Link href="/console" className="ghost small">← Overview</Link>
      </header>

      <Toolbar ctl={tc} noun="slots" placeholder="Search dock, shipment, status…" />

      <div className="legend">
        {[["open", "var(--ok)"], ["held for 120s", "var(--hold)"],
          ["booked", "var(--booked)"], ["dock down or blocked", "var(--out)"]].map(([l, c]) => (
          <span key={l}><i style={{ background: c }} />{l}</span>))}
      </div>

      {facs.map((f) => {
        const docks = [...new Set(shown.filter((r) => r.facility_id === f)
                                      .map((r) => `${r.dock_code}|${r.dock_type}`))].sort();
        return (
          <section className="card" key={f}>
            <h2><i className="fdot" style={{ background: facs.indexOf(f) === 0
                 ? "var(--fac-a)" : "var(--fac-b)" }} />{f}</h2>
            <div className="scrollx">
              <table className="grid board">
                <thead>
                  <tr><th>Dock</th>{times.map((t) => <th key={t} className="mono">{t}</th>)}</tr>
                </thead>
                <tbody>
                  {docks.map((d) => {
                    const [code, type] = d.split("|");
                    return (
                      <tr key={d}>
                        <th className="rowh">
                          <span className="mono">{code}</span>
                          <em>{type}</em>
                        </th>
                        {times.map((t) => {
                          const r = shown.find((x) => x.facility_id === f && x.dock_code === code
                                                   && hm(x.slot_start_ts) === t);
                          const c = cell(r);
                          const lit = r && hit.size && hit.has(r.slot_id);
                          return <td key={t} className={`cell ${c.cls}${lit ? " lit" : ""}`}
                                     title={r ? `${code} ${t} — ${c.txt || r.slot_status}` : ""}>
                            {c.txt}</td>;
                        })}
                      </tr>);
                  })}
                </tbody>
              </table>
            </div>
          </section>
        );
      })}
    </main>
  );
}
