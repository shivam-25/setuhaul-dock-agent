"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { myTrips } from "../lib/api";
import { useSession } from "../lib/session";
import { BarChart, Progress, Stat, DemoClock, delayText } from "../components/charts";
import { useTableControls, Toolbar, PRIORITIES, PRANK } from "../components/table";

const LEVEL = { critical: "Act now", warn: "Check this", info: "For info", ok: "All good" };

export default function Home() {
  const { user } = useSession();
  const router = useRouter();
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    if (!user) return;
    myTrips().then(setD).catch((e) => setErr(e.message));
  }, [user]);

  // A driver with three trips does not need a filter bar; one with twenty does. It
  // appears at four, which is roughly where scanning stops working.
  const tc = useTableControls(d?.trips || [], {
    searchKeys: ["shipment_id", "customer_name", "origin_city", "facility_name",
                 "priority_code", "stage", "dock_code"],
    filters: [
      { key: "state", label: "Show",
        options: [{ value: "attention", label: "Needs attention" },
                  { value: "booked", label: "Dock booked" },
                  { value: "unbooked", label: "No dock yet" },
                  { value: "late", label: "Running late" }],
        test: (t, v) => v === "booked" ? !!t.appt_id
                      : v === "unbooked" ? !t.appt_id
                      : v === "late" ? t.delay_min >= 15
                      : t.alerts.some((a) => a.level !== "ok") },
      { key: "priority", label: "Priority", options: PRIORITIES,
        test: (t, v) => t.priority_code === v },
    ],
    sorts: [
      { key: "eta",   label: "Soonest arrival", value: (t) => t.eta_hm || "99:99" },
      { key: "delay", label: "Most delayed",    value: (t) => -(t.delay_min || 0) },
      { key: "pri",   label: "Priority",        value: (t) => PRANK[t.priority_code] ?? 9 },
      { key: "cust",  label: "Customer",        value: (t) => t.customer_name },
    ],
  });

  if (!user) return null;
  if (err) return <main className="pad"><p className="empty">{err}</p></main>;
  if (!d) return <main className="pad"><p className="empty">Loading your trips…</p></main>;

  // The same page serves a driver looking at their own two trucks and a coordinator
  // looking at all eighteen. Calling the whole fleet "My trips" was simply wrong, and so
  // was telling a coordinator that "your" booked window had passed.
  const ops = d.voice === "ops";

  const ask = (shipment, text) =>
    router.push(`/chat?shipment=${shipment}${text ? `&q=${encodeURIComponent(text)}` : ""}`);

  const everyAlert = d.trips.flatMap((t) =>
    t.alerts.map((a) => ({ ...a, shipment: t.shipment_id, customer: t.customer_name })));
  const needed = everyAlert.filter((a) => a.level !== "ok");

  return (
    <main className="pad">
      <header className="phead">
        <div>
          <p className="eyebrow">
            {ops ? `Operations · ${user.facility_id || "all facilities"}`
                 : `Driver · ${user.display_name}`}
          </p>
          <h1>{ops ? "Every truck today" : "My trips"}</h1>
        </div>
        <DemoClock now={d.now} day={d.day} />
      </header>

      <section className="statrow">
        <Stat n={d.summary.active}   label={ops ? "trucks moving" : "active trips"} />
        <Stat n={d.summary.booked}   label="dock booked" tone={d.summary.booked ? "ok" : ""} />
        <Stat n={d.summary.unbooked} label="no dock yet" tone={d.summary.unbooked ? "warn" : ""} />
        <Stat n={d.summary.late}     label="running late" tone={d.summary.late ? "warn" : ""} />
      </section>

      {needed.length > 0 && (
        <section className="card alerts">
          <h2>{ops ? "What needs someone" : "What needs you"}</h2>
          {needed.map((a, i) => (
            <div className={`alert ${a.level}`} key={i}>
              <div className="atop">
                <span className="tag">{LEVEL[a.level]}</span>
                <b>{a.title}</b>
                <span className="muted">{a.shipment} · {a.customer}</span>
              </div>
              <p>{a.body}</p>
              {a.action === "ask" && !ops && (
                <button className="ghost small" onClick={() => ask(a.shipment, a.ask)}>
                  Ask about this →
                </button>
              )}
            </div>
          ))}
        </section>
      )}

      {d.delay_chart.length > 0 && (
        <section className="card">
          <h2>{ops ? "How late each truck is running" : "How late each trip is running"}</h2>
          <p className="sub">Minutes against the original plan. Zero means on time.</p>
          <BarChart data={d.delay_chart} unit=" min"
                    accent={d.summary.late ? "var(--hold)" : "var(--ok)"} />
        </section>
      )}

      {/* A filter bar over two trips is furniture. It appears at four, which is roughly
          where a driver stops being able to scan the list. */}
      <div className="listhead plain">
        <h2>{ops ? "All trucks" : d.trips.length === 1 ? "Your trip" : "Your trips"}</h2>
        {d.trips.length >= 4 && <Toolbar ctl={tc} noun="trips"
                                         placeholder="Search customer, city, shipment…" />}
      </div>

      <section className="trips">
        {(d.trips.length >= 4 ? tc.rows : d.trips).map((t) => (
          <article className="card trip" key={t.shipment_id}>
            <div className="thead">
              <div className="tid">
                <h3>{t.customer_name}</h3>
                <p className="muted">
                  {t.shipment_id} · {t.origin_city} → {t.facility_name}
                </p>
              </div>
              <span className={`pri p-${t.priority_code.toLowerCase()}`}>{t.priority_code}</span>
            </div>

            <dl className="facts">
              <div><dt>Status</dt><dd>{t.stage}</dd></div>
              <div><dt>Planned arrival</dt><dd>{t.plan_hm || "—"}</dd></div>
              <div>
                <dt>Now expected</dt>
                <dd className={t.delay_min >= 15 ? "warnfg" : ""}>
                  {t.eta_hm || "—"}{t.delay_min ? ` (${delayText(t.delay_min)})` : ""}
                </dd>
              </div>
              <div>
                <dt>Dock</dt>
                <dd>{t.dock_code ? `${t.dock_code} · ${t.slot_hm}` : "not booked"}</dd>
              </div>
              <div><dt>Load</dt><dd>{t.pallet_count} pallets · {t.load_weight_kg} kg</dd></div>
              <div><dt>Unload time</dt><dd>{t.expected_unload_min} min</dd></div>
            </dl>

            <Progress stages={t.stages} />

            <div className="trowbtns">
              {!ops && (
                <button className="primary small" onClick={() => ask(t.shipment_id)}>
                  Message the dock desk
                </button>
              )}
              {ops && <span className="whoname">{t.driver_name}</span>}
              {!ops && !t.appt_id && (
                <button className="ghost small"
                        onClick={() => ask(t.shipment_id, "I am running late. What slots can I get?")}>
                  I am running late
                </button>
              )}
              {!ops && t.appt_id && (
                <button className="ghost small"
                        onClick={() => ask(t.shipment_id, "Is my booked slot still valid?")}>
                  Check my slot
                </button>
              )}
            </div>
          </article>
        ))}
        {d.trips.length >= 4 && tc.rows.length === 0 && (
          <p className="empty">No trip matches that.</p>
        )}
      </section>
    </main>
  );
}
