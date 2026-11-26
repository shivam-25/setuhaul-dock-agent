"""U2 - the capacity lane. No model may enter this file.

Feasibility, ranking, holds and commit. Every rejection carries a reason, because
decision_audit has to be able to answer "why did driver B not get 18:00?" weeks later.
The clock is always a parameter: the snapshot has no 'now' (audit D14).
"""
from __future__ import annotations
import sqlite3, json, uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

POLICY_VERSION = "1.0.0"
HOLD_TTL_SECONDS = 120
CONFIDENCE_BUFFER_MIN = {"HIGH": 0, "MEDIUM": 15, "LOW": 30}
PRIORITY_RANK = {"CRITICAL": 0, "HIGH": 1, "NORMAL": 2, "LOW": 3}
ACTIVE = ("PENDING_CONFIRMATION", "CONFIRMED", "IN_PROGRESS")

def ts(dt: datetime) -> str: return dt.isoformat()
def dt_(s: str) -> datetime: return datetime.fromisoformat(s)

@dataclass
class Ctx:
    shipment_id: str; facility_id: str; required_dock_type: str
    load_weight_kg: int; expected_unload_min: int; priority_code: str
    effective_eta: datetime; confidence: str
    earliest_acceptable: datetime | None = None
    latest_acceptable: datetime | None = None
    has_commitment: bool = False
    requested_at: datetime | None = None
    thread_id: str | None = None
    @property
    def ready_at(self) -> datetime:
        return self.effective_eta + timedelta(minutes=CONFIDENCE_BUFFER_MIN.get(self.confidence, 15))

@dataclass
class Candidate:
    slot_ids: list[str]; dock_id: str; dock_code: str; dock_type: str
    start: datetime; end: datetime
    failures: list[str] = field(default_factory=list)
    @property
    def ok(self) -> bool: return not self.failures
    @property
    def label(self) -> str:
        return f"{self.dock_code} {self.start:%H:%M}-{self.end:%H:%M}"

def load_ctx(con, shipment_id, *, thread_id=None, now=None) -> Ctx:
    """One query, not three.

    This ran three statements - the shipment, its latest exception, and whether it already
    holds a commitment - and is itself called several times per turn, once for the driver
    and once per outranking contender. Against a database a region away that was six or
    nine avoidable round trips at roughly 160 ms each. Correlated subqueries return the
    same three answers in one.
    """
    r = con.execute("""
        SELECT s.shipment_id, s.destination_facility_id, s.required_dock_type,
               s.load_weight_kg, s.expected_unload_min, s.priority_code,
               COALESCE(v.effective_eta_ts, s.original_eta_ts) AS eta,
               COALESCE(v.eta_confidence, 'HIGH')              AS conf,
               (SELECT e.earliest_acceptable_ts FROM driver_exceptions e
                 WHERE e.shipment_id = s.shipment_id AND e.exception_status <> 'DUPLICATE'
                 ORDER BY e.reported_at DESC LIMIT 1)          AS earliest,
               (SELECT e.latest_acceptable_ts FROM driver_exceptions e
                 WHERE e.shipment_id = s.shipment_id AND e.exception_status <> 'DUPLICATE'
                 ORDER BY e.reported_at DESC LIMIT 1)          AS latest,
               (SELECT e.declared_eta_ts FROM driver_exceptions e
                 WHERE e.shipment_id = s.shipment_id AND e.exception_status <> 'DUPLICATE'
                 ORDER BY e.reported_at DESC LIMIT 1)          AS declared,
               (SELECT COUNT(*) FROM appointments a
                 WHERE a.shipment_id = s.shipment_id AND a.is_current = 1
                   AND a.appointment_status IN ('PENDING_CONFIRMATION','CONFIRMED'))
                                                               AS commitment_n
        FROM shipments s
        LEFT JOIN v_latest_eta v ON v.shipment_id = s.shipment_id
        WHERE s.shipment_id = ?""", (shipment_id,)).fetchone()
    if not r: raise ValueError(f"unknown shipment {shipment_id}")
    eta = dt_(r[10]) if r[10] else dt_(r[6])
    return Ctx(r[0], r[1], r[2], r[3], r[4], r[5], eta, r[7],
               dt_(r[8]) if r[8] else None,
               dt_(r[9]) if r[9] else None,
               bool(r[11]), now, thread_id)

def _rules(con, facility_id) -> dict:
    return {k: v for k, v in con.execute(
        "SELECT rule_type,rule_value FROM facility_rules WHERE facility_id=?", (facility_id,))}

def candidates(con, ctx: Ctx, now: datetime, *, include_failures=True) -> list[Candidate]:
    """Every slot chain on every dock at the facility, each with its rejection reasons.
    F9 reads dock_status_events DIRECTLY, never slot_status - see audit D05."""
    # Within one turn, candidates() runs at least twice - once for this driver and once
    # per outranking contender inside the priority gate - and every facility-wide read it
    # makes is identical between those calls. Only ctx differs. Memoising the facility
    # snapshot on the connection for this instant removes the repeats; across a network
    # database each one cost a round trip.
    #
    # Locks are cached WITH their holding thread and filtered per ctx below, because
    # "held by someone else" depends on who is asking and must not be baked into the cache.
    # sqlite3.Connection has no __dict__, so the cache cannot attach there - and it does
    # not need to. SQLite is in-process and a repeat read costs microseconds; the cache
    # exists for the network case. On SQLite this falls back to a per-call dict, which
    # simply never hits, and the behaviour is identical either way.
    _key = ("_fac_snap", ctx.facility_id, ts(now))
    snap = getattr(con, "_setuhaul_snap", None)
    if not snap or snap.get("key") != _key:
        snap = {"key": _key}
        try:
            con._setuhaul_snap = snap
        except AttributeError:
            pass
    rules = snap.get("rules")
    if rules is None:
        rules = snap["rules"] = _rules(con, ctx.facility_id)
    # No rule at this facility means no heavy-dock mandate here, not Jaipur's 25000.
    heavy_kg = int(rules["HEAVY_DOCK_REQUIRED_KG"]) if "HEAVY_DOCK_REQUIRED_KG" in rules \
               else 10**9
    fac = snap.get("fac")
    if fac is None:
        fac = snap["fac"] = con.execute(
            "SELECT open_time,close_time FROM facilities WHERE facility_id=?",
            (ctx.facility_id,)).fetchone()
    # If this facility sets no last-start rule, the limit is its own closing time. Falling back to
    # a constant would apply one facility's rule (Jaipur's 21:00) to another. Rules are per-facility.
    last_start = rules.get("LAST_NEW_START_TIME") or fac[1]
    docks = snap.get("docks")
    if docks is None:
        docks = snap["docks"] = con.execute(
            "SELECT dock_id,dock_code,dock_type,max_vehicle_weight_kg FROM docks "
            "WHERE facility_id=?", (ctx.facility_id,)).fetchall()

    # Prefetch, once per facility, what used to be three queries per slot chain.
    #
    # On SQLite those 237 extra round trips were in-process and invisible. Against a
    # network database they cost 41 ms each and turned one feasibility pass into 10
    # seconds, well past the 5-second gate. The N+1 was always there; only the move to
    # Postgres made it measurable.
    #
    # This does not weaken the concurrency guarantee. candidates() was only ever a read,
    # and a read is stale the instant it returns whether it ran once or 246 times. The
    # authoritative arbiter is unchanged: claim() takes the lock atomically and the
    # partial unique index refuses a second active appointment on a slot. Reading in bulk
    # changes how fresh the OFFER is, never whether the COMMIT can double-book.
    taken_ids = snap.get("taken")
    if taken_ids is None:
      taken_rows = con.execute(
        f"SELECT a.slot_id FROM appointments a "
        f"JOIN appointment_slots sl ON sl.slot_id = a.slot_id "
        f"JOIN docks dk ON dk.dock_id = sl.dock_id "
        f"WHERE dk.facility_id = ? AND a.appointment_status IN {ACTIVE}",
        (ctx.facility_id,)).fetchall()
      taken_ids = snap["taken"] = {r[0] for r in taken_rows}

    locks = snap.get("locks")
    if locks is None:
        locks = snap["locks"] = [
            (r[0], r[1]) for r in con.execute(
                "SELECT l.slot_id, l.thread_id FROM slot_locks l "
                "JOIN appointment_slots sl ON sl.slot_id = l.slot_id "
                "JOIN docks dk ON dk.dock_id = sl.dock_id "
                "WHERE dk.facility_id = ? AND l.held_until > ?",
                (ctx.facility_id, ts(now))).fetchall()]
    # "Held by another conversation" depends on who is asking, so filter here, not in SQL.
    locked_ids = {sid for sid, holder in locks if holder != ctx.thread_id}

    outages = snap.get("outages")
    if outages is None:
      outage_rows = con.execute(
        "SELECT e.dock_id, e.dock_event_id, e.event_type, e.event_start_ts, e.event_end_ts "
        "FROM dock_status_events e JOIN docks dk ON dk.dock_id = e.dock_id "
        "WHERE dk.facility_id = ?", (ctx.facility_id,)).fetchall()
      outages = snap["outages"] = {}
      for r in outage_rows:
        outages.setdefault(r[0], []).append((r[1], r[2], r[3], r[4]))

    # One slots query for the facility rather than one per dock. Six docks meant six round
    # trips at about 160 ms each, twice per turn once the priority gate also calls this.
    all_slots = snap.get("slots")
    if all_slots is None:
        all_slots = snap["slots"] = {}
        for r in con.execute(
                "SELECT sl.dock_id, sl.slot_id, sl.slot_start_ts, sl.slot_end_ts, sl.slot_status "
                "FROM appointment_slots sl JOIN docks dk ON dk.dock_id = sl.dock_id "
                "WHERE dk.facility_id = ? ORDER BY sl.dock_id, sl.slot_start_ts",
                (ctx.facility_id,)).fetchall():
            all_slots.setdefault(r[0], []).append((r[1], r[2], r[3], r[4]))

    out: list[Candidate] = []
    for dock_id, dock_code, dock_type, dock_max_kg in docks:
        slots = all_slots.get(dock_id, [])
        for i, (sid, s_start, s_end, s_status) in enumerate(slots):
            start = dt_(s_start)
            # F7: walk forward over CONTIGUOUS slots until the unload duration is covered (DEFECT-1)
            chain, end = [sid], dt_(s_end)
            j = i
            while (end - start).total_seconds() / 60 < ctx.expected_unload_min and j + 1 < len(slots):
                nxt = slots[j + 1]
                if dt_(nxt[1]) != end: break          # a gap: chain is not contiguous
                chain.append(nxt[0]); end = dt_(nxt[2]); j += 1
            c = Candidate(chain, dock_id, dock_code, dock_type, start, end)
            f = c.failures
            if dock_type != ctx.required_dock_type:
                f.append(f"F1 dock is {dock_type}, load needs {ctx.required_dock_type}")
            if ctx.load_weight_kg > dock_max_kg:
                f.append(f"F2a load {ctx.load_weight_kg}kg over dock limit {dock_max_kg}kg")
            if ctx.load_weight_kg > heavy_kg and dock_type != "HEAVY":
                f.append(f"F2b load over {heavy_kg}kg must use HEAVY dock (RULE004)")
            blocked = [s[0] for s in slots[i:j+1] if s[3] != "OPEN"]
            if blocked: f.append(f"F3 slot marked {slots[i][3]}")
            taken = [sl for sl in chain if sl in taken_ids]
            if taken: f.append(f"F4 already booked: {','.join(taken)}")
            locked = [sl for sl in chain if sl in locked_ids]
            if locked: f.append(f"F5 held by another conversation: {','.join(locked)}")
            if start < ctx.ready_at:
                f.append(f"F6 starts {start:%H:%M}, before ready time {ctx.ready_at:%H:%M} "
                         f"(ETA {ctx.effective_eta:%H:%M} + {CONFIDENCE_BUFFER_MIN[ctx.confidence]}min "
                         f"{ctx.confidence}-confidence buffer)")
            if (end - start).total_seconds() / 60 < ctx.expected_unload_min:
                f.append(f"F7 only {int((end-start).total_seconds()//60)}min contiguous, "
                         f"needs {ctx.expected_unload_min}min")
            if f"{end:%H:%M}" > fac[1] and end.strftime("%H:%M") != "00:00":
                f.append(f"F8a finishes {end:%H:%M}, facility closes {fac[1]}")
            if f"{start:%H:%M}" > last_start:
                f.append(f"F8b starts after {last_start}, needs manual approval (RULE005)")
            outage = [o for o in outages.get(dock_id, ())
                      if o[2] < ts(end) and o[3] > ts(start)]
            if outage:
                o = outage[0]
                f.append(f"F9 dock {o[1]} {dt_(o[2]):%H:%M}-{dt_(o[3]):%H:%M} ({o[0]})")
            if ctx.earliest_acceptable and start < ctx.earliest_acceptable:
                f.append(f"F10a driver asked for nothing before {ctx.earliest_acceptable:%H:%M}")
            if ctx.latest_acceptable and end > ctx.latest_acceptable:
                f.append(f"F10b driver must finish by {ctx.latest_acceptable:%H:%M}")
            if c.ok or include_failures: out.append(c)
    return out

def feasible(con, ctx, now): return [c for c in candidates(con, ctx, now) if c.ok]

def rank_options(cands: list[Candidate]) -> list[Candidate]:
    """Within one request: least waiting first, then a stable dock order."""
    return sorted(cands, key=lambda c: (c.start, c.dock_code))

def claim(con, slot_ids, thread_id, shipment_id, now, ttl=HOLD_TTL_SECONDS) -> bool:
    """Atomic, expiry-aware, no reaper. 0 rows changed == lost the race.
    Identical statement works in Postgres; `now` is a bound parameter, never an index predicate."""
    until = ts(now + timedelta(seconds=ttl))
    for sid in slot_ids:
        cur = con.execute("""
            INSERT INTO slot_locks(slot_id,thread_id,shipment_id,held_until,acquired_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(slot_id) DO UPDATE SET
                thread_id=excluded.thread_id, shipment_id=excluded.shipment_id,
                held_until=excluded.held_until, acquired_at=excluded.acquired_at
            WHERE slot_locks.held_until <= ?""",
            (sid, thread_id, shipment_id, until, ts(now), ts(now)))
        if cur.rowcount == 0:
            return False
    return True

class LostRace(Exception): ...

def commit_booking(con, ctx: Ctx, cand: Candidate, now: datetime) -> tuple[str, bool]:
    """One short transaction. The INSERT is itself conditional on still holding the lock,
    so a hold cannot expire between revalidation and insert. The partial unique index
    ux_active_appointment_per_slot is the final arbiter."""
    # IDEMPOTENCY. A duplicate request (THR009 repeats THR001) must not book twice. If this
    # shipment already holds this exact slot, return the existing appointment unchanged.
    existing = con.execute(
        "SELECT appointment_id FROM appointments WHERE shipment_id=? AND slot_id=? AND is_current=1 "
        f"AND appointment_status IN {ACTIVE}", (ctx.shipment_id, cand.slot_ids[0])).fetchone()
    if existing:
        return existing[0], False          # replayed, not created

    appt = f"APT-{uuid.uuid4().hex[:10].upper()}"
    try:
        con.execute("BEGIN IMMEDIATE")
        # Supersede the appointment this rebooking replaces. ux_current_active_appointment_per_shipment
        # allows only ONE current active appointment per shipment, so the old one must step down in the
        # SAME transaction. It is kept as history (is_current=0), never deleted - the brief requires
        # that a missed appointment stays visible.
        prev = con.execute(
            "SELECT appointment_id FROM appointments WHERE shipment_id=? AND is_current=1 "
            f"AND appointment_status IN {ACTIVE}", (ctx.shipment_id,)).fetchone()
        if prev:
            con.execute(
                "UPDATE appointments SET is_current=0, appointment_status='CANCELLED', "
                "cancelled_at=?, cancellation_reason=?, updated_at=? WHERE appointment_id=?",
                (ts(now), 'REPLACED_BY_REBOOKING', ts(now), prev[0]))
        for n, sid in enumerate(cand.slot_ids):
            # primary row is_current=1; overflow rows is_current=0 so the per-shipment index
            # stays satisfied while the per-slot index still protects the overflow slot.
            cur = con.execute("""
                INSERT INTO appointments(appointment_id,shipment_id,slot_id,appointment_status,
                                         booking_source,is_current,booked_at,replaced_appointment_id,
                                         updated_at)
                SELECT ?,?,?,'PENDING_CONFIRMATION','DRIVER_CHAT',?,?,?,?
                WHERE EXISTS (SELECT 1 FROM slot_locks
                              WHERE slot_id=? AND thread_id=? AND held_until > ?)""",
                (f"{appt}" if n == 0 else f"{appt}-O{n}", ctx.shipment_id, sid,
                 1 if n == 0 else 0, ts(now),
                 (prev[0] if prev else None) if n == 0 else appt, ts(now),
                 sid, ctx.thread_id, ts(now)))
            if cur.rowcount == 0:
                raise LostRace(f"hold on {sid} was not live at commit")
        con.execute("DELETE FROM slot_locks WHERE slot_id IN (%s) AND thread_id=?"
                    % ",".join("?" * len(cand.slot_ids)), cand.slot_ids + [ctx.thread_id])
        con.commit()
        return appt, True
    except sqlite3.IntegrityError as e:
        con.rollback()
        # ONLY a uniqueness collision on the slot means somebody else won the race.
        # Any other integrity error is OUR bug and must not masquerade as contention.
        msg = str(e).lower()
        if "unique" in msg and "slot_id" in msg:
            raise LostRace(f"slot taken by another booking: {e}")
        raise
    except Exception:
        con.rollback(); raise

def audit(con, *, request_id, box, decision, thread_id=None, shipment_id=None,
          actor="agent", detail=None, latency_ms=None, now=None):
    con.execute("""INSERT INTO decision_audit(ts,request_id,thread_id,shipment_id,actor,box,
                   decision,detail_json,policy_version,latency_ms) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (ts(now or datetime.now()), request_id, thread_id, shipment_id, actor, box,
                 decision, json.dumps(detail) if detail else None, POLICY_VERSION, latency_ms))
    con.commit()

# ---------------------------------------------------------------- the warehouse side
def pending_acceptances(con) -> list[dict]:
    """Every booking the agent has made that the warehouse has not yet answered.

    A chain writes one primary row (is_current=1) plus APT-x-O1.. overflow rows, so group
    by the primary and span the whole chain - otherwise a 2-slot booking shows as one hour.
    """
    rows = con.execute("""
        SELECT a.appointment_id, a.shipment_id, s.driver_id, d.dock_code, f.facility_id,
               MIN(sl.slot_start_ts) AS starts, MAX(sl.slot_end_ts) AS ends, a.booked_at,
               s.priority_code, s.required_dock_type, s.expected_unload_min
        FROM appointments a
        JOIN appointment_slots sl ON sl.slot_id = a.slot_id
        JOIN docks d       ON d.dock_id = sl.dock_id
        JOIN facilities f  ON f.facility_id = d.facility_id
        JOIN shipments s   ON s.shipment_id = a.shipment_id
        WHERE a.appointment_status = 'PENDING_CONFIRMATION' AND a.is_current = 1
        -- Every non-aggregated column is named. SQLite accepts a bare GROUP BY and picks
        -- an arbitrary row from each group; Postgres rejects it outright. The SQLite
        -- behaviour was never safe - it just failed quietly instead of loudly.
        GROUP BY a.appointment_id, a.shipment_id, s.driver_id, d.dock_code, f.facility_id,
                 a.booked_at, s.priority_code, s.required_dock_type, s.expected_unload_min
        ORDER BY starts""").fetchall()
    return [dict(r) if not isinstance(r, tuple) else r for r in rows]


def decide_appointment(con, appointment_id: str, *, accept: bool, actor: str,
                       reason: str | None, now: datetime) -> dict:
    """The warehouse accepts or rejects a booking the agent made.

    Only PENDING_CONFIRMATION may move, and the UPDATE is itself conditional on that status,
    so two coordinators clicking at once cannot both win - the second changes 0 rows and is
    told the booking was already decided. Overflow rows of a chain move with the primary.
    """
    row = con.execute(
        "SELECT appointment_id, shipment_id, appointment_status FROM appointments "
        "WHERE appointment_id=?", (appointment_id,)).fetchone()
    if not row:
        return {"ok": False, "error": "no such appointment", "appointment_id": appointment_id}
    status_now = row[2] if isinstance(row, tuple) else row["appointment_status"]
    shipment_id = row[1] if isinstance(row, tuple) else row["shipment_id"]
    if status_now != "PENDING_CONFIRMATION":
        # Idempotent: saying yes twice is not an error, it is the same outcome.
        return {"ok": False, "already": status_now, "appointment_id": appointment_id,
                "shipment_id": shipment_id,
                "error": f"already {status_now}; only a pending booking can be decided"}

    new = "CONFIRMED" if accept else "CANCELLED"
    ref = f"WH-{uuid.uuid4().hex[:8].upper()}" if accept else None
    ids = (appointment_id, appointment_id + "-O%")
    try:
        con.execute("BEGIN IMMEDIATE")
        cur = con.execute(
            "UPDATE appointments SET appointment_status=?, confirmed_at=?, cancelled_at=?, "
            "cancellation_reason=?, warehouse_confirmation_ref=?, is_current=?, updated_at=? "
            "WHERE (appointment_id=? OR appointment_id LIKE ?) "
            "  AND appointment_status='PENDING_CONFIRMATION'",
            (new, ts(now) if accept else None, None if accept else ts(now),
             None if accept else (reason or "WAREHOUSE_REJECTED"), ref,
             1 if accept else 0, ts(now), *ids))
        if cur.rowcount == 0:
            con.rollback()
            return {"ok": False, "error": "status changed under us; nothing updated",
                    "appointment_id": appointment_id}
        if not accept:
            # A rejected slot must become bookable again immediately, or the rejection
            # silently destroys capacity for the rest of the day.
            con.execute("DELETE FROM slot_locks WHERE slot_id IN "
                        "(SELECT slot_id FROM appointments WHERE appointment_id=? "
                        " OR appointment_id LIKE ?)", ids)
        con.commit()
    except Exception:
        con.rollback()
        raise
    return {"ok": True, "appointment_id": appointment_id, "shipment_id": shipment_id,
            "status": new, "warehouse_ref": ref, "rows": cur.rowcount}

# ---------------------------------------------------------------- operations alerts
# The brief's Human-operations layer must "handle safety, disputed data, no-slot cases,
# penalties and exceptional commitments" and must not "leave the loop on high-risk
# decisions". Those decisions were already written to decision_audit; nothing surfaced
# them, so a coordinator had to read the raw log to find out a driver was stuck.
# Derived from the append-only audit rather than a second table, so the alert list cannot
# drift from what actually happened.
ALERT_KINDS = {
    "escalated":            ("no slot exists",        "high"),
    "contradictory_window": ("driver limits clash",   "medium"),
    "refused_injection":    ("message refused",       "medium"),
    "ambiguous_shipment":   ("which trip is unclear", "low"),
    "asked_intent":         ("intent unclear",        "low"),
}


def open_alerts(con, limit: int = 40) -> list[dict]:
    rows = con.execute(
        "SELECT audit_id, ts, decision, shipment_id, thread_id, detail_json, actor "
        "FROM decision_audit "
        "WHERE decision IN ('escalated','contradictory_window','refused_injection',"
        "                   'ambiguous_shipment','asked_intent') "
        "ORDER BY audit_id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r) if not isinstance(r, tuple) else dict(zip(
            ("audit_id","ts","decision","shipment_id","thread_id","detail_json","actor"), r))
        label, severity = ALERT_KINDS.get(d["decision"], (d["decision"], "low"))
        detail = {}
        try:
            detail = json.loads(d["detail_json"]) if d["detail_json"] else {}
        except Exception:
            detail = {}
        # An escalation whose own hand-off route is broken is the highest-risk case there is:
        # nobody is actually going to be told. Promote it.
        blocked = bool(detail.get("route_blocker") or detail.get("blocker"))
        out.append({"audit_id": d["audit_id"], "at": d["ts"], "kind": d["decision"],
                    "label": label, "severity": "critical" if blocked else severity,
                    "shipment_id": d["shipment_id"], "thread_id": d["thread_id"],
                    "reason": (detail.get("reason") or detail.get("why")
                               or (detail.get("rejections") or [{}])[0].get("rule")
                               if isinstance(detail.get("rejections"), list) else None),
                    "route_blocker": detail.get("route_blocker") or detail.get("blocker")})
    return out


def operations_metrics(con) -> dict:
    """The measures the brief says the submission is judged on, computed from the audit
    rather than asserted in prose.

    One query, not fourteen. The service runs in ap-southeast-2 and the database in
    ap-south-1 - an organisation policy forbids Lambda in ap-south-1, so they cannot be
    co-located and every round trip costs about 160 ms. Fourteen separate COUNT statements
    made this endpoint take 2.8 seconds while doing almost no work. Scalar subqueries in a
    single SELECT return the same numbers in one trip.
    """
    row = con.execute("""
        SELECT
          (SELECT COUNT(*) FROM request_log WHERE route='auto')                AS auto_n,
          (SELECT COUNT(*) FROM request_log WHERE route='human')               AS human_n,
          (SELECT COUNT(*) FROM decision_audit WHERE decision IN
             ('ambiguous_shipment','asked_intent','contradictory_window'))     AS asked_n,
          (SELECT COUNT(*) FROM appointment_slots WHERE slot_status='OPEN')    AS slots_open,
          (SELECT COUNT(DISTINCT slot_id) FROM appointments WHERE
             appointment_status IN ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS'))
                                                                               AS used_n,
          (SELECT COUNT(*) FROM decision_audit WHERE decision='duplicate_replayed')
                                                                               AS dupes,
          (SELECT COUNT(*) FROM decision_audit WHERE decision='escalated')     AS escal,
          (SELECT COUNT(*) FROM (SELECT slot_id FROM appointments WHERE appointment_status
             IN ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS')
             GROUP BY slot_id HAVING COUNT(*) > 1) x)                          AS conflicts
    """).fetchone()
    d = dict(row)
    auto, human, asked = d["auto_n"], d["human_n"], d["asked_n"]
    replies = auto + human + asked
    slots, used = d["slots_open"], d["used_n"]
    out = {"turns": replies,
           "resolved_without_human": auto,
           "clarification_turns": asked,
           "handed_to_human": human,
           "automation_share": round(auto / replies, 3) if replies else None,
           "clarifications_per_turn": round(asked / replies, 3) if replies else None,
           "slots_open": slots, "slots_allocated": used,
           "slot_utilisation": round(used / slots, 3) if slots else None,
           "duplicates_replayed": d["dupes"],
           "escalations": d["escal"],
           "conflicting_allocations": d["conflicts"]}
    out.update({f"wait_{k}": v for k, v in waiting_times(con).items() if k != "still_waiting"})
    return out


# ---------------------------------------------------------------- D7: scarce capacity
# "How do you order simultaneous requests when capacity runs short?"
#
# Requests do not actually arrive simultaneously - they arrive one at a time, seconds
# apart, which is why rank_requests (a batch sort) never had a caller. Ordering a list
# is the wrong shape for an online service. The real question is narrower and harder:
# when THIS driver asks, may they take this slot, given who else is waiting?
#
# The policy, stated so it can be checked and argued with:
#
#   A lower-priority truck may not take the LAST slot that a higher-priority truck
#   could use, WHEN the lower-priority truck still has an alternative of its own.
#
# Both halves matter. Without the first, a CRITICAL reefer loses its only bay to a LOW
# load that had four other choices. Without the second, we starve whoever is actually in
# front of us to protect a truck that may never ask - so if this is also their last
# option, they keep it and first-come-first-served stands.
#
# A held booking is never taken back. Re-allocation after a promise is a different
# decision with a human in it (see Boundaries: "whether a booking committed in the
# system of record").

def contenders(con, ctx: Ctx, now: datetime, *, outranking_only: bool = False) -> list[Ctx]:
    """Other shipments genuinely competing for this facility right now: in an open
    exception, not yet served, arriving in a comparable window. Not every shipment in the
    table - a truck that arrived yesterday is not contending for tonight.

    outranking_only filters by priority in SQL. The gate only ever looks at trucks that
    outrank this one, and each Ctx costs three queries to build, so loading the rest was
    pure latency.
    """
    better = [k for k, v in PRIORITY_RANK.items()
              if v < PRIORITY_RANK.get(ctx.priority_code, 9)]
    extra, params = "", [ctx.facility_id, ctx.shipment_id]
    if outranking_only:
        if not better:
            return []
        extra = f" AND s.priority_code IN ({','.join('?' * len(better))})"
        params += better
    rows = con.execute(f"""
        SELECT DISTINCT s.shipment_id
        FROM shipments s
        JOIN driver_exceptions e ON e.shipment_id = s.shipment_id
                                AND e.exception_status NOT IN ('DUPLICATE','RESOLVED','CLOSED')
        WHERE s.destination_facility_id = ?
          AND s.shipment_id <> ?
          AND NOT EXISTS (SELECT 1 FROM appointments a
                          WHERE a.shipment_id = s.shipment_id AND a.is_current = 1
                            AND a.appointment_status IN ('CONFIRMED','IN_PROGRESS'))
        {extra}
        """, params).fetchall()
    out = []
    for r in rows:
        sid = r[0] if not isinstance(r, dict) else r["shipment_id"]
        try:
            out.append(load_ctx(con, sid, now=now))
        except Exception:
            continue
    return out


def priority_gate(con, ctx: Ctx, ranked: list[Candidate], now: datetime) -> tuple[list[Candidate], list[dict]]:
    """Filter the offer list under the scarcity policy above.

    Returns (allowed, withheld) where each withheld entry carries the reason and the
    higher-priority shipment it was protected for, so decision_audit can answer
    "why did driver B not get 18:00?" and so priority-policy violations are countable.
    """
    higher = contenders(con, ctx, now, outranking_only=True)
    if not higher or len(ranked) <= 1:
        # Nobody outranks this driver here, or this is their only option. Either way the
        # policy has nothing to say and first-come-first-served stands.
        return ranked, []

    # What each higher-priority contender could still use.
    reach: dict[str, set[str]] = {}
    for h in higher:
        try:
            reach[h.shipment_id] = {c.slot_ids[0] for c in candidates(con, h, now) if c.ok}
        except Exception:
            continue

    allowed, withheld = [], []
    for cand in ranked:
        sole_for = [sid for sid, slots in reach.items()
                    if slots and slots == {cand.slot_ids[0]}]
        if sole_for:
            withheld.append({"slot_id": cand.slot_ids[0], "label": cand.label,
                             "reason": "last compatible slot for a higher-priority load",
                             "protected_for": sole_for,
                             "their_priority": next(h.priority_code for h in higher
                                                    if h.shipment_id == sole_for[0]),
                             "my_priority": ctx.priority_code})
        else:
            allowed.append(cand)
    if not allowed:                      # never starve the driver in front of us
        return ranked, []
    return allowed, withheld

# ---------------------------------------------------------------- arrival is not an ETA
# The brief lists gate check-in as one of the five parties, and the scheduling model reads
# gate-in as "evidence that the truck is available now". Nothing in this service read it.
#
# That is not cosmetic. An ETA is an estimate and earns a confidence buffer - up to 30
# minutes for a LOW-confidence driver declaration. A truck standing in the yard is not an
# estimate. Buffering it pushes a truck that is physically present past slots it could
# take, and asking it "when will you arrive?" wastes the driver's time on a fact the
# backend already holds.

ARRIVED_WAITING = ("WAITING_EARLY", "WAITING_LATE", "WAITING_DOCK_UNAVAILABLE")


def arrival(con, shipment_id: str) -> dict | None:
    """What gate check-in knows about this truck, or None if it has not arrived."""
    r = con.execute(
        "SELECT gate_in_ts, yard_queue_enter_ts, dock_in_ts, unload_start_ts, unload_end_ts,"
        "       gate_out_ts, arrival_state, queue_state, queue_position, "
        "       (SELECT dock_code FROM docks d WHERE d.dock_id = f.actual_dock_id) AS dock_code "
        "FROM facility_checkins f WHERE shipment_id=? ORDER BY gate_in_ts DESC LIMIT 1",
        (shipment_id,)).fetchone()
    if not r:
        return None
    cols = ("gate_in_ts","yard_queue_enter_ts","dock_in_ts","unload_start_ts","unload_end_ts",
            "gate_out_ts","arrival_state","queue_state","queue_position","dock_code")
    d = dict(r) if isinstance(r, dict) else dict(zip(cols, r))
    d["has_arrived"]  = bool(d.get("gate_in_ts"))
    d["on_dock"]      = d.get("queue_state") == "IN_DOCK" or bool(d.get("dock_in_ts"))
    d["finished"]     = d.get("queue_state") == "COMPLETED" or bool(d.get("gate_out_ts"))
    d["still_waiting"] = d.get("queue_state") in ARRIVED_WAITING
    return d


def apply_arrival(con, ctx: Ctx) -> tuple[Ctx, str | None]:
    """Replace a predicted ETA with the observed one when the truck is already here.

    Returns (ctx, note). The note is for the audit trail and the box log, so a reader can
    see why the buffer disappeared.
    """
    a = arrival(con, ctx.shipment_id)
    if not a or not a["has_arrived"] or a["finished"]:
        return ctx, None
    if a["on_dock"]:
        # Already unloading. This is a fixed task, not a scheduling candidate.
        # The dock CODE (D2), never the internal id (DOCK-JAI-D2): handed the id, the model
        # rendered it as "dock 12".
        return ctx, f"already on dock {a.get('dock_code') or '?'} - unloading, not rebookable"
    gate_in = dt_(a["gate_in_ts"])
    ctx.effective_eta = gate_in
    ctx.confidence = "HIGH"        # observed, so the buffer is zero - see CONFIDENCE_BUFFER_MIN
    return ctx, (f"gated in at {gate_in:%H:%M} ({a.get('queue_state')}) - arrival observed, "
                 f"so no confidence buffer is applied")


def waiting_times(con) -> dict:
    """The brief's measure: average driver waiting after rescheduling, gate-in to dock-in."""
    rows = con.execute("SELECT shipment_id, gate_in_ts, dock_in_ts, queue_state "
                       "FROM facility_checkins WHERE gate_in_ts IS NOT NULL").fetchall()
    waits, still = [], []
    for r in rows:
        d = dict(r) if isinstance(r, dict) else dict(zip(
            ("shipment_id","gate_in_ts","dock_in_ts","queue_state"), r))
        if d["dock_in_ts"]:
            waits.append((dt_(d["dock_in_ts"]) - dt_(d["gate_in_ts"])).total_seconds() / 60)
        else:
            still.append(d["shipment_id"])
    return {"docked": len(waits),
            "avg_gate_to_dock_min": round(sum(waits) / len(waits), 1) if waits else None,
            "max_gate_to_dock_min": round(max(waits), 1) if waits else None,
            "still_waiting": still,
            "still_waiting_count": len(still)}


# ---------------------------------------------------------------- a promise can go stale
# Brief level 5, "the world changes mid-conversation" (SHP1005, APT1008), and the measure
# "options later found infeasible - re-check each shown option at confirmation".
#
# SHP1005 holds a CONFIRMED appointment on D3 for 09:00-10:00. At 10:05 that window has
# closed and the truck is still in the yard as WAITING_DOCK_UNAVAILABLE. Reporting it as
# "confirmed" is literally true and operationally false: the slot is gone. A commitment
# has to be checked against the clock before it is repeated to a driver.

def commitment_state(con, shipment_id: str, now: datetime) -> dict | None:
    r = con.execute(
        "SELECT a.appointment_id, a.appointment_status, d.dock_code, "
        "       s.slot_start_ts, s.slot_end_ts "
        "FROM appointments a "
        "JOIN appointment_slots s ON s.slot_id = a.slot_id "
        "JOIN docks d ON d.dock_id = s.dock_id "
        f"WHERE a.shipment_id=? AND a.is_current=1 AND a.appointment_status IN {ACTIVE} "
        "ORDER BY s.slot_start_ts LIMIT 1", (shipment_id,)).fetchone()
    if not r:
        return None
    cols = ("appointment_id","appointment_status","dock_code","slot_start_ts","slot_end_ts")
    d = dict(r) if isinstance(r, dict) else dict(zip(cols, r))
    start, end = dt_(d["slot_start_ts"]), dt_(d["slot_end_ts"])
    # The agent carries a naive clock (IST already applied); slot rows carry +05:30. Compare
    # like with like rather than letting the mismatch surface as a TypeError mid-conversation.
    if now.tzinfo is None:
        start, end = start.replace(tzinfo=None), end.replace(tzinfo=None)
    elif start.tzinfo is None:
        start, end = start.replace(tzinfo=now.tzinfo), end.replace(tzinfo=now.tzinfo)
    arr = arrival(con, shipment_id) or {}
    if arr.get("on_dock") or arr.get("finished"):
        phase = "being_served"
    elif now >= end:
        phase = "lapsed"           # the window closed and the truck never got the dock
    elif now >= start:
        phase = "in_window"        # due now
    else:
        phase = "upcoming"
    d.update(start=start, end=end, phase=phase,
             minutes_past_end=round((now - end).total_seconds() / 60) if now > end else 0)
    return d
