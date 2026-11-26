"""The conversation lane. Boxes 1-6 and 11. Two model calls, everything else code."""
from __future__ import annotations
import json, re, time, uuid
from datetime import datetime, timedelta
import os
import boto3
from botocore.config import Config
from . import prompts
from .capacity import (Ctx, dt_, ts, CONFIDENCE_BUFFER_MIN, candidates, feasible,
                       rank_options, claim, commit_booking, LostRace, audit, ACTIVE)

REGION = os.environ.get("AWS_REGION") or "ap-southeast-2"
# A named profile is a developer-laptop idea. On Lambda or AgentCore there is no profile and no
# key: the one execution role supplies credentials. Setting a profile there fails outright, which
# is the correct failure - it means something is reaching for a credential it should not have.
PROFILE = os.environ.get("SETUHAUL_AWS_PROFILE")
MODEL_FAST = "apac.amazon.nova-micro-v1:0"
MODEL_DEEP = "apac.amazon.nova-pro-v1:0"

_session = (boto3.Session(profile_name=PROFILE, region_name=REGION) if PROFILE
            else boto3.Session(region_name=REGION))
_brt = _session.client("bedrock-runtime",
                       config=Config(retries={"max_attempts": 3, "mode": "adaptive"},
                                     read_timeout=30))

# ---------------------------------------------------------------- box 2: screen
# The verb and its object, with anything short in between, rather than one fixed phrasing.
# The first version matched "ignore previous instructions" but not "ignore YOUR instructions",
# which is a one-word edit. This is still a keyword screen and still fails open by design:
# anything it misses meets C1-C9 downstream, which is where the real guarantee lives.
_INJECTION = re.compile(
    r"\b(?:ignore|disregard|forget|override|bypass|skip)\b[^.]{0,40}?"
    r"\b(?:instructions?|rules?|polic(?:y|ies)|prompt|guidelines?|constraints?|checks?)\b"
    r"|\byou are now\b|\bsystem prompt\b|\bact as (?:an? )?(?:admin|administrator|developer|system)\b"
    r"|\b(?:new|updated|revised) instructions\b"
    r"|grant me|give me (?:any|the) slot (?:anyway|regardless)|pretend", re.I)
_PII = [(re.compile(r"\+?\d[\d\s-]{8,}\d"), "[phone]"),
        (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "[email]")]

def screen(text: str) -> tuple[str, list[str]]:
    flags, masked = [], text
    if _INJECTION.search(text): flags.append("G1_injection_attempt")
    for rx, repl in _PII:
        if rx.search(masked):
            masked = rx.sub(repl, masked); flags.append("G1_pii_masked")
    return masked, flags

# ---------------------------------------------------------------- box 3: dedupe
def dedupe_key(driver_id, shipment_id, text, now) -> str:
    import hashlib
    h = hashlib.sha1(re.sub(r"\s+", " ", text.strip().lower()).encode()).hexdigest()[:8]
    return f"{driver_id}-{shipment_id or 'UNKNOWN'}-{now:%Y%m%d}-{h}"

# ---------------------------------------------------------------- box 4: extract
_TOOL = {"toolSpec": {"name": "record_driver_facts",
    "description": "Record the facts stated in the driver's message.",
    "inputSchema": {"json": {"type": "object", "properties": {
        "intent": {"type": "string", "enum": ["ASK_OPTIONS","BOOK","REPORT_DELAY","CHECK_STATUS","UNKNOWN"]},
        "declared_eta_time": {"type": ["string","null"], "description": "HH:MM, 24h, or null"},
        "eta_range_end": {"type": ["string","null"], "description": "HH:MM if a range was given"},
        "relative_delay_min": {"type": ["integer","null"]},
        "confidence": {"type": "string", "enum": ["HIGH","MEDIUM","LOW"]},
        "earliest_acceptable_time": {"type": ["string","null"]},
        "latest_finish_time": {"type": ["string","null"]},
        "shipment_hint": {"type": ["string","null"]},
        "requested_dock": {"type": ["string","null"], "description":
            "A dock the driver asks for by code, e.g. D4. Null if they named none."},
        "reason_code": {"type": ["string","null"], "enum": ["TRAFFIC","BREAKDOWN","WEATHER",
                        "LATE_DEPARTURE","LOADING_DELAY","ROUTE_ISSUE","OTHER", None]},
    }, "required": ["intent","confidence"]}}}}

def _converse(model, system, messages, tools=None, max_tokens=400):
    kw = dict(modelId=model, system=[{"text": system}], messages=messages,
              inferenceConfig={"maxTokens": max_tokens, "temperature": 0.0})
    if tools: kw["toolConfig"] = {"tools": tools, "toolChoice": {"tool": {"name": tools[0]["toolSpec"]["name"]}}}
    t0 = time.time()
    r = _brt.converse(**kw)
    return r, int((time.time() - t0) * 1000)

def parse_hhmm(v):
    """Tolerant clock parser. Models return '11:20', '1120', '11 45', '9', '6:30 pm'. An
    unparseable value becomes None rather than an exception: a crash in box 4 would take out a
    driver's turn over a formatting quirk, and the plan ETA is a safe fallback."""
    if v is None: return None
    t = str(v).strip().lower()
    # A time field must contain a time, not a sentence. Digging digits out of prose invents a
    # constraint the driver never stated, which is worse than having none.
    if re.search(r"[b-ln-oq-z]", re.sub(r"\b(am|pm|a\.m|p\.m|hrs|hours?)\b", "", t)):
        return None
    pm = "pm" in t or "p.m" in t
    am = "am" in t or "a.m" in t
    digits = re.sub(r"[^0-9]", "", t)
    if ":" in t:
        a, b = t.split(":")[0], t.split(":")[1]
        h, m = re.sub(r"\D", "", a), re.sub(r"\D", "", b)[:2]
        if not h or not m: return None
        h, m = int(h), int(m)
    elif len(digits) == 4: h, m = int(digits[:2]), int(digits[2:])
    elif len(digits) == 3: h, m = int(digits[0]), int(digits[1:])
    elif len(digits) in (1, 2): h, m = int(digits), 0
    else: return None
    if pm and h < 12: h += 12
    if am and h == 12: h = 0
    if not (0 <= h <= 23 and 0 <= m <= 59): return None
    return h, m

def _norm(v):
    """Models sometimes emit the STRING 'null' for an absent value. Treat it as absent."""
    if isinstance(v, str) and v.strip().lower() in ("null", "none", "n/a", ""): return None
    return v

def extract(message: str, plan_hint: str) -> tuple[dict, int, dict]:
    r, ms = _converse(MODEL_FAST, prompts.EXTRACT_SYSTEM,
        [{"role": "user", "content": [{"text": f"Driver message:\n{message}\n\nCurrent plan:\n{plan_hint}"}]}],
        tools=[_TOOL])
    out = {}
    for blk in r["output"]["message"]["content"]:
        if "toolUse" in blk: out = blk["toolUse"]["input"]
    return {k: _norm(v) for k, v in out.items()}, ms, r.get("usage", {})

# ---------------------------------------------------------------- box 6: the ETA rule (D3, in code)
def apply_eta_rule(facts: dict, original_eta: datetime, known_eta: datetime | None = None,
                   known_conf: str = "HIGH") -> tuple[datetime, str, str]:
    """The model reports what was SAID. This decides what it MEANS. Deterministic."""
    # Confidence is decided HERE, not by the model. The rule is deterministic and matches every
    # confidence_code in the supplied data: an explicit clock time is HIGH; a relative delay alone
    # is LOW because the driver never states the reference point; a range is LOW.
    conf = facts.get("confidence") or "MEDIUM"
    def at(v):
        hm = parse_hhmm(v)
        if hm is None: return None
        return original_eta.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
    end, start = at(facts.get("eta_range_end")), at(facts.get("declared_eta_time"))
    if end is not None and start is not None and end > start:
        return end, "LOW", f"range given; using the later end {end:%H:%M}"
    if start is not None:
        return start, "HIGH", f"driver stated {start:%H:%M}"
    if facts.get("relative_delay_min") is not None:
        eta = original_eta + timedelta(minutes=int(facts["relative_delay_min"]))
        return eta, "LOW", (f"{facts['relative_delay_min']} min after the planned "
                            f"{original_eta:%H:%M}; no arrival time given, so treated as uncertain")
    # Nothing declared THIS turn. Fall back to what the driver already told us earlier in the
    # thread, not to the seeded plan - otherwise a driver who reports an hour's delay and then
    # says anything else is silently offered a dock before they can arrive.
    if known_eta is not None and known_eta != original_eta:
        return known_eta, known_conf, (f"nothing new this turn; using the {known_eta:%H:%M} "
                                       f"the driver gave earlier")
    return original_eta, "HIGH", "no change declared; using the plan"

# ---------------------------------------------------------------- box 11: output checks
_CONFIRM = re.compile(r"\bconfirm(ed|ation)?\b", re.I)
# C4 bans ASSERTING confirmation, not the word itself. "waiting for warehouse to accept" and
# "pending confirmation" are the honest phrasings and must survive, or a status answer about a
# PENDING_CONFIRMATION booking can never be written.
_NOT_YET = re.compile(r"\b(pending|waiting|awaiting|await|not|yet|once|after|when|unconfirmed|"
                      r"to be|will be|still|before)\b[^.]{0,16}$", re.I)
_TIME = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_DOCK = re.compile(r"\bD([1-9])\b")
# OPTIONS is a shortlist of the feasible chains, never the whole board. A reply that
# generalises from it ("all slots are free") is false even when every dock and time it
# names is in OPTIONS, so C1/C2 cannot catch it. C8 does.
_OVERCLAIM = re.compile(
    r"\b(all|every)\b[^.]{0,30}?\b(slots?|docks?)\b[^.]{0,30}?\b(free|open|available|empty)\b"
    r"|\b(slots?|docks?)\b[^.]{0,25}?\b(all|every)\b[^.]{0,25}?\b(free|open|available)\b"
    r"|\beverything\s+is\s+(free|open|available)\b", re.I)
# "all three slots are open" is scoped to what was offered and is true; "all slots are free"
# is not. The difference is a word that ties the quantifier to the list, so a match that
# carries one is allowed through. Without this C8 escalates correct answers.
_SCOPED = re.compile(r"\bD[1-9]\b|\b(these|those|both|two|three|above|listed|shown|"
                     r"offered|here|\d+)\b", re.I)

def output_checks(reply: str, options: list, status: str, held_slot_labels: set) -> list[str]:
    fails = []
    allowed_times = {t for o in options for t in (f"{o.start:%H:%M}", f"{o.end:%H:%M}")}
    allowed_docks = {o.dock_code for o in options}
    for m in _DOCK.finditer(reply):
        if f"D{m.group(1)}" not in allowed_docks: fails.append(f"C1 dock D{m.group(1)} not in options")
    for m in _TIME.finditer(reply):
        hhmm = f"{int(m.group(1)):02d}:{m.group(2)}"
        if hhmm not in allowed_times: fails.append(f"C2 time {hhmm} not in options")
    if not options and (_DOCK.search(reply) or _TIME.search(reply)):
        fails.append("C3 named a slot when no option was offered")
    if status != "CONFIRMED":
        for m in _CONFIRM.finditer(reply):
            if not _NOT_YET.search(reply[:m.start()]):
                fails.append(f"C4 said 'confirmed' while status is {status}")
                break
    if re.search(r"\[phone\]|\[email\]", reply): fails.append("C7 echoed masked personal data")
    m = _OVERCLAIM.search(reply)
    if m and not _SCOPED.search(m.group(0)):
        fails.append("C8 claimed availability beyond the options given")
    # A dock code with no time cannot be acted on. If we offered slots, the reply must carry
    # every distinct start time we offered.
    for st in {f"{o.start:%H:%M}" for o in options}:
        if st not in reply:
            fails.append(f"C9 offered a slot starting {st} without naming the time")
    return fails

def draft_reply(context: str, options: list, status: str) -> tuple[str, int, list[str]]:
    opt_txt = "\n".join(f"- {o.dock_code} {o.start:%H:%M} to {o.end:%H:%M}" for o in options) or "(none)"
    r, ms = _converse(MODEL_FAST, prompts.REPLY_SYSTEM,
        [{"role": "user", "content": [{"text": f"SITUATION:\n{context}\n\nOPTIONS:\n{opt_txt}\n\n"
                                               f"BOOKING STATUS: {status}\n\nWrite the driver's message."}]}])
    text = "".join(b.get("text", "") for b in r["output"]["message"]["content"]).strip()
    return text, ms, output_checks(text, options, status, set())

def clarify(question_about: str, message: str) -> tuple[str, int]:
    r, ms = _converse(MODEL_FAST, prompts.CLARIFY_SYSTEM,
        [{"role": "user", "content": [{"text": f"The driver wrote: {message}\n\n"
                                               f"You need to know: {question_about}"}]}], max_tokens=120)
    return "".join(b.get("text","") for b in r["output"]["message"]["content"]).strip(), ms


# ---------------------------------------------------------------- the pipeline
def _group_failures(cands):
    from collections import Counter, OrderedDict
    counts, example = Counter(), {}
    for c in cands:
        for f in c.failures:
            code = f.split(" ")[0]
            counts[code] += 1
            example.setdefault(code, f)
    return [{"check": k, "slots_rejected": v, "example": example[k]}
            for k, v in counts.most_common()]

def escalation_record(con, ctx, cands, now, reason):
    """D10. Every field the coordinator needs, including why each slot was rejected."""
    contacts = con.execute(
        "SELECT contact_id,contact_name,email,contact_role FROM facility_contacts "
        "WHERE facility_id=? AND active_flag=1", (ctx.facility_id,)).fetchall()
    reachable = [c for c in contacts if c[2]]
    known = {c[2] for c in contacts if c[2]}
    attempts = con.execute(
        "SELECT operational_message_id,recipient_address,delivery_status FROM operational_messages "
        "WHERE shipment_id=? AND channel='EMAIL'", (ctx.shipment_id,)).fetchall()
    prior = [{"message": a[0], "recipient": a[1], "status": a[2],
              "recipient_known": a[1] in known} for a in attempts]
    unknown = [a for a in prior if not a["recipient_known"]]
    undelivered = [a for a in prior if a["status"] in ("QUEUED", "FAILED")]
    blocker = None
    if not reachable:
        blocker = "no contact at this facility has an email address - audit D10"
    elif unknown:
        blocker = (f"previous escalation for this shipment went to {unknown[0]['recipient']}, "
                   f"which matches no contact on file - audit D10. Route it to a named contact.")
    elif undelivered:
        blocker = (f"previous escalation {undelivered[0]['message']} is still "
                   f"{undelivered[0]['status']} - nobody has actually been asked - audit D11.")
    return {
        "escalation_id": f"ESC-{uuid.uuid4().hex[:8].upper()}",
        "raised_at": ts(now), "shipment_id": ctx.shipment_id, "thread_id": ctx.thread_id,
        "facility_id": ctx.facility_id, "priority": ctx.priority_code,
        "declared_eta": ts(ctx.effective_eta), "confidence": ctx.confidence,
        "ready_at": ts(ctx.ready_at), "required_dock_type": ctx.required_dock_type,
        "unload_minutes": ctx.expected_unload_min, "reason": reason,
        "slots_considered": len(cands),
        # grouped by predicate, with one worked example each - a coordinator needs to see WHY,
        # not a wall of slot ids
        "rejections": _group_failures(cands),
        "contacts_on_file": [{"id": c[0], "name": c[1], "role": c[3],
                              "email": c[2] or None} for c in contacts],
        "reachable_contacts": len(reachable),
        # Having contacts on file is not the same as the escalation ROUTE working. Check what
        # previous escalation attempts for this shipment actually did (audit D10).
        "prior_escalation_attempts": prior,
        "BLOCKER": blocker,
    }

class _Shown:
    """Minimal option shape so output_checks can police a status reply like any other."""
    __slots__ = ("dock_code", "start", "end")
    def __init__(self, dock_code, start, end):
        self.dock_code, self.start, self.end = dock_code, start, end

def remember_turn(con, *, shipment_id, driver_id, thread_id, facts, eta, conf,
                  early, latest, dedupe, now) -> bool:
    """Persist what the driver declared, so the next turn in the thread starts from it.

    Without this the agent is stateless per turn: a driver who reports an hour's delay and
    then says only "D4 please" gets slots computed from the ORIGINAL plan, i.e. offered a
    dock before they can arrive. Writes nothing when the driver declared nothing.
    """
    declared = (facts.get("declared_eta_time") or facts.get("relative_delay_min") is not None)
    if not (declared or early or latest):
        return False
    try:
        if declared:
            con.execute(
                "INSERT INTO eta_updates(eta_update_id,shipment_id,source_type,"
                "reported_by_driver_id,declared_eta_ts,confidence_code,delay_reason_code,"
                "note,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (f"ETA-{uuid.uuid4().hex[:8]}", shipment_id, "DRIVER_DECLARED", driver_id,
                 ts(eta), conf, facts.get("reason_code"), "declared in conversation", ts(now)))
        if early or latest:
            con.execute(
                "INSERT INTO driver_exceptions(exception_id,shipment_id,driver_id,thread_id,"
                "exception_type,reported_at,reported_delay_min,declared_eta_ts,"
                "earliest_acceptable_ts,latest_acceptable_ts,severity_code,exception_status,"
                "description,dedupe_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"EXC-{uuid.uuid4().hex[:8]}", shipment_id, driver_id, thread_id,
                 facts.get("reason_code") or "UNKNOWN", ts(now),
                 facts.get("relative_delay_min"), ts(eta) if declared else None,
                 ts(early) if early else None, ts(latest) if latest else None,
                 "MEDIUM", "OPEN", "constraint stated in conversation", dedupe))
        con.commit()
        return True
    except Exception:
        con.rollback()      # a concurrent duplicate won the unique index; harmless
        return False

def handle(con, *, message, driver_id, thread_id, now, shipment_id=None, intent_override=None):
    """One driver turn through all eleven boxes. Returns a transcript record."""
    rid = f"REQ-{uuid.uuid4().hex[:8]}"
    rec = {"request_id": rid, "driver_id": driver_id, "thread_id": thread_id,
           "message": message, "boxes": [], "route": None, "reply": None, "t0": time.time()}
    def box(n, name, note, ms=None):
        rec["boxes"].append({"n": n, "name": name, "note": note, "ms": ms})

    masked, flags = screen(message)
    box(2, "screen", "clean" if not flags else ", ".join(flags))
    dk = dedupe_key(driver_id, shipment_id, message, now)
    prior = con.execute("SELECT request_id,route,reply FROM request_log WHERE dedupe_key=?",
                        (dk,)).fetchone()
    if prior:
        box(3, "dedupe", f"DUPLICATE of {prior[0]} - replayed, nothing re-booked")
        rec["route"], rec["reply"], rec["duplicate_of"] = prior[1], prior[2], prior[0]
        audit(con, request_id=rid, box="3", decision="duplicate_replayed",
              thread_id=thread_id, shipment_id=shipment_id, detail={"dedupe_key": dk}, now=now)
        rec["ms"] = int((time.time() - rec["t0"]) * 1000); return rec
    box(3, "dedupe", "first time seen")
    if "G1_injection_attempt" in flags:
        rec["route"] = "human"; rec["reply"] = ("I can't action that request here. I've passed this "
            "conversation to a SetuHaul coordinator, who will reply shortly.")
        box(11, "reply", "refused: injection attempt, handed to a human")
        audit(con, request_id=rid, box="2", decision="refused_injection",
              thread_id=thread_id, actor="system", now=now)
        rec["ms"] = int((time.time() - rec["t0"]) * 1000); return rec

    # box 5: resolve
    if shipment_id is None:
        rows = con.execute("SELECT shipment_id,destination_facility_id,original_eta_ts FROM shipments "
                           "WHERE driver_id=? AND current_status IN ('ASSIGNED','IN_TRANSIT','WAITING') "
                           "ORDER BY original_eta_ts", (driver_id,)).fetchall()
        if len(rows) == 0:
            box(5, "resolve", "no active shipment for this driver")
            rec["route"] = "human"; rec["reply"] = ("I can't find an active trip for you right now. "
                "A coordinator is checking and will come back to you.")
            rec["ms"] = int((time.time() - rec["t0"]) * 1000); return rec
        if len(rows) > 1:
            opts = ", ".join(f"{r[0]} to {r[1]} ({dt_(r[2]):%H:%M})" for r in rows)
            box(5, "resolve", f"AMBIGUOUS - {len(rows)} active shipments: {opts}")
            q, ms = clarify(f"which of these two trips they mean: {opts}", masked)
            box(11, "clarify", "asked which shipment", ms)
            rec["route"] = "ask"; rec["reply"] = q
            audit(con, request_id=rid, box="5", decision="ambiguous_shipment",
                  thread_id=thread_id, detail={"candidates": [r[0] for r in rows]}, now=now)
            rec["ms"] = int((time.time() - rec["t0"]) * 1000); return rec
        shipment_id = rows[0][0]
        box(5, "resolve", f"bound to {shipment_id} (only active shipment)")
    else:
        box(5, "resolve", f"bound to {shipment_id} (from the thread)")

    from .capacity import load_ctx
    ctx = load_ctx(con, shipment_id, thread_id=thread_id, now=now)
    plan = (f"Shipment {shipment_id}, planned arrival {ctx.effective_eta:%H:%M}, "
            f"{ctx.required_dock_type.lower()} dock, {ctx.expected_unload_min} min unload.")

    facts, ms, usage = extract(masked, plan)
    box(4, "extract", f"intent={facts.get('intent')} "
        f"eta={facts.get('declared_eta_time') or facts.get('relative_delay_min')} "
        f"early={facts.get('earliest_acceptable_time')} latest={facts.get('latest_finish_time')}", ms)

    orig = con.execute("SELECT original_eta_ts FROM shipments WHERE shipment_id=?",
                       (shipment_id,)).fetchone()[0]
    eta, conf, why = apply_eta_rule(facts, dt_(orig), ctx.effective_eta, ctx.confidence)
    ctx.effective_eta, ctx.confidence = eta, conf
    def _at(v, after=None):
        hm = parse_hhmm(v)
        if hm is None: return None
        t = eta.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
        # A deadline or a window that lands before the driver even arrives is a 12-hour clock
        # written without am/pm ("leave before 1:30"). Shift it, don't accept the impossible.
        if after is not None and t <= after and hm[0] < 12:
            t = t.replace(hour=hm[0] + 12)
        return t
    early = _at(facts.get("earliest_acceptable_time"))
    latest = _at(facts.get("latest_finish_time"), after=eta)
    # A window that closes before it opens cannot be satisfied by any slot, so the feasibility
    # pass would return 0 of 79 and the driver would be told there is no capacity tonight.
    # That is a misreading, not a shortage. Ask which limit is real instead of escalating.
    if early is not None and latest is not None and latest <= early:
        box(6, "eta", f"contradictory window: not before {early:%H:%M}, finished by "
                      f"{latest:%H:%M}")
        q, ms = clarify(f"which of these two is right, since they cannot both be - they want "
                        f"a slot no earlier than {early:%H:%M}, but also to finish unloading "
                        f"by {latest:%H:%M}", masked)
        box(11, "clarify", "asked which limit is real", ms)
        rec["route"] = "ask"; rec["reply"] = q
        audit(con, request_id=rid, box="6", decision="contradictory_window",
              shipment_id=shipment_id, thread_id=thread_id,
              detail={"earliest": ts(early), "latest": ts(latest)}, now=now)
        rec["ms"] = int((time.time() - rec["t0"]) * 1000); return rec
    if early is not None:  ctx.earliest_acceptable = early
    if latest is not None: ctx.latest_acceptable = latest
    box(6, "eta", f"{eta:%H:%M} {conf} -> ready {ctx.ready_at:%H:%M} ({why})")

    # The driver said this out loud; the next turn must not re-derive from the original plan.
    # load_ctx already reads eta_updates (via v_latest_eta) and driver_exceptions - nothing
    # was ever writing them, so every turn restarted from the seeded ETA.
    remember_turn(con, shipment_id=shipment_id, driver_id=driver_id, thread_id=thread_id,
                  facts=facts, eta=eta, conf=conf, early=early, latest=latest,
                  dedupe=dk, now=now)

    intent = intent_override or facts.get("intent") or "UNKNOWN"

    # CHECK_STATUS is a question about an existing booking, not a request for new slots.
    # The extractor has always returned it; until now the router collapsed everything that
    # was not BOOK into "here are three slots", so "Am I free now" got an options list.
    said_something = any(facts.get(k) is not None for k in
                         ("declared_eta_time", "relative_delay_min", "earliest_acceptable_time",
                          "latest_finish_time", "requested_dock"))
    if intent == "UNKNOWN" and not said_something:
        # The model could not tell what was being asked and the message carries no facts.
        # Dumping three slots at it is how "Am I free now" got an options list.
        box(7, "route", "intent UNKNOWN and no facts stated - asking rather than offering")
        q, ms = clarify("whether they want to see options, book a slot, or check the booking "
                        "they already have", masked)
        box(11, "clarify", "asked what they need", ms)
        rec["route"] = "ask"; rec["reply"] = q
        audit(con, request_id=rid, box="7", decision="asked_intent", shipment_id=shipment_id,
              thread_id=thread_id, now=now)
        rec["ms"] = int((time.time() - rec["t0"]) * 1000); return rec

    if intent == "CHECK_STATUS":
        # A chain books one row per slot: the primary APT-x (is_current=1) plus APT-x-O1
        # overflow rows. Span the whole chain, or the driver is told a 60-minute window
        # for a 120-minute unload.
        appt = con.execute(
            "SELECT a.appointment_id,a.appointment_status,d.dock_code,"
            "       MIN(sl.slot_start_ts),MAX(sl.slot_end_ts) "
            "FROM appointments a "
            "JOIN appointment_slots sl ON sl.slot_id=a.slot_id "
            "JOIN docks d ON d.dock_id=sl.dock_id "
            "WHERE a.shipment_id=? AND a.appointment_status IN "
            "      ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS') "
            "  AND (a.is_current=1 OR a.appointment_id LIKE ("
            "      SELECT p.appointment_id FROM appointments p WHERE p.shipment_id=a.shipment_id "
            "      AND p.is_current=1 LIMIT 1) || '-O%') "
            "GROUP BY d.dock_code", (shipment_id,)).fetchone()
        box(7, "status", f"{appt[1]} on {appt[2]}" if appt else "no current appointment")
        if appt:
            st, en = dt_(appt[3]), dt_(appt[4])
            opts = [_Shown(appt[2], st, en)]
            ctxt = (f"The driver is asking about their existing booking, not for new slots. "
                    f"They have dock {appt[2]} from {st:%H:%M} to {en:%H:%M}. Its status is "
                    f"{appt[1]}. Tell them that and nothing else. Do not offer other slots.")
            status = appt[1]
        else:
            opts, status = [], "NOT_RESERVED"
            ctxt = ("The driver is asking whether they have a dock booked. They do NOT - nothing "
                    "is held or booked for them. Say that plainly, then say they can ask for "
                    "options and you will show what fits. Do not name any dock or time.")
        text, ms, fails = draft_reply(ctxt, opts, status)
        box(11, "reply", f"status answer; checks {'passed' if not fails else fails}", ms)
        rec["route"] = "auto" if not fails else "human"
        rec["reply"] = text if not fails else ("I've passed this to a SetuHaul coordinator so you "
                                               "get an accurate answer rather than a guess.")
        rec["checks"] = fails
        audit(con, request_id=rid, box="7", decision="status_answered", shipment_id=shipment_id,
              thread_id=thread_id, detail={"appointment": appt[0] if appt else None}, now=now)
        rec["ms"] = int((time.time() - rec["t0"]) * 1000); return rec

    all_c = candidates(con, ctx, now)
    ok = rank_options([c for c in all_c if c.ok])
    box(7, "feasible", f"{len(ok)} of {len(all_c)} slot chains pass F1-F10")

    # A dock the driver names is a preference, never a relaxation: filter the feasible set,
    # never add to it. If nothing feasible is on that dock, say so and offer the rest.
    want = (facts.get("requested_dock") or "").strip().upper()
    dock_note = None
    if want and re.fullmatch(r"D[1-9]", want):
        on_dock = [c for c in ok if c.dock_code == want]
        if on_dock:
            ok = on_dock + [c for c in ok if c.dock_code != want]
            box(8, "prefer", f"driver asked for {want}: {len(on_dock)} feasible there, ranked first")
        else:
            dock_note = (f"The driver asked for dock {want}. Nothing on {want} fits their load "
                         f"today, so these are on other docks. Say that in one short clause.")
            box(8, "prefer", f"driver asked for {want}: nothing feasible there, offering others")

    if not ok:
        esc = escalation_record(con, ctx, all_c, now, "no feasible slot after all constraints")
        rec["escalation"] = esc
        box(8, "rank", "nothing to rank")
        blockers = "; ".join(r["example"] for r in esc["rejections"][:3])
        text, ms, fails = draft_reply(
            f"There is NO slot we can offer {shipment_id} tonight and nothing is pending or being "
            f"held. Every option was ruled out: {blockers}. A SetuHaul coordinator now owns this "
            f"case (reference {esc['escalation_id']}) and will contact the driver directly. "
            f"Tell the driver plainly that we cannot give a time, say why in one short phrase, "
            f"and say a person will call. Do not imply anything is being arranged automatically.",
            [], "ESCALATED")
        box(11, "reply", f"escalated; checks {'passed' if not fails else fails}", ms)
        rec["route"] = "human"; rec["reply"] = text; rec["checks"] = fails
        audit(con, request_id=rid, box="7", decision="escalated", shipment_id=shipment_id,
              thread_id=thread_id, detail=esc, now=now)
        rec["ms"] = int((time.time() - rec["t0"]) * 1000); return rec

    shown = ok[:3]
    if intent != "BOOK":
        box(9, "hold", "none taken - the driver is asking, not booking")
        status = "NOT_RESERVED"
        # What is true: these are the earliest-starting chains that passed F1-F10, not the
        # whole board. Say that, rather than leaving the model to invent a quantifier.
        ctxt = (f"These are the {len(shown)} EARLIEST dock slots that fit this driver's load - "
                f"its dock type, its unload time and its deadline. Tell the driver that in one "
                f"short sentence: these are the earliest that fit. Other slots exist later in "
                f"the day; these are the soonest. Give each one as dock code AND both times, "
                f"exactly as written in OPTIONS - a dock code with no time is useless to a "
                f"driver. Write 'your load', never the internal shipment code. "
                f"NONE of these {len(shown)} is reserved yet - write that as 'none of these "
                f"is reserved', never as 'all slots are not reserved' - "
                f"say so once, for them together, and ask the driver to reply with the one they "
                f"want so we can hold it. Do not single any one out as unreserved, because that "
                f"implies the others are. Never say or imply that all slots, every slot, or the "
                f"dock board generally is free or open - that is false and it will be rejected."
                + (f" {dock_note}" if dock_note else ""))
    else:
        best = shown[0]
        got = claim(con, best.slot_ids, thread_id, shipment_id, now); con.commit()
        box(9, "hold", f"{best.label} held for 120s" if got else f"{best.label} just taken by another driver")
        if not got:
            shown = shown[1:] or []
            status = "NOT_RESERVED"
            ctxt = "The slot the driver wanted was taken while we were talking. Offer the next one."
        else:
            try:
                appt, created = commit_booking(con, ctx, best, now)
                status = "PENDING_CONFIRMATION"
                box(10, "commit", f"{appt} {'created' if created else 'replayed (duplicate request)'}")
                shown = [best]
                ctxt = (f"{shipment_id} now has {best.dock_code} from {best.start:%H:%M} to "
                        f"{best.end:%H:%M}, held and sent to the warehouse to accept.")
            except LostRace as e:
                status = "NOT_RESERVED"; shown = ok[1:3]
                box(10, "commit", f"lost the race: {e}")
                ctxt = "That slot went while we were talking. Offer the next one."

    text, ms, fails = draft_reply(ctxt, shown, status)
    box(11, "reply", f"checks {'passed' if not fails else 'FAILED ' + str(fails)}", ms)
    if fails:
        rec["route"] = "human"
        rec["reply"] = ("I've passed this to a SetuHaul coordinator to answer, so you get an "
                        "accurate time rather than a guess.")
    else:
        rec["route"] = "auto"; rec["reply"] = text
    rec["checks"] = fails; rec["options"] = [o.label for o in shown]
    audit(con, request_id=rid, box="11", decision=rec["route"], shipment_id=shipment_id,
          thread_id=thread_id, detail={"options": rec["options"], "checks": fails}, now=now)
    try:
        con.execute("INSERT INTO request_log(dedupe_key,request_id,thread_id,shipment_id,route,"
                    "reply,handled_at) VALUES(?,?,?,?,?,?,?)",
                    (dk, rid, thread_id, shipment_id, rec["route"], rec["reply"], ts(now)))
        con.commit()
    except Exception:
        con.rollback()        # a concurrent duplicate won the insert; harmless
    rec["ms"] = int((time.time() - rec["t0"]) * 1000)
    return rec
