"""The optional facility-level scheduling tool.

The agent handles one driver. This looks at one facility and all its trucks together. It
is a controlled tool: it reads structured rows and never free text, and it returns a
proposed sequence, never a promise. Nothing here writes to the database - a proposal that
silently rebooked drivers would break the one rule the whole service rests on.

The brief's mapping, implemented literally:

    job                 a shipment needing a dock
    machine             a dock door, the limited resource
    release time        when the truck can actually start: gate-in if it has arrived,
                        otherwise the buffered ETA. Observed beats predicted.
    processing time     expected_unload_min
    due date            the end of the appointment already promised, if there is one
    fixed task          a truck already on a dock. It cannot be moved, so the dock is
                        simply busy until it finishes.
    priority            a weight on waiting and on lateness

OBJECTIVE, in plain words: less waiting, fewer missed appointments, and fewer changes to
a schedule we have already told a driver about. The third term is why this is not a pure
optimiser - a sequence that saves ten minutes by moving three drivers who were already
promised a time is worse than one that saves five and moves nobody.

Per the brief, this starts as a dispatch rule with a transparent score and is compared
against simpler rules on the same snapshot. Constraint programming is not used, because
nothing here yet justifies it: see compare() for the measured gap.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .capacity import (dt_, ts, arrival, PRIORITY_RANK, CONFIDENCE_BUFFER_MIN,
                       commitment_state)

# Weight on waiting and lateness. CRITICAL waiting a minute costs 8x a LOW load waiting one.
PRIORITY_WEIGHT = {"CRITICAL": 8.0, "HIGH": 4.0, "NORMAL": 2.0, "LOW": 1.0}
# Cost of moving a driver who already has a promised window, in "minutes of waiting".
# Set deliberately high: a changed promise is worse than a short wait.
REBOOK_PENALTY_MIN = 45.0


@dataclass
class Job:
    shipment_id: str
    priority: str
    release: datetime          # cannot start before this
    process_min: int
    due: datetime | None       # promised window end, if any
    dock_type: str
    weight_kg: int
    fixed_dock: str | None = None   # already on this dock; cannot be moved
    fixed_until: datetime | None = None
    promised: bool = False

    @property
    def w(self) -> float: return PRIORITY_WEIGHT.get(self.priority, 1.0)


@dataclass
class Placement:
    shipment_id: str; dock_code: str
    start: datetime; end: datetime
    wait_min: float; late_min: float; moved: bool
    fixed: bool = False


def read_jobs(con, facility_id: str, now: datetime) -> list[Job]:
    """Structured rows only. No message text reaches this function."""
    rows = con.execute(
        "SELECT s.shipment_id, s.priority_code, s.required_dock_type, s.load_weight_kg, "
        "       s.expected_unload_min, COALESCE(v.effective_eta_ts, s.original_eta_ts) AS eta, "
        "       COALESCE(v.eta_confidence,'HIGH') AS conf "
        "FROM shipments s LEFT JOIN v_latest_eta v ON v.shipment_id = s.shipment_id "
        "WHERE s.destination_facility_id = ?", (facility_id,)).fetchall()
    jobs = []
    for r in rows:
        cols = ("shipment_id","priority_code","required_dock_type","load_weight_kg",
                "expected_unload_min","eta","conf")
        d = dict(r) if isinstance(r, dict) else dict(zip(cols, r))
        a = arrival(con, d["shipment_id"]) or {}
        if a.get("finished"):
            continue                                   # gone; not competing for anything
        eta = dt_(d["eta"])
        if now.tzinfo is None and eta.tzinfo is not None: eta = eta.replace(tzinfo=None)
        # Release time: observed arrival beats a prediction, and an observed one carries no
        # confidence buffer because there is nothing left to be uncertain about.
        if a.get("has_arrived") and a.get("gate_in_ts"):
            rel = dt_(a["gate_in_ts"])
            if now.tzinfo is None: rel = rel.replace(tzinfo=None)
        else:
            rel = eta + timedelta(minutes=CONFIDENCE_BUFFER_MIN.get(d["conf"], 15))
        cs = commitment_state(con, d["shipment_id"], now)
        jobs.append(Job(
            shipment_id=d["shipment_id"], priority=d["priority_code"], release=rel,
            process_min=int(d["expected_unload_min"] or 60),
            due=(cs or {}).get("end"), dock_type=d["required_dock_type"],
            weight_kg=int(d["load_weight_kg"] or 0),
            fixed_dock=(cs or {}).get("dock_code") if a.get("on_dock") else None,
            fixed_until=(cs or {}).get("end") if a.get("on_dock") else None,
            promised=bool(cs and cs.get("phase") in ("upcoming", "in_window"))))
    return jobs


def read_docks(con, facility_id: str) -> list[dict]:
    rows = con.execute(
        "SELECT dock_id, dock_code, dock_type FROM docks "
        "WHERE facility_id=? AND dock_status='ACTIVE' ORDER BY dock_code", (facility_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r) if isinstance(r, dict) else dict(zip(("dock_id","dock_code","dock_type"), r))
        out.append(d)
    return out


def _compatible(job: Job, dock: dict) -> bool:
    return dock["dock_type"] == job.dock_type or job.dock_type in (None, "", "ANY")


def dispatch(jobs: list[Job], docks: list[dict], now: datetime, rule: str = "weighted") -> list[Placement]:
    """Greedy list scheduling. At each point a dock frees, choose among the trucks that
    have arrived using one transparent rule, and place it.

    rule = fcfs      earliest release first - the obvious baseline
           edd       earliest due date first - classic for lateness
           spt       shortest processing time first - classic for waiting
           weighted  this tool's score (below)
    """
    free: dict[str, datetime] = {d["dock_code"]: now for d in docks}
    out: list[Placement] = []

    # Fixed tasks first: a truck already unloading owns its dock until it is done.
    for j in jobs:
        if j.fixed_dock and j.fixed_until:
            free[j.fixed_dock] = max(free.get(j.fixed_dock, now), j.fixed_until)
            out.append(Placement(j.shipment_id, j.fixed_dock, now, j.fixed_until,
                                 0.0, 0.0, False, fixed=True))

    pending = [j for j in jobs if not j.fixed_dock]
    while pending:
        # The dock that frees soonest makes the next decision.
        dock = min((d for d in docks), key=lambda d: free[d["dock_code"]])
        t = free[dock["dock_code"]]
        ready = [j for j in pending if _compatible(j, dock)]
        if not ready:
            docks = [d for d in docks if d["dock_code"] != dock["dock_code"]]
            if not docks: break
            continue
        available = [j for j in ready if j.release <= t] or [min(ready, key=lambda j: j.release)]

        def score(j: Job) -> float:
            start = max(t, j.release)
            wait = (start - j.release).total_seconds() / 60
            if rule == "fcfs": return -j.release.timestamp()
            if rule == "edd":
                # datetime.max.timestamp() overflows once a local offset is applied, so a
                # job with no due date sorts last via a plain large number instead.
                return -(j.due.timestamp() if j.due else 9.9e12)
            if rule == "spt":  return -j.process_min
            # weighted: value per minute of dock time, with lateness and the cost of
            # breaking a promise priced in. Everything here is visible and arguable.
            end  = start + timedelta(minutes=j.process_min)
            late = max(0.0, (end - j.due).total_seconds() / 60) if j.due else 0.0
            move = REBOOK_PENALTY_MIN if (j.promised and j.due and end > j.due) else 0.0
            return (j.w * (wait + late) + move) / max(1, j.process_min)

        pick = max(available, key=score)
        start = max(t, pick.release)
        end   = start + timedelta(minutes=pick.process_min)
        wait  = (start - pick.release).total_seconds() / 60
        late  = max(0.0, (end - pick.due).total_seconds() / 60) if pick.due else 0.0
        out.append(Placement(pick.shipment_id, dock["dock_code"], start, end,
                             round(wait, 1), round(late, 1),
                             moved=bool(pick.promised and pick.due and end > pick.due)))
        free[dock["dock_code"]] = end
        pending.remove(pick)
    return out


def summarise(jobs: list[Job], plan: list[Placement]) -> dict:
    byid = {j.shipment_id: j for j in jobs}
    movable = [p for p in plan if not p.fixed]
    if not movable:
        return {"scheduled": 0}
    tot_w = sum(byid[p.shipment_id].w * p.wait_min for p in movable)
    return {"scheduled": len(movable),
            "total_wait_min": round(sum(p.wait_min for p in movable), 1),
            "weighted_wait": round(tot_w, 1),
            "missed_appointments": sum(1 for p in movable if p.late_min > 0),
            "total_late_min": round(sum(p.late_min for p in movable), 1),
            "promises_broken": sum(1 for p in movable if p.moved),
            "makespan_min": round((max(p.end for p in movable)
                                   - min(p.start for p in movable)).total_seconds() / 60, 1)}


def compare(con, facility_id: str, now: datetime) -> dict:
    """The comparison the brief asks for: this tool's score against simple dispatch rules,
    on the same snapshot. If the simple rule wins, say so - that is the finding."""
    jobs, docks = read_jobs(con, facility_id, now), read_docks(con, facility_id)
    result = {}
    for rule in ("fcfs", "edd", "spt", "weighted"):
        plan = dispatch(jobs, docks, now, rule)
        result[rule] = {"summary": summarise(jobs, plan),
                        "sequence": [{"shipment": p.shipment_id, "dock": p.dock_code,
                                      "start": ts(p.start), "end": ts(p.end),
                                      "wait_min": p.wait_min, "late_min": p.late_min,
                                      "fixed": p.fixed} for p in plan]}
    return {"facility": facility_id, "as_of": ts(now), "jobs": len(jobs),
            "docks": len(docks), "objective": "less waiting, fewer missed appointments, "
            "fewer changes to a schedule already told to a driver", "rules": result}
