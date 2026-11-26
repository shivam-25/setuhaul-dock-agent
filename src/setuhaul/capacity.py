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
    r = con.execute("""
        SELECT s.shipment_id,s.destination_facility_id,s.required_dock_type,s.load_weight_kg,
               s.expected_unload_min,s.priority_code,
               COALESCE(v.effective_eta_ts,s.original_eta_ts) AS eta,
               COALESCE(v.eta_confidence,'HIGH') AS conf
        FROM shipments s LEFT JOIN v_latest_eta v ON v.shipment_id=s.shipment_id
        WHERE s.shipment_id=?""", (shipment_id,)).fetchone()
    if not r: raise ValueError(f"unknown shipment {shipment_id}")
    e = con.execute("""SELECT earliest_acceptable_ts,latest_acceptable_ts,declared_eta_ts
                       FROM driver_exceptions
                       WHERE shipment_id=? AND exception_status<>'DUPLICATE'
                       ORDER BY reported_at DESC LIMIT 1""", (shipment_id,)).fetchone()
    commitment = con.execute(
        "SELECT 1 FROM appointments WHERE shipment_id=? AND is_current=1 AND appointment_status IN "
        "('PENDING_CONFIRMATION','CONFIRMED') LIMIT 1", (shipment_id,)).fetchone() is not None
    eta = dt_(e[2]) if (e and e[2]) else dt_(r[6])
    return Ctx(r[0], r[1], r[2], r[3], r[4], r[5], eta, r[7],
               dt_(e[0]) if e and e[0] else None,
               dt_(e[1]) if e and e[1] else None,
               commitment, now, thread_id)

def _rules(con, facility_id) -> dict:
    return {k: v for k, v in con.execute(
        "SELECT rule_type,rule_value FROM facility_rules WHERE facility_id=?", (facility_id,))}

def candidates(con, ctx: Ctx, now: datetime, *, include_failures=True) -> list[Candidate]:
    """Every slot chain on every dock at the facility, each with its rejection reasons.
    F9 reads dock_status_events DIRECTLY, never slot_status - see audit D05."""
    rules = _rules(con, ctx.facility_id)
    # No rule at this facility means no heavy-dock mandate here, not Jaipur's 25000.
    heavy_kg = int(rules["HEAVY_DOCK_REQUIRED_KG"]) if "HEAVY_DOCK_REQUIRED_KG" in rules \
               else 10**9
    fac = con.execute("SELECT open_time,close_time FROM facilities WHERE facility_id=?",
                      (ctx.facility_id,)).fetchone()
    # If this facility sets no last-start rule, the limit is its own closing time. Falling back to
    # a constant would apply one facility's rule (Jaipur's 21:00) to another. Rules are per-facility.
    last_start = rules.get("LAST_NEW_START_TIME") or fac[1]
    docks = con.execute("SELECT dock_id,dock_code,dock_type,max_vehicle_weight_kg FROM docks "
                        "WHERE facility_id=?", (ctx.facility_id,)).fetchall()
    out: list[Candidate] = []
    for dock_id, dock_code, dock_type, dock_max_kg in docks:
        slots = con.execute("SELECT slot_id,slot_start_ts,slot_end_ts,slot_status FROM appointment_slots "
                            "WHERE dock_id=? ORDER BY slot_start_ts", (dock_id,)).fetchall()
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
            taken = con.execute(
                f"SELECT slot_id FROM appointments WHERE appointment_status IN {ACTIVE} "
                f"AND slot_id IN ({','.join('?'*len(chain))})", chain).fetchall()
            if taken: f.append(f"F4 already booked: {','.join(t[0] for t in taken)}")
            locked = con.execute(
                f"SELECT slot_id FROM slot_locks WHERE held_until > ? AND thread_id IS NOT ? "
                f"AND slot_id IN ({','.join('?'*len(chain))})",
                [ts(now), ctx.thread_id] + chain).fetchall()
            if locked: f.append(f"F5 held by another conversation: {','.join(l[0] for l in locked)}")
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
            outage = con.execute(
                "SELECT dock_event_id,event_type,event_start_ts,event_end_ts FROM dock_status_events "
                "WHERE dock_id=? AND event_start_ts < ? AND event_end_ts > ?",
                (dock_id, ts(end), ts(start))).fetchall()
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

def rank_requests(ctxs: list[Ctx]) -> list[Ctx]:
    """D7. Across competing requests. Physical arrival does NOT appear here, deliberately."""
    return sorted(ctxs, key=lambda c: (
        PRIORITY_RANK.get(c.priority_code, 9),
        0 if c.has_commitment else 1,
        c.effective_eta,
        c.requested_at or datetime.max.replace(tzinfo=c.effective_eta.tzinfo)))

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
        GROUP BY a.appointment_id
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
