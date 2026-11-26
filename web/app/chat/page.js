"use client";
import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { myTrips, send } from "../../lib/api";
import { useSession } from "../../lib/session";

// Quick prompts, not test cases. The old screen listed the evaluation ladder - the
// adversarial case included - which is a developer's harness, not something to put in
// front of a driver. These are the things a driver actually needs to say.
const QUICK = [
  "I am running late. What slots can I get?",
  "Is my booked slot still valid?",
  "Anything later than that?",
  "I have arrived at the gate.",
  "Where should I go?",
];

function ChatInner() {
  const { user } = useSession();
  const params = useSearchParams();
  const router = useRouter();
  const [trips, setTrips] = useState([]);
  const [shipment, setShipment] = useState(params.get("shipment") || "");
  const [text, setText] = useState(params.get("q") || "");
  const [log, setLog] = useState([]);
  const [busy, setBusy] = useState(false);
  const endRef = useRef(null);
  const thread = useRef(`THR-${Math.random().toString(36).slice(2, 9)}`);

  useEffect(() => {
    if (!user) return;
    myTrips().then((d) => {
      setTrips(d.trips);
      if (!shipment && d.trips[0]) setShipment(d.trips[0].shipment_id);
    }).catch(() => {});
  }, [user]);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [log, busy]);

  const trip = trips.find((t) => t.shipment_id === shipment);

  async function submit(msg) {
    const m = (msg ?? text).trim();
    if (!m || busy) return;
    setText(""); setBusy(true);
    setLog((l) => [...l, { who: "me", text: m }]);
    try {
      const r = await send({ message: m, shipment_id: shipment, thread_id: thread.current });
      setLog((l) => [...l, { who: "them", text: r.reply, route: r.route, ms: r.ms,
                             options: r.options, checks: r.checks }]);
    } catch (e) {
      setLog((l) => [...l, { who: "them", text: `Could not send: ${e.message}`, route: "error" }]);
    } finally { setBusy(false); }
  }

  if (!user) return null;

  return (
    <main className="pad chatpage">
      <header className="phead">
        <div>
          <p className="eyebrow">Dock desk</p>
          <h1>Message the dock desk</h1>
        </div>
        <button className="ghost small" onClick={() => router.push("/")}>← My trips</button>
      </header>

      <div className="chatgrid">
        <section className="card chatcol">
          <div className="trippick">
            <label htmlFor="sh">About which trip</label>
            <select id="sh" value={shipment} onChange={(e) => { setShipment(e.target.value); setLog([]); }}>
              {trips.map((t) => (
                <option key={t.shipment_id} value={t.shipment_id}>
                  {t.shipment_id} — {t.customer_name} ({t.origin_city} → {t.facility_name})
                </option>
              ))}
            </select>
          </div>

          <div className="log">
            {log.length === 0 && (
              <p className="empty">
                Tell us what has happened. You do not need to quote your shipment number,
                your dock or your priority — we already have those.
              </p>
            )}
            {log.map((m, i) => (
              <div className={`msg ${m.who}`} key={i}>
                <p>{m.text}</p>
                {m.who === "them" && (
                  <p className="meta">
                    {m.route === "human" ? "passed to a coordinator"
                      : m.route === "ask" ? "needs one more detail" : "answered"}
                    {m.ms ? ` · ${(m.ms / 1000).toFixed(1)}s` : ""}
                  </p>
                )}
              </div>
            ))}
            {busy && <div className="msg them"><p className="typing">Checking the dock board…</p></div>}
            <div ref={endRef} />
          </div>

          <div className="quick">
            {QUICK.map((q) => (
              <button key={q} className="chip" disabled={busy} onClick={() => submit(q)}>{q}</button>
            ))}
          </div>

          <form className="composer" onSubmit={(e) => { e.preventDefault(); submit(); }}>
            <input value={text} onChange={(e) => setText(e.target.value)} disabled={busy}
                   placeholder="Type what has happened…" aria-label="Your message" />
            <button className="primary" disabled={busy || !text.trim()}>Send</button>
          </form>
        </section>

        {trip && (
          <aside className="card sidecol">
            <h2>This trip</h2>
            <dl className="facts stacked">
              <div><dt>Customer</dt><dd>{trip.customer_name}</dd></div>
              <div><dt>Route</dt><dd>{trip.origin_city} → {trip.facility_name}</dd></div>
              <div><dt>Priority</dt><dd>{trip.priority_code}</dd></div>
              <div><dt>Status</dt><dd>{trip.stage}</dd></div>
              <div><dt>Expected</dt><dd>{trip.eta_hm}{trip.delay_min ? ` (+${trip.delay_min}m)` : ""}</dd></div>
              <div><dt>Dock</dt><dd>{trip.dock_code ? `${trip.dock_code} · ${trip.slot_hm}` : "not booked"}</dd></div>
              <div><dt>Load</dt><dd>{trip.pallet_count} pallets · {trip.load_weight_kg} kg</dd></div>
            </dl>
            {trip.alerts.filter((a) => a.level !== "ok").map((a, i) => (
              <div className={`alert ${a.level} tight`} key={i}>
                <b>{a.title}</b><p>{a.body}</p>
              </div>
            ))}
          </aside>
        )}
      </div>
    </main>
  );
}

export default function Chat() {
  return <Suspense fallback={<main className="pad"><p className="empty">Loading…</p></main>}>
    <ChatInner />
  </Suspense>;
}
