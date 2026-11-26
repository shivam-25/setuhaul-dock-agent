"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { board } from "../../lib/api";
import { useSession } from "../../lib/session";

const hm = (t) => (t || "").slice(11, 16);

export default function Board() {
  const { user } = useSession();
  const [rows, setRows] = useState([]);
  const [err, setErr] = useState(null);
  useEffect(() => {
    if (user && user.role !== "driver") board().then(setRows).catch((e) => setErr(String(e)));
  }, [user]);

  if (!user) return null;
  if (user.role === "driver")
    return <main className="pad"><p className="empty">This page is for coordinators.</p></main>;
  if (err) return <main className="pad"><p className="empty">{err}</p></main>;

  const facs = [...new Set(rows.map((r) => r.facility_id))];
  const times = [...new Set(rows.map((r) => hm(r.slot_start_ts)))].sort();

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

      <div className="legend">
        {[["open", "var(--ok)"], ["held for 120s", "var(--hold)"],
          ["booked", "var(--booked)"], ["dock down or blocked", "var(--out)"]].map(([l, c]) => (
          <span key={l}><i style={{ background: c }} />{l}</span>))}
      </div>

      {facs.map((f) => {
        const docks = [...new Set(rows.filter((r) => r.facility_id === f)
                                      .map((r) => `${r.dock_code}|${r.dock_type}`))].sort();
        return (
          <section className="card" key={f}>
            <h2>{f}</h2>
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
                          const r = rows.find((x) => x.facility_id === f && x.dock_code === code
                                                  && hm(x.slot_start_ts) === t);
                          const c = cell(r);
                          return <td key={t} className={`cell ${c.cls}`}
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
