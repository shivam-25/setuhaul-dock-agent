"""One service, two contracts.

  /ping, /invocations   - the Bedrock AgentCore Runtime contract
  /api/*                - the REST API the web UI uses

Both call the same pipeline, so the demo and the deployed agent cannot drift apart.
"""
from __future__ import annotations
import os, sys, sqlite3, pathlib, shutil, json
from datetime import datetime
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from setuhaul.agent import handle
from setuhaul.capacity import (dt_, load_ctx, candidates, rank_options, pending_acceptances,
                               decide_appointment, audit as write_audit,
                               open_alerts, operations_metrics)

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

def reset_live():
    for s in ("", "-wal", "-shm"):
        p = pathlib.Path(str(LIVE) + s)
        if p.exists(): p.unlink()
    LIVE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(BASE, LIVE)
    c = sqlite3.connect(LIVE); c.execute("PRAGMA journal_mode=WAL"); c.commit(); c.close()

if not LIVE.exists(): reset_live()

DATABASE_URL = os.environ.get("DATABASE_URL")

def db():
    """Postgres in any deployed environment, SQLite only for a local file run.

    This is the fix for the split-container defect: Lambda's /tmp is per execution
    environment, so two warm containers held two different SQLite files and could sell the
    same slot twice. Reserved concurrency caps simultaneous executions, not warm
    environments, so no Lambda setting could fix it. One shared Postgres does.
    """
    if DATABASE_URL:
        from setuhaul.db import connect
        return connect(DATABASE_URL, row_factory=True)
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
    driver_id: str = "DRV006"
    thread_id: str = "THR-API"
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
