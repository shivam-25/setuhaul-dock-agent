"""Feasibility precision/recall against an INDEPENDENT oracle.

The oracle is written in SQL and derived from the rules, not from capacity.py. If it merely
called the same Python it would measure the code agreeing with itself, which measures nothing.
It also joins dock_status_events directly, because the supplied view does not (audit D05).
"""
import sys, sqlite3, pathlib, json
from datetime import datetime, timedelta
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from setuhaul.capacity import load_ctx, candidates, dt_, ts, CONFIDENCE_BUFFER_MIN

DB = ROOT / "work" / "setuhaul_work.db"

ORACLE = """
WITH me AS (
  SELECT s.shipment_id, s.destination_facility_id AS fac, s.required_dock_type AS need,
         s.load_weight_kg AS kg, s.expected_unload_min AS mins
  FROM shipments s WHERE s.shipment_id = :shp
),
-- A facility with no HEAVY_DOCK_REQUIRED_KG rule has no heavy-dock mandate. Defaulting to
-- Jaipur's 25000 would apply one facility's policy to another; leaving it NULL is worse still,
-- because 7200 <= NULL is NULL and NULL OR false excludes the row, silently rejecting every
-- slot at that facility. The physical limit (F2a) still guards the dock either way.
heavy AS (SELECT COALESCE(
            (SELECT CAST(rule_value AS INT) FROM facility_rules
              WHERE facility_id=(SELECT fac FROM me) AND rule_type='HEAVY_DOCK_REQUIRED_KG'),
            999999999) AS v),
laststart AS (SELECT COALESCE(
            (SELECT rule_value FROM facility_rules
              WHERE facility_id=(SELECT fac FROM me) AND rule_type='LAST_NEW_START_TIME'),
            (SELECT close_time FROM facilities WHERE facility_id=(SELECT fac FROM me))) AS v),
-- Every slot, with the end of the run that covers the unload duration.
-- NOTE: datetime() on a timestamp carrying '+05:30' converts it to UTC and DROPS the offset,
-- producing '2026-08-04 04:10:00'. String-comparing that against '2026-08-04T09:00:00+05:30'
-- is always true, because ' ' sorts before 'T'. So all arithmetic here is done on the naive
-- local part via substr(...,1,19), and comparisons are made in that same form.
chain AS (
  SELECT a.slot_id, a.dock_id, a.slot_start_ts AS st,
         (SELECT MIN(b.slot_end_ts) FROM appointment_slots b
           WHERE b.dock_id = a.dock_id
             AND datetime(substr(b.slot_end_ts,1,19)) >=
                 datetime(substr(a.slot_start_ts,1,19),
                          '+'||(SELECT mins FROM me)||' minutes')
             -- contiguity: no gap between a.start and b.end on this dock
             AND NOT EXISTS (
               SELECT 1 FROM appointment_slots g
               WHERE g.dock_id = a.dock_id
                 AND datetime(substr(g.slot_start_ts,1,19)) >= datetime(substr(a.slot_start_ts,1,19))
                 AND datetime(substr(g.slot_end_ts,1,19))  <= datetime(substr(b.slot_end_ts,1,19))
                 AND NOT EXISTS (SELECT 1 FROM appointment_slots h
                       WHERE h.dock_id=g.dock_id AND h.slot_start_ts = g.slot_end_ts
                         AND datetime(substr(h.slot_start_ts,1,19)) < datetime(substr(b.slot_end_ts,1,19)))
                 AND datetime(substr(g.slot_end_ts,1,19)) < datetime(substr(b.slot_end_ts,1,19))
             )
         ) AS en
  FROM appointment_slots a
  WHERE a.dock_id IN (SELECT dock_id FROM docks WHERE facility_id=(SELECT fac FROM me))
)
SELECT c.slot_id
FROM chain c
JOIN docks d ON d.dock_id = c.dock_id
WHERE c.en IS NOT NULL
  AND d.dock_type = (SELECT need FROM me)                                   -- F1
  AND (SELECT kg FROM me) <= d.max_vehicle_weight_kg                        -- F2a
  AND ((SELECT kg FROM me) <= (SELECT v FROM heavy) OR d.dock_type='HEAVY') -- F2b
  AND NOT EXISTS (SELECT 1 FROM appointment_slots x WHERE x.dock_id=c.dock_id
        AND x.slot_start_ts < c.en AND x.slot_end_ts > c.st AND x.slot_status <> 'OPEN')   -- F3
  AND NOT EXISTS (SELECT 1 FROM appointments ap JOIN appointment_slots y ON y.slot_id=ap.slot_id
        WHERE y.dock_id=c.dock_id AND y.slot_start_ts < c.en AND y.slot_end_ts > c.st
          AND ap.appointment_status IN ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS'))  -- F4
  AND NOT EXISTS (SELECT 1 FROM slot_locks l JOIN appointment_slots z ON z.slot_id=l.slot_id
        WHERE z.dock_id=c.dock_id AND z.slot_start_ts < c.en AND z.slot_end_ts > c.st
          AND l.held_until > :now)                                                          -- F5
  AND c.st >= :ready                                                                        -- F6
  AND c.en <= :close                                                                        -- F8a
  AND substr(c.st,12,5) <= (SELECT v FROM laststart)                                        -- F8b
  AND NOT EXISTS (SELECT 1 FROM dock_status_events e WHERE e.dock_id=c.dock_id
        AND e.event_start_ts < c.en AND e.event_end_ts > c.st)                              -- F9
  AND (:early IS NULL OR c.st >= :early)                                                    -- F10a
  AND (:latest IS NULL OR c.en <= :latest)                                                  -- F10b
ORDER BY c.slot_id
"""

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
shipments = [r[0] for r in con.execute(
    "SELECT shipment_id FROM shipments WHERE current_status NOT IN ('CANCELLED','COMPLETED') "
    "ORDER BY shipment_id")]
CLOCKS = ["2026-08-04T08:05:00+05:30", "2026-08-04T10:05:00+05:30",
          "2026-08-04T13:05:00+05:30", "2026-08-04T16:30:00+05:30"]

tp = fp = fn = 0
rows, mismatches = [], []
for clock in CLOCKS:
    now = dt_(clock)
    for shp in shipments:
        ctx = load_ctx(con, shp, thread_id="EVAL", now=now)
        close = con.execute("SELECT close_time FROM facilities WHERE facility_id=?",
                            (ctx.facility_id,)).fetchone()[0]
        mine = {c.slot_ids[0] for c in candidates(con, ctx, now) if c.ok}
        gold = {r["slot_id"] for r in con.execute(ORACLE, {
            "shp": shp, "now": ts(now), "ready": ts(ctx.ready_at),
            "close": f"{now:%Y-%m-%d}T{close}:00+05:30",
            "early": ts(ctx.earliest_acceptable) if ctx.earliest_acceptable else None,
            "latest": ts(ctx.latest_acceptable) if ctx.latest_acceptable else None})}
        t, f, m = len(mine & gold), len(mine - gold), len(gold - mine)
        tp += t; fp += f; fn += m
        rows.append({"clock": clock[11:16], "shipment": shp, "mine": len(mine),
                     "oracle": len(gold), "tp": t, "fp": f, "fn": m})
        if f or m:
            mismatches.append({"clock": clock[11:16], "shipment": shp,
                               "offered_but_oracle_says_no": sorted(mine - gold)[:4],
                               "oracle_says_yes_but_missed": sorted(gold - mine)[:4]})

prec = tp / (tp + fp) if tp + fp else 1.0
rec  = tp / (tp + fn) if tp + fn else 1.0
print(f"cases: {len(rows)}  ({len(shipments)} shipments x {len(CLOCKS)} clocks)")
print(f"true positives {tp}   false positives {fp}   false negatives {fn}")
print(f"PRECISION {prec:.4f}   (gate 1.0000 - never offer an infeasible slot)")
print(f"RECALL    {rec:.4f}   (gate >= 0.90)")
print(f"{'PASS' if prec >= 1.0 and rec >= 0.90 else 'FAIL'}")
if mismatches:
    print(f"\n{len(mismatches)} disagreements, first 5:")
    for m in mismatches[:5]: print("  ", m)
(ROOT / "eval" / "feasibility_results.json").write_text(json.dumps(
    {"generated": datetime.now().isoformat(timespec="seconds"), "cases": len(rows),
     "tp": tp, "fp": fp, "fn": fn, "precision": round(prec, 4), "recall": round(rec, 4),
     "mismatches": mismatches[:40]}, indent=2))
