"use client";
import { useState } from "react";
import { why } from "../lib/api";

const LEVEL = { critical: "Act now", warn: "Check this", info: "For info", ok: "All good" };
const RANK = { critical: 0, warn: 1, info: 2, ok: 3 };

/* One card per truck, not one per alert.
 *
 * The first version listed every alert as its own card. A truck that is late, lapsed and
 * third in the yard queue produced three cards, and at eighteen trucks the operations
 * page was thirty cards deep with the same shipment id repeated down it. A coordinator
 * reads by truck — "what is wrong with SHP1004" — not by alert, so the truck is the row
 * and its problems are the contents. */
function group(trips) {
  return trips
    .map((t) => {
      const live = (t.alerts || []).filter((a) => a.level !== "ok");
      if (!live.length) return null;
      const worst = live.reduce((w, a) => (RANK[a.level] < RANK[w] ? a.level : w), "ok");
      return { ...t, live, worst };
    })
    .filter(Boolean)
    .sort((a, b) => RANK[a.worst] - RANK[b.worst] || (b.delay_min || 0) - (a.delay_min || 0));
}

/* What a coordinator can actually do about it.
 *
 * The driver's button opens the conversation. A coordinator cannot use that — they would
 * be writing as the driver — so for a long time they got no button at all, which left
 * them reading "needs a new slot" with no way to find one. This asks the same engine the
 * agent asks and shows the answer: every slot this truck can still take, and when there
 * are none, the rule that rejected the most candidates. Read-only: it books nothing. */
function Options({ shipment }) {
  const [s, setS] = useState({ state: "idle" });

  const load = async () => {
    setS({ state: "loading" });
    try { setS({ state: "done", d: await why(shipment) }); }
    catch (e) { setS({ state: "error", msg: e.message }); }
  };

  if (s.state === "idle")
    return <button className="ghost small" onClick={load}>What can it get? →</button>;
  if (s.state === "loading")
    return <p className="muted small">Checking every dock…</p>;
  if (s.state === "error")
    return <p className="muted small">Could not check: {s.msg}</p>;

  const { d } = s;
  const top = {};
  d.rejected.forEach((r) => (r.reasons || []).forEach((x) => (top[x] = (top[x] || 0) + 1)));
  const worst = Object.entries(top).sort((a, b) => b[1] - a[1]).slice(0, 3);

  return (
    <div className="opts">
      <p className="small muted">
        Ready {d.ready_at} · {d.required_dock_type.toLowerCase()} dock ·
        {" "}{d.unload_minutes} min to unload · {d.considered} candidates checked
      </p>
      {d.accepted.length > 0 ? (
        <ul className="optlist">
          {d.accepted.slice(0, 4).map((o) => (
            <li key={o.label}>
              <b>{o.dock}</b> <span className="mono">{o.start}–{o.end}</span>
            </li>
          ))}
        </ul>
      ) : (
        <div className="small">
          <p><b>Nothing fits.</b> Every candidate was rejected:</p>
          <ul className="optlist">
            {worst.map(([r, n]) => <li key={r}><span className="mono">{n}×</span> {r}</li>)}
          </ul>
        </div>
      )}
      <p className="small muted">
        Read-only. Nothing here is held or promised until it is booked and accepted.
      </p>
    </div>
  );
}

export default function AlertList({ trips, ops, onAsk }) {
  const [all, setAll] = useState(false);
  const [lvl, setLvl] = useState("all");

  let rows = group(trips);
  if (lvl !== "all") rows = rows.filter((t) => t.worst === lvl);
  const total = rows.length;
  // Six is about what fits before the page below it stops existing.
  const shown = all ? rows : rows.slice(0, 6);
  if (!total && lvl === "all") return null;

  const counts = group(trips).reduce((c, t) => ({ ...c, [t.worst]: (c[t.worst] || 0) + 1 }), {});

  return (
    <section className="card alerts">
      <div className="listhead plain">
        <h2>{ops ? "What needs someone" : "What needs you"}</h2>
        <div className="toolbar">
          {["all", "critical", "warn", "info"].map((k) => (
            <button key={k} className={`chipbtn ${lvl === k ? "on" : ""}`}
                    onClick={() => { setLvl(k); setAll(false); }}>
              {k === "all" ? `All ${group(trips).length}` : `${LEVEL[k]} ${counts[k] || 0}`}
            </button>
          ))}
        </div>
      </div>

      {total === 0 && <p className="empty">Nothing at this level.</p>}

      {shown.map((t) => (
        <div className={`alert ${t.worst}`} key={t.shipment_id}>
          <div className="atop">
            <span className="tag">{LEVEL[t.worst]}</span>
            <b>{ops ? `${t.driver_name} · ${t.customer_name}` : t.customer_name}</b>
            <span className="muted">
              {t.shipment_id}{t.facility_name ? ` · ${t.facility_name}` : ""}
            </span>
          </div>
          <ul className="reasons">
            {t.live.map((a, i) => (
              <li key={i}>
                <b>{a.title}.</b>{" "}
                {/* The grouped card already names the truck, so the ops phrasing's
                    "Driver · Customer —" prefix would say it a second time. */}
                {ops ? a.body.replace(/^.*? — /, "") : a.body}
              </li>
            ))}
          </ul>
          <div className="arow">
            {ops ? <Options shipment={t.shipment_id} />
                 : t.live.find((a) => a.action === "ask") && (
                     <button className="ghost small"
                             onClick={() => onAsk(t.shipment_id,
                                                  t.live.find((a) => a.action === "ask").ask)}>
                       Ask about this →
                     </button>
                   )}
          </div>
        </div>
      ))}

      {total > shown.length && (
        <button className="ghost small" onClick={() => setAll(true)}>
          Show the other {total - shown.length} →
        </button>
      )}
      {all && total > 6 && (
        <button className="ghost small" onClick={() => setAll(false)}>Show fewer</button>
      )}
    </section>
  );
}
