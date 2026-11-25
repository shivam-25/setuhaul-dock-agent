"""One service, two contracts.

  /ping, /invocations   - the Bedrock AgentCore Runtime contract
  /api/*                - the REST API the web UI uses

Both call the same pipeline, so the demo and the deployed agent cannot drift apart.
"""
from __future__ import annotations
import os, sys, sqlite3, pathlib, shutil, json
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from setuhaul.agent import handle
from setuhaul.capacity import dt_, load_ctx, candidates, rank_options

BASE = pathlib.Path(os.environ.get("SETUHAUL_BASE_DB", ROOT / "work" / "setuhaul_work.db"))
LIVE = pathlib.Path(os.environ.get("SETUHAUL_LIVE_DB", ROOT / "work" / "live.db"))
DEFAULT_NOW = os.environ.get("SETUHAUL_NOW", "2026-08-04T10:05:00+05:30")

app = FastAPI(title="SetuHaul dock exception service")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def reset_live():
    for s in ("", "-wal", "-shm"):
        p = pathlib.Path(str(LIVE) + s)
        if p.exists(): p.unlink()
    LIVE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(BASE, LIVE)
    c = sqlite3.connect(LIVE); c.execute("PRAGMA journal_mode=WAL"); c.commit(); c.close()

if not LIVE.exists(): reset_live()

def db():
    con = sqlite3.connect(LIVE, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=5000")
    return con

# ------------------------------------------------------------------ AgentCore
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
def chat(body: ChatIn):
    if not body.message.strip(): raise HTTPException(400, "empty message")
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
def drivers():
    con = db()
    rows = con.execute("""
        SELECT d.driver_id, d.driver_name, s.shipment_id, s.destination_facility_id,
               s.required_dock_type, s.load_weight_kg, s.expected_unload_min,
               s.priority_code, s.original_eta_ts, s.current_status
        FROM drivers d JOIN shipments s ON s.driver_id = d.driver_id
        WHERE s.current_status IN ('ASSIGNED','IN_TRANSIT','WAITING')
        ORDER BY d.driver_id, s.original_eta_ts""").fetchall()
    con.close()
    out = {}
    for r in rows:
        out.setdefault(r["driver_id"], {"driver_id": r["driver_id"], "name": r["driver_name"],
                                        "shipments": []})
        out[r["driver_id"]]["shipments"].append(dict(r))
    return list(out.values())

@app.get("/api/why/{shipment_id}")
def why(shipment_id: str, now: str | None = None):
    """Every candidate considered, with the reason each was rejected. The coordinator's panel."""
    con = db(); n = dt_(now or DEFAULT_NOW)
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
def board(now: str | None = None):
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
def audit(limit: int = 60):
    con = db()
    rows = con.execute("SELECT * FROM decision_audit ORDER BY audit_id DESC LIMIT ?",
                       (limit,)).fetchall()
    con.close()
    return [dict(r) for r in rows]

@app.post("/api/reset")
def reset():
    reset_live(); return {"reset": True, "now": DEFAULT_NOW}

@app.get("/api/meta")
def meta():
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
