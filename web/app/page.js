"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { myTrips } from "../lib/api";
import { useSession } from "../lib/session";
import { BarChart, Progress, Stat } from "../components/charts";

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

  if (!user) return null;
  if (err) return <main className="pad"><p className="empty">{err}</p></main>;
  if (!d) return <main className="pad"><p className="empty">Loading your trips…</p></main>;

  const ask = (shipment, text) =>
    router.push(`/chat?shipment=${shipment}${text ? `&q=${encodeURIComponent(text)}` : ""}`);

  const everyAlert = d.trips.flatMap((t) =>
    t.alerts.map((a) => ({ ...a, shipment: t.shipment_id, customer: t.customer_name })));
  const needed = everyAlert.filter((a) => a.level !== "ok");

  return (
    <main className="pad">
      <header className="phead">
        <div>
          <p className="eyebrow">Driver · {user.display_name}</p>
          <h1>My trips</h1>
        </div>
        <p className="clock">{d.now}</p>
      </header>

      <section className="statrow">
        <Stat n={d.summary.active}   label="active trips" />
        <Stat n={d.summary.booked}   label="dock booked" tone={d.summary.booked ? "ok" : ""} />
        <Stat n={d.summary.unbooked} label="no dock yet" tone={d.summary.unbooked ? "warn" : ""} />
        <Stat n={d.summary.late}     label="running late" tone={d.summary.late ? "warn" : ""} />
      </section>

      {needed.length > 0 && (
        <section className="card alerts">
          <h2>What needs you</h2>
          {needed.map((a, i) => (
            <div className={`alert ${a.level}`} key={i}>
              <div className="atop">
                <span className="tag">{LEVEL[a.level]}</span>
                <b>{a.title}</b>
                <span className="muted">{a.shipment} · {a.customer}</span>
              </div>
              <p>{a.body}</p>
              {a.action === "ask" && (
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
          <h2>How late each trip is running</h2>
          <p className="sub">Minutes against the original plan. Zero means on time.</p>
          <BarChart data={d.delay_chart} unit=" min"
                    accent={d.summary.late ? "var(--hold)" : "var(--ok)"} />
        </section>
      )}

      <section className="trips">
        {d.trips.map((t) => (
          <article className="card trip" key={t.shipment_id}>
            <div className="thead">
              <div className="tid">
                <h3>{t.customer_name}</h3>
                <p className="muted">
                  {t.shipment_id} · {t.origin_city} → {t.facility_name}
                </p>
              </div>
              <span className={`pill p-${t.priority_code.toLowerCase()}`}>{t.priority_code}</span>
            </div>

            <dl className="facts">
              <div><dt>Status</dt><dd>{t.stage}</dd></div>
              <div><dt>Planned arrival</dt><dd>{t.plan_hm || "—"}</dd></div>
              <div>
                <dt>Now expected</dt>
                <dd className={t.delay_min >= 15 ? "warnfg" : ""}>
                  {t.eta_hm || "—"}{t.delay_min ? ` (+${t.delay_min}m)` : ""}
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
              <button className="primary small" onClick={() => ask(t.shipment_id)}>
                Message the dock desk
              </button>
              {!t.appt_id && (
                <button className="ghost small"
                        onClick={() => ask(t.shipment_id, "I am running late. What slots can I get?")}>
                  I am running late
                </button>
              )}
              {t.appt_id && (
                <button className="ghost small"
                        onClick={() => ask(t.shipment_id, "Is my booked slot still valid?")}>
                  Check my slot
                </button>
              )}
            </div>
          </article>
        ))}
      </section>
    </main>
  );
}
