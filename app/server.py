"""One service, two contracts.

  /ping, /invocations   - the Bedrock AgentCore Runtime contract
  /api/*                - the REST API the web UI uses

Both call the same pipeline, so the demo and the deployed agent cannot drift apart.
"""
from __future__ import annotations
import os, sys, sqlite3, pathlib, shutil, json, uuid
from datetime import datetime
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from setuhaul.agent import handle
from setuhaul.capacity import (dt_, load_ctx, candidates, rank_options, pending_acceptances,
                               decide_appointment, audit as write_audit,
                               open_alerts, operations_metrics, commitment_state,
                               PRIORITY_RANK, ts)

BASE = pathlib.Path(os.environ.get("SETUHAUL_BASE_DB", ROOT / "work" / "setuhaul_work.db"))
LIVE = pathlib.Path(os.environ.get("SETUHAUL_LIVE_DB", ROOT / "work" / "live.db"))
DEFAULT_NOW = os.environ.get("SETUHAUL_NOW", "2026-08-04T10:05:00+05:30")

app = FastAPI(title="SetuHaul dock exception service")

# CORS must be owned by exactly ONE layer. A Lambda Function URL applies its own CORS config to
# every response, so adding the middleware too emits two Access-Control-Allow-Origin headers and
# every browser rejects the response - while curl, which does not enforce CORS, reports 200.
# That is why this only shows up when you actually open the page.
ON_LAMBDA = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
ALLOWED_ORIGINS = [o for o in os.environ.get("SETUHAUL_ORIGINS", "*").split(",") if o]
if not ON_LAMBDA:
    app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS,
                       allow_methods=["GET", "POST"], allow_headers=["*"])

# A demo token, NOT authentication. It ships in the browser bundle, so anyone who opens the page
# can read it. It exists only to stop drive-by scanners spending Bedrock tokens on this account,
# which is the realistic threat to a public function URL. Real access control is the Cognito pool
# described in docs/06-AWS-RUNBOOK.md: there the token is issued per person and carries a facility.
DEMO_TOKEN = os.environ.get("SETUHAUL_DEMO_TOKEN")

# The shared demo token is retired. It shipped inside the browser bundle, so every visitor
# had it and it was never a credential - it kept a public URL from being trivially crawled,
# nothing more. Each person now signs in, and authorisation is per role in app/authapi.py.
# SETUHAUL_DEMO_TOKEN is still honoured if set, as a crawler gate in front of sign-in only.
@app.middleware("http")
async def gate(request, call_next):
    if DEMO_TOKEN and request.url.path.startswith("/api/") \
       and not request.url.path.startswith("/api/auth/") \
       and request.method != "OPTIONS" \
       and request.headers.get("x-demo-token") != DEMO_TOKEN:
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "missing or wrong x-demo-token"}, status_code=401)
    return await call_next(request)

DATABASE_URL = os.environ.get("DATABASE_URL")

def reset_live():
    for s in ("", "-wal", "-shm"):
        p = pathlib.Path(str(LIVE) + s)
        if p.exists(): p.unlink()
    LIVE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(BASE, LIVE)
    c = sqlite3.connect(LIVE); c.execute("PRAGMA journal_mode=WAL"); c.commit(); c.close()

# Only the SQLite path needs a writable working copy. On Postgres there is nothing to
# build, and attempting it fails at import: the Lambda bundle is mounted read-only.
if not os.environ.get("DATABASE_URL") and not LIVE.exists():
    reset_live()

# One Postgres connection per warm container, not per request.
#
# This function is in ap-southeast-2 and the database is in ap-south-1, because an
# organisation service control policy denies Lambda in ap-south-1 and the two cannot be
# co-located. At roughly 90 ms between them, a fresh connect costs about ten round trips
# for TLS and SCRAM before a single query runs - which made a plain metrics read take five
# seconds. A Lambda execution environment is reused across invocations, so the connection
# should be too.
_PG = None

class _Keep:
    """Hands back the shared connection and swallows close(), because every call site
    closes in a finally block. Rolling back on release is what stops one request's aborted
    transaction from poisoning the next one that reuses the container."""
    def __init__(self, con): self._c = con
    def __getattr__(self, k): return getattr(self._c, k)
    def close(self):
        try: self._c.rollback()
        except Exception: pass


def db():
    """Postgres in any deployed environment, SQLite only for a local file run.

    This is the fix for the split-container defect: Lambda's /tmp is per execution
    environment, so two warm containers held two different SQLite files and could sell the
    same slot twice. Reserved concurrency caps simultaneous executions, not warm
    environments, so no Lambda setting could fix it. One shared Postgres does.
    """
    global _PG
    if DATABASE_URL:
        from setuhaul.db import connect
        if _PG is not None:
            try:
                _PG.execute("SELECT 1").fetchone()      # cheap liveness probe
                return _Keep(_PG)
            except Exception:
                try: _PG.close()
                except Exception: pass
                _PG = None
        _PG = connect(DATABASE_URL, row_factory=True)
        return _Keep(_PG)
    con = sqlite3.connect(LIVE, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=5000")
    return con

# ------------------------------------------------------------------ AgentCore
from app.authapi import router as auth_router, current_user, require_role, scope_clause
app.include_router(auth_router)


@app.get("/ping")
def ping(): return {"status": "healthy"}

class Invocation(BaseModel):
    prompt: str | None = None
    driver_id: str = "DRV006"
    thread_id: str = "THR-API"
    shipment_id: str | None = None
    now: str | None = None

@app.post("/invocations")
def invocations(inv: Invocation):
    return chat(ChatIn(message=inv.prompt or "", driver_id=inv.driver_id,
                       thread_id=inv.thread_id, shipment_id=inv.shipment_id, now=inv.now))

# ------------------------------------------------------------------ the UI API
class ChatIn(BaseModel):
    message: str
    # No demo defaults here. driver_id used to default to "DRV006" and thread_id to a
    # single shared "THR-API".
    #
    # The first one locked every other driver out of the product: a signed-in driver who
    # did not send the field got DRV006, the guard below saw a mismatch with their own
    # identity and refused them. The web client does not send it, so fourteen of fifteen
    # drivers could not use chat at all. It survived because every test ran as DRV006.
    #
    # The second was worse in kind: two drivers who both omitted thread_id would have
    # shared one conversation and seen each other's turns.
    driver_id: str | None = None
    thread_id: str | None = None
    shipment_id: str | None = None
    now: str | None = None

@app.post("/api/chat")
def chat(body: ChatIn, user: dict = Depends(current_user)):
    if not body.message.strip(): raise HTTPException(400, "empty message")
    # A driver speaks only as themselves. Without this, any signed-in driver could post
    # into another driver's thread by changing one field in the request body.
    if user["role"] == "driver":
        if body.driver_id and body.driver_id != user["driver_id"]:
            raise HTTPException(403, "you can only send messages as yourself")
        body.driver_id = user["driver_id"]
    elif not body.driver_id:
        raise HTTPException(400, "driver_id is required when not signed in as a driver")
    if not body.thread_id:
        body.thread_id = f"THR-{uuid.uuid4().hex[:10]}"
    con = db()
    try:
        rec = handle(con, message=body.message, driver_id=body.driver_id,
                     thread_id=body.thread_id, now=dt_(body.now or DEFAULT_NOW),
                     shipment_id=body.shipment_id)
    finally:
        con.close()
    rec.pop("t0", None)
    return rec

@app.get("/api/drivers")
def drivers(user: dict = Depends(current_user)):
    # One scope_clause, defined once in authapi, so a new endpoint cannot accidentally
    # expose another driver's trips by forgetting a WHERE.
    where, params = scope_clause(user)
    con = db()
    rows = con.execute(f"""
        SELECT d.driver_id, d.driver_name, s.shipment_id, s.destination_facility_id,
               s.required_dock_type, s.load_weight_kg, s.expected_unload_min,
               s.priority_code, s.original_eta_ts, s.current_status
        FROM drivers d JOIN shipments s ON s.driver_id = d.driver_id
        WHERE s.current_status IN ('ASSIGNED','IN_TRANSIT','WAITING') AND {where}
        ORDER BY d.driver_id, s.original_eta_ts""", params).fetchall()
    con.close()
    out = {}
    for r in rows:
        out.setdefault(r["driver_id"], {"driver_id": r["driver_id"], "name": r["driver_name"],
                                        "shipments": []})
        out[r["driver_id"]]["shipments"].append(dict(r))
    return list(out.values())

def assert_may_see(con, user: dict, shipment_id: str):
    """One ownership rule for every endpoint that takes a shipment id.

    Found by driving the UI as a driver: /api/why returned another driver's full
    rejection detail - their load, priority, dock type and every slot considered - to
    anyone signed in. Hiding a link in the nav is not access control.
    """
    if user["role"] == "coordinator":
        return
    row = con.execute("SELECT driver_id, destination_facility_id FROM shipments "
                      "WHERE shipment_id=?", (shipment_id,)).fetchone()
    if not row:
        raise HTTPException(404, "no such shipment")
    if user["role"] == "driver" and row["driver_id"] != user["driver_id"]:
        raise HTTPException(403, "that shipment is not yours")
    if user["role"] == "warehouse" and row["destination_facility_id"] != user["facility_id"]:
        raise HTTPException(403, "that shipment is not at your facility")


@app.get("/api/why/{shipment_id}")
def why(shipment_id: str, now: str | None = None, user: dict = Depends(current_user)):
    """Every candidate considered, with the reason each was rejected. The coordinator's panel."""
    con = db(); n = dt_(now or DEFAULT_NOW)
    assert_may_see(con, user, shipment_id)
    try:
        ctx = load_ctx(con, shipment_id, thread_id="WHY", now=n)
        cands = candidates(con, ctx, n)
    except Exception as e:
        con.close(); raise HTTPException(404, str(e))
    ok = rank_options([c for c in cands if c.ok])
    con.close()
    return {"shipment_id": shipment_id, "now": now or DEFAULT_NOW,
            "effective_eta": f"{ctx.effective_eta:%Y-%m-%dT%H:%M}", "confidence": ctx.confidence,
            "ready_at": f"{ctx.ready_at:%H:%M}", "priority": ctx.priority_code,
            "required_dock_type": ctx.required_dock_type,
            "unload_minutes": ctx.expected_unload_min,
            "considered": len(cands), "feasible": len(ok),
            "accepted": [{"label": c.label, "dock": c.dock_code, "start": f"{c.start:%H:%M}",
                          "end": f"{c.end:%H:%M}", "slots": c.slot_ids} for c in ok[:8]],
            "rejected": [{"label": c.label, "dock": c.dock_code, "start": f"{c.start:%H:%M}",
                          "reasons": c.failures} for c in cands if not c.ok][:60]}

@app.get("/api/board")
def board(user: dict = Depends(require_role("coordinator","warehouse")), now: str | None = None):
    con = db()
    rows = con.execute("""
        SELECT f.facility_id, d.dock_code, d.dock_type, s.slot_id, s.slot_start_ts, s.slot_end_ts,
               s.slot_status,
               (SELECT a.appointment_status FROM appointments a
                 WHERE a.slot_id = s.slot_id
                   AND a.appointment_status IN ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS')
                 LIMIT 1) AS appt_status,
               (SELECT a.shipment_id FROM appointments a
                 WHERE a.slot_id = s.slot_id
                   AND a.appointment_status IN ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS')
                 LIMIT 1) AS appt_shipment,
               (SELECT l.thread_id FROM slot_locks l
                 WHERE l.slot_id = s.slot_id AND l.held_until > ?) AS held_by,
               (SELECT GROUP_CONCAT(e.event_type) FROM dock_status_events e
                 WHERE e.dock_id = d.dock_id
                   AND e.event_start_ts < s.slot_end_ts AND e.event_end_ts > s.slot_start_ts) AS outage
        FROM appointment_slots s
        JOIN docks d ON d.dock_id = s.dock_id
        JOIN facilities f ON f.facility_id = d.facility_id
        ORDER BY f.facility_id, d.dock_code, s.slot_start_ts""",
        (now or DEFAULT_NOW,)).fetchall()
    con.close()
    return [dict(r) for r in rows]

@app.get("/api/audit")
def audit(limit: int = 60, user: dict = Depends(current_user)):
    """The append-only decision log, filtered to what this person may see.

    Unscoped, this returned every decision for every driver at every facility to anyone
    signed in. A driver may read the reasoning about their own loads - that is the point
    of an auditable system - but not about anyone else's.
    """
    con = db()
    if user["role"] == "coordinator":
        rows = con.execute("SELECT * FROM decision_audit ORDER BY audit_id DESC LIMIT ?",
                           (limit,)).fetchall()
    elif user["role"] == "driver":
        rows = con.execute(
            "SELECT a.* FROM decision_audit a JOIN shipments s ON s.shipment_id=a.shipment_id "
            "WHERE s.driver_id=? ORDER BY a.audit_id DESC LIMIT ?",
            (user["driver_id"], limit)).fetchall()
    else:
        rows = con.execute(
            "SELECT a.* FROM decision_audit a JOIN shipments s ON s.shipment_id=a.shipment_id "
            "WHERE s.destination_facility_id=? ORDER BY a.audit_id DESC LIMIT ?",
            (user["facility_id"], limit)).fetchall()
    con.close()
    return [dict(r) for r in rows]

class Decision(BaseModel):
    appointment_id: str
    accept: bool
    actor: str = "warehouse"
    reason: str | None = None
    now: str | None = None


@app.get("/api/pending")
def pending(user: dict = Depends(require_role("warehouse","coordinator")), ):
    """What the warehouse has been asked to accept and has not answered."""
    con = db()
    try:
        return pending_acceptances(con)
    finally:
        con.close()


@app.post("/api/decide")
def decide(body: Decision, user: dict = Depends(require_role("warehouse","coordinator"))):
    """The other half of 'waiting for warehouse to accept'.

    Until this existed every booking told the driver it was awaiting acceptance and nothing
    in the system could ever accept it. The decision is audited like any agent decision, with
    actor set to the warehouse rather than the agent.
    """
    now = dt_(body.now or DEFAULT_NOW)
    con = db()
    try:
        out = decide_appointment(con, body.appointment_id, accept=body.accept,
                                 actor=body.actor, reason=body.reason, now=now)
        # decision_audit.actor is CHECK-constrained to ('agent','human','system'). A warehouse
        # decision is made by a person, so the column says 'human' and the role it was made in
        # goes in the detail, where it is still queryable. Widening the CHECK is a schema change
        # on the supplied snapshot, which this project does not make.
        write_audit(con, request_id=f"WH-{body.appointment_id}",
                    box="12", actor="human",
                    decision="warehouse_accepted" if body.accept else "warehouse_rejected",
                    shipment_id=out.get("shipment_id"),
                    detail={**{k: v for k, v in out.items() if k != "shipment_id"},
                            "role": body.actor, "reason": body.reason}, now=now)
        if not out.get("ok") and not out.get("already"):
            raise HTTPException(409, out.get("error", "could not decide"))
        return out
    finally:
        con.close()


@app.get("/api/alerts")
def alerts(user: dict = Depends(require_role("coordinator","warehouse")), ):
    """What a SetuHaul operations coordinator must act on. Derived from decision_audit, so
    it cannot disagree with what the agent actually did."""
    con = db()
    try:
        return open_alerts(con)
    finally:
        con.close()


@app.get("/api/metrics")
def metrics(user: dict = Depends(require_role("coordinator","warehouse")), ):
    """The brief's measures, computed rather than asserted."""
    con = db()
    try:
        return operations_metrics(con)
    finally:
        con.close()



# ---------------------------------------------------------------- dashboards
# The six stages come from the brief's "follow one truck". facility_checkins already
# records each timestamp, so progress is read from the data rather than inferred from a
# status string that can disagree with it.
STAGES = [("departed", "Departed"), ("gate_in_ts", "At gate"), ("yard_queue_enter_ts", "In yard"),
          ("dock_in_ts", "On dock"), ("unload_start_ts", "Unloading"), ("gate_out_ts", "Released")]


def _trip_rows(con, where, params, now_ts):
    return con.execute(f"""
        SELECT s.shipment_id, s.order_reference, s.customer_name, s.origin_city,
               s.destination_facility_id, s.priority_code, s.current_status,
               s.required_dock_type, s.load_weight_kg, s.pallet_count, s.product_category,
               s.expected_unload_min, s.planned_departure_ts, s.actual_departure_ts,
               s.original_eta_ts, d.driver_name, d.driver_id, f.facility_name,
               COALESCE(v.effective_eta_ts, s.original_eta_ts) AS eta,
               COALESCE(v.eta_confidence, 'HIGH')              AS eta_conf,
               c.gate_in_ts, c.yard_queue_enter_ts, c.dock_in_ts, c.unload_start_ts,
               c.unload_end_ts, c.gate_out_ts, c.queue_position,
               (SELECT a.appointment_id FROM appointments a WHERE a.shipment_id=s.shipment_id
                 AND a.is_current=1 AND a.appointment_status IN
                 ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS') LIMIT 1) AS appt_id,
               (SELECT a.appointment_status FROM appointments a WHERE a.shipment_id=s.shipment_id
                 AND a.is_current=1 AND a.appointment_status IN
                 ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS') LIMIT 1) AS appt_status,
               (SELECT dk.dock_code FROM appointments a
                 JOIN appointment_slots sl ON sl.slot_id=a.slot_id
                 JOIN docks dk ON dk.dock_id=sl.dock_id
                 WHERE a.shipment_id=s.shipment_id AND a.is_current=1 AND a.appointment_status IN
                 ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS') LIMIT 1) AS dock_code,
               (SELECT sl.slot_start_ts FROM appointments a
                 JOIN appointment_slots sl ON sl.slot_id=a.slot_id
                 WHERE a.shipment_id=s.shipment_id AND a.is_current=1 AND a.appointment_status IN
                 ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS') LIMIT 1) AS slot_start,
               (SELECT sl.slot_end_ts FROM appointments a
                 JOIN appointment_slots sl ON sl.slot_id=a.slot_id
                 WHERE a.shipment_id=s.shipment_id AND a.is_current=1 AND a.appointment_status IN
                 ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS') LIMIT 1) AS slot_end
        FROM shipments s
        JOIN drivers d   ON d.driver_id = s.driver_id
        JOIN facilities f ON f.facility_id = s.destination_facility_id
        LEFT JOIN v_latest_eta v ON v.shipment_id = s.shipment_id
        LEFT JOIN facility_checkins c ON c.shipment_id = s.shipment_id
        WHERE {where}
        ORDER BY s.original_eta_ts""", params).fetchall()



def phase_of(d, now):
    """The commitment phase computed from a row already fetched, not re-queried.

    commitment_state() costs two round trips per shipment - itself plus arrival() - and the
    operations dashboard called it once per truck, which is where 35 queries and eight
    seconds came from. Every column it reads is already in _trip_rows: the slot window and
    the check-in timestamps. So compute it here instead and spend nothing.

    This duplicates the rule, which is a real risk: two copies can drift. scripts/phase_test.py
    asserts the two agree for every shipment in the snapshot, so a drift fails a test rather
    than quietly mis-stating a booking to a driver.
    """
    if not d.get("appt_id") or not d.get("slot_end"):
        return None
    start, end = dt_(d["slot_start"]), dt_(d["slot_end"])
    if now.tzinfo is None:
        start, end = start.replace(tzinfo=None), end.replace(tzinfo=None)
    elif start.tzinfo is None:
        start, end = start.replace(tzinfo=now.tzinfo), end.replace(tzinfo=now.tzinfo)
    # arrival(): on_dock is dock_in_ts, finished is gate_out_ts. queue_state is not in this
    # row, but dock_in_ts and gate_out_ts are set whenever it is, which phase_test checks.
    if d.get("dock_in_ts") or d.get("unload_start_ts") or d.get("gate_out_ts"):
        return "being_served"
    if now >= end:
        return "lapsed"
    if now >= start:
        return "in_window"
    return "upcoming"


def _trip_alerts(con, d, now, voice="driver"):
    """What someone must be told without having to ask.

    The same facts read differently depending on who is looking. A driver is told "your
    booked window has passed"; a coordinator looking at the whole fleet is told which
    driver and which customer, because "your window" is meaningless to them and there
    are eighteen trucks on the page.
    """
    out = []
    sid = d["shipment_id"]
    ops = voice != "driver"
    who = f"{d['driver_name']} · {d['customer_name']}"
    if d["appt_id"]:
        phase = phase_of(d, now)
        if phase == "lapsed":
            out.append({"level": "critical",
                        "title": "Booked window has passed" if ops
                                 else "Your booked window has passed",
                        "body": (f"{who} — dock {d['dock_code']} "
                                 f"{fmt_hm(d['slot_start'])}-{fmt_hm(d['slot_end'])} expired "
                                 f"with the truck still waiting. Needs a new slot.") if ops else
                                (f"Dock {d['dock_code']} {fmt_hm(d['slot_start'])}-"
                                 f"{fmt_hm(d['slot_end'])} is no longer valid. A coordinator "
                                 f"is finding you a new one."),
                        "action": None if ops else "ask",
                        "ask": "My booked window has passed. What now?"})
        elif phase == "being_served":
            out.append({"level": "ok", "title": f"Unloading at dock {d['dock_code']}",
                        "body": f"{who} is on the dock." if ops
                                else "Nothing to do. You are on the dock now."})
        elif d["appt_status"] == "PENDING_CONFIRMATION":
            out.append({"level": "warn", "title": "Held, waiting for the warehouse",
                        "body": (f"{who} — dock {d['dock_code']} {fmt_hm(d['slot_start'])} is "
                                 f"held but not accepted. The driver has been told it is not "
                                 f"confirmed.") if ops else
                                (f"Dock {d['dock_code']} {fmt_hm(d['slot_start'])} is held for "
                                 f"you but not yet accepted. Do not rely on it until it is."),
                        "action": None if ops else "ask",
                        "ask": "Has the warehouse accepted my slot yet?"})
        elif phase == "in_window":
            out.append({"level": "ok",
                        "title": "Window open now" if ops else "Your window is open now",
                        "body": (f"{who} — dock {d['dock_code']} until "
                                 f"{fmt_hm(d['slot_end'])}.") if ops
                                else f"Dock {d['dock_code']} until {fmt_hm(d['slot_end'])}."})
    elif d["current_status"] in ("IN_TRANSIT", "WAITING", "ASSIGNED"):
        out.append({"level": "warn", "title": "No dock booked yet",
                    "body": f"{who} is in transit with no appointment." if ops
                            else "Tell us your delay and we will find you a slot.",
                    "action": None if ops else "ask",
                    "ask": "I am running late. What slots can I get?"})
    if d["delay_min"] >= 30 and d["appt_id"]:
        out.append({"level": "warn", "title": f"Running {d['delay_min']} minutes behind",
                    "body": f"{who} — booked window may no longer fit." if ops
                            else "Your booked window may no longer fit. Worth checking.",
                    "action": None if ops else "ask",
                    "ask": f"I am about {d['delay_min']} minutes late. "
                           f"Does my slot still work?"})
    if d["queue_position"]:
        out.append({"level": "info", "title": f"Position {d['queue_position']} in the yard queue",
                    "body": f"{who} is checked in, waiting for a dock." if ops
                            else "You are checked in and waiting for a dock."})
    return out


def fmt_hm(t):
    return (t or "")[11:16]


@app.get("/api/me/trips")
def my_trips(user: dict = Depends(current_user), now: str | None = None):
    """Everything a driver's home screen needs, in one call."""
    n = dt_(now or DEFAULT_NOW)
    voice = "driver" if user["role"] == "driver" else "ops"
    where, params = scope_clause(user)
    con = db()
    try:
        rows = _trip_rows(con, where, params, ts(n))
        trips = []
        for r in rows:
            d = dict(r)
            done, stage_now = [], None
            for key, label in STAGES:
                at = d["actual_departure_ts"] if key == "departed" else d.get(key)
                done.append({"key": key, "label": label, "at": fmt_hm(at), "done": bool(at)})
                if at:
                    stage_now = label
            d["stages"] = done
            d["stage"] = stage_now or "Not departed"
            eta = dt_(d["eta"]) if d["eta"] else None
            plan = dt_(d["original_eta_ts"]) if d["original_eta_ts"] else None
            d["delay_min"] = int((eta - plan).total_seconds() // 60) if eta and plan else 0
            d["eta_hm"], d["plan_hm"] = fmt_hm(d["eta"]), fmt_hm(d["original_eta_ts"])
            d["slot_hm"] = (f"{fmt_hm(d['slot_start'])}-{fmt_hm(d['slot_end'])}"
                            if d["slot_start"] else None)
            d["alerts"] = _trip_alerts(con, d, n, voice)
            trips.append(d)

        # A driver's own record, so the numbers on their screen are about them.
        active = [t for t in trips if t["current_status"] in
                  ("ASSIGNED", "IN_TRANSIT", "WAITING", "IN_DOCK")]
        booked = [t for t in active if t["appt_id"]]
        late = [t for t in active if t["delay_min"] >= 15]
        return {"now": fmt_hm(ts(n)), "day": ts(n)[:10], "voice": voice, "trips": trips,
                "summary": {"active": len(active), "booked": len(booked),
                            "unbooked": len(active) - len(booked), "late": len(late),
                            "alerts": sum(len(t["alerts"]) for t in trips),
                            "critical": sum(1 for t in trips for a in t["alerts"]
                                            if a["level"] == "critical")},
                "delay_chart": [{"label": t["shipment_id"][-4:], "value": t["delay_min"],
                                 "sub": t["customer_name"]} for t in active]}
    finally:
        con.close()


@app.get("/api/ops/overview")
def ops_overview(user: dict = Depends(require_role("coordinator", "warehouse")),
                 now: str | None = None):
    """One call behind the operations dashboard: the state of the day, plus the series the
    charts draw. Scoped, so a warehouse user sees only their own facility."""
    n = dt_(now or DEFAULT_NOW)
    fac = user.get("facility_id")
    con = db()
    try:
        fac_where = "AND dk.facility_id = ?" if fac else ""
        fac_p = (fac,) if fac else ()

        # Dock-by-dock state at this instant: what is on each dock and what is next.
        docks = con.execute(f"""
            SELECT dk.dock_id, dk.dock_code, dk.dock_type, dk.facility_id, f.facility_name,
                   (SELECT e.event_type FROM dock_status_events e WHERE e.dock_id = dk.dock_id
                     AND e.event_start_ts <= ? AND e.event_end_ts > ?)        AS outage,
                   (SELECT a.shipment_id FROM appointments a
                     JOIN appointment_slots sl ON sl.slot_id = a.slot_id
                     WHERE sl.dock_id = dk.dock_id AND a.appointment_status = 'IN_PROGRESS')
                                                                              AS busy_with,
                   (SELECT COUNT(*) FROM appointment_slots sl
                     JOIN appointments a ON a.slot_id = sl.slot_id AND a.is_current = 1
                      AND a.appointment_status IN ('PENDING_CONFIRMATION','CONFIRMED')
                     WHERE sl.dock_id = dk.dock_id AND sl.slot_start_ts >= ?) AS upcoming,
                   (SELECT COUNT(*) FROM appointment_slots sl
                     WHERE sl.dock_id = dk.dock_id AND sl.slot_status = 'OPEN'
                       AND sl.slot_start_ts >= ?)                             AS open_slots
            FROM docks dk JOIN facilities f ON f.facility_id = dk.facility_id
            WHERE 1=1 {fac_where}
            ORDER BY dk.facility_id, dk.dock_code""",
            (ts(n), ts(n), ts(n), ts(n)) + fac_p).fetchall()

        # Bookings per hour across the day - the shape of the facility's load.
        by_hour = con.execute(f"""
            SELECT SUBSTR(sl.slot_start_ts, 12, 2) AS hh,
                   COUNT(*) AS booked
            FROM appointments a
            JOIN appointment_slots sl ON sl.slot_id = a.slot_id
            JOIN docks dk ON dk.dock_id = sl.dock_id
            WHERE a.is_current = 1
              AND a.appointment_status IN ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS')
              {fac_where}
            GROUP BY hh ORDER BY hh""", fac_p).fetchall()
        open_hour = con.execute(f"""
            SELECT SUBSTR(sl.slot_start_ts, 12, 2) AS hh, COUNT(*) AS open
            FROM appointment_slots sl JOIN docks dk ON dk.dock_id = sl.dock_id
            WHERE sl.slot_status = 'OPEN' {fac_where}
            GROUP BY hh ORDER BY hh""", fac_p).fetchall()
        openmap = {r["hh"]: r["open"] for r in open_hour}

        # Trucks that need someone to look at them, worst first.
        where, params = scope_clause(user)
        trips = _trip_rows(con, where, params, ts(n))
        attention = []
        for r in trips:
            d = dict(r)
            if d["current_status"] in ("COMPLETED", "CANCELLED"):
                continue
            eta = dt_(d["eta"]) if d["eta"] else None
            plan = dt_(d["original_eta_ts"]) if d["original_eta_ts"] else None
            delay = int((eta - plan).total_seconds() // 60) if eta and plan else 0
            phase = phase_of(d, n)
            why = None
            if phase == "lapsed":
                why, rank = "Booked window has passed", 0
            elif not d["appt_id"] and d["current_status"] in ("IN_TRANSIT", "WAITING"):
                why, rank = "No dock booked", 1
            elif d["appt_status"] == "PENDING_CONFIRMATION":
                why, rank = "Waiting for warehouse to accept", 2
            elif delay >= 30:
                why, rank = f"{delay} min behind, slot may not fit", 3
            if why:
                attention.append({"shipment_id": d["shipment_id"], "driver": d["driver_name"],
                                  "driver_id": d["driver_id"], "customer": d["customer_name"],
                                  "priority": d["priority_code"], "why": why, "rank": rank,
                                  "dock": d["dock_code"], "eta": fmt_hm(d["eta"]),
                                  "delay_min": delay, "facility": d["facility_name"]})
        attention.sort(key=lambda a: (a["rank"], PRIORITY_RANK.get(a["priority"], 9)))

        m = operations_metrics(con)
        busy = sum(1 for d in docks if d["busy_with"])
        out_n = sum(1 for d in docks if d["outage"])
        return {
            "now": fmt_hm(ts(n)),
            "day": ts(n)[:10],
            "scope": fac or "all facilities",
            "docks": [dict(d) for d in docks],
            "dock_summary": {"total": len(docks), "busy": busy, "out": out_n,
                             "free": len(docks) - busy - out_n},
            "attention": attention[:12],
            "attention_total": len(attention),
            "alerts": open_alerts(con, 8),
            "metrics": m,
            "load_chart": [{"label": f"{r['hh']}:00", "value": r["booked"],
                            "sub": f"{openmap.get(r['hh'], 0)} open"} for r in by_hour],
        }
    finally:
        con.close()


@app.post("/api/reset")
def reset(user: dict = Depends(require_role("coordinator"))):
    # Destructive, and it had no guard at all: any signed-in driver could wipe the demo.
    reset_live(); return {"reset": True, "now": DEFAULT_NOW}

@app.get("/api/meta")
def meta(user: dict = Depends(current_user), ):
    con = db()
    n = lambda q: con.execute(q).fetchone()[0]
    out = {"now": DEFAULT_NOW,
           "shipments": n("SELECT COUNT(*) FROM shipments"),
           "docks": n("SELECT COUNT(*) FROM docks"),
           "slots": n("SELECT COUNT(*) FROM appointment_slots"),
           "active_appointments": n("SELECT COUNT(*) FROM appointments WHERE appointment_status "
                                    "IN ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS')"),
           "audit_rows": n("SELECT COUNT(*) FROM decision_audit")}
    con.close(); return out

handler = None
try:
    from mangum import Mangum
    handler = Mangum(app)          # Lambda entrypoint, unused when run under uvicorn
except ImportError:
    pass
