# SetuHaul — Decision register + edge cases
Answers the brief's 13 decisions. Every answer states the rule, the evidence, and the test that proves it.

## Core principle
Rules in code, judgement in the model. The LLM reads text and writes text.
It never decides feasibility, never ranks scarce capacity, never commits.

---


## Evidence index

The brief asks for an answer, a reason, and a named piece of evidence for each of the
thirteen. This is the evidence column; the reasoning is below.

| # | Decision | Evidence to bring | Where it is |
|---|---|---|---|
| 1 | What to collect before showing options | Three chats with the fields asked for | `docs/TRANSCRIPTS.md`, cases 1–3 |
| 2 | Linking a conversation to driver/shipment/appointment | A message-to-rows trace for an unclear case | `docs/TRANSCRIPTS.md` "ambiguous shipment"; box 5 in any `/api/chat` response |
| 3 | Setting the revised ETA and its confidence | The rule applied to two messages | `docs/TRANSCRIPTS.md`; box 6 shows the arithmetic and the buffer |
| 4 | What makes a slot feasible | A written check run on three shipments | `scripts/eval_feasibility.py` — precision 1.0000, recall 1.0000 against an independent SQL oracle |
| 5 | What "available" means while another driver decides | A definition and the state it needs | `slot_locks` + `claim()` in `src/setuhaul/capacity.py`; D5 below |
| 6 | Option → hold → request → confirmed | The states, who moves each, what the driver sees | D6 below; `decide_appointment()`; testing step 8 |
| 7 | Ordering simultaneous requests when capacity is short | A policy tested on competing trucks | `scripts/priority_test.py`, `docs/PRIORITY_POLICY_TEST.json` |
| 8 | *(Optional)* Facility recalculation and objective | A rule, an objective, and one replay | `scripts/schedule_test.py`, `docs/SCHEDULING_TOOL.json` |
| 9 | Stale options, cancellations, duplicates, retries | One replayed case for each | D9 below; the lapsed-commitment path, testing step 4 |
| 10 | No feasible slot exists | The escalation message and the hand-off record | SHP1015; `escalation_record()`; testing step 7 |
| 11 | Refusal wording | Two refusals in plain words | D11 below; testing steps 4 and 7 |
| 12 | What needs human approval | A list with the trigger for each | D12 below; `/api/alerts` ranks them live |
| 13 | Proving no double-booking | A concurrency test and its result | `scripts/race_test.py`, `docs/INTEGRITY_PROOF.json` — 20 contenders × 10 runs |

## PART 1 — THE 13 DECISIONS

### D1. What must be collected before showing options?
Ask the driver ONLY for what the database cannot hold:
  a. which shipment (only when the driver has >1 active assignment)
  b. revised ETA (only when not computable from the message)
  c. latest acceptable finish time (only when the driver implies a constraint)
  d. intent: see options, or book?
NEVER ask for: shipment id, current appointment, priority, dock compatibility,
weight, unload duration, whether the truck has arrived. All are in the DB.
Metric: clarification necessity >= 0.90.

### D2. How does a conversation link to driver / shipment / appointment?
driver_id -> shipments WHERE current_status IN (ASSIGNED, IN_TRANSIT, WAITING).
  1 match  -> bind thread.shipment_id
  >1 match -> ASK. Do not guess. (THR010 / DRV004 holds SHP1004 and SHP1020)
  0 match  -> check CANCELLED (SHP1019/THR012) -> explain, do not offer slots
Binding is written once and is immutable for the thread.

### D3. ETA rule and confidence

**Gate-in outranks any prediction.** An ETA is an estimate and earns a confidence buffer of
up to 30 minutes. A truck standing in the yard is not an estimate: `apply_arrival()` replaces
the predicted ETA with the observed `gate_in_ts` and sets confidence to HIGH, so the buffer
goes to zero. Buffering a truck that is physically present pushes it past slots it could
take. A truck already on a dock is a fixed task and is not offered slots at all.

| Driver says | Rule | Confidence |
|---|---|---|
| explicit clock time ("11:20") | use it | HIGH |
| relative delay ("90 minutes late") | original_eta + delay | HIGH |
| vague relative ("late by one hour") | original_eta + delay | LOW |
| range ("12:30 to 13:00") | take the LATE end | LOW |
| vague ("around two hours") | late end + buffer | LOW |
Buffer by confidence: HIGH 0 min, MEDIUM 15 min, LOW 30 min.
Never offer a slot starting before effective_eta + buffer.
Rationale: an early offer becomes a no-show. The buffer is the price of uncertainty,
and it errs toward the facility, which is the party that cannot absorb surprise.
Evidence: SHP1013 (LOW, "late by one hour"), SHP1017 (LOW, range 12:30-13:00).

### D4. What makes a slot feasible? (all 9 must pass; binary, in code)
F1 dock_type matches required_dock_type           (RULE003 reefer -> D5 only)
F2 weight <= dock limit                           (RULE004 >25000 kg -> HEAVY)
F3 slot not OCCUPIED / BLOCKED / CLOSED           (v_slot_availability)
F4 slot not HELD by another thread                (our hold layer)
F5 slot_start >= effective_eta + confidence buffer
F6 contiguous free minutes >= expected_unload_min  <-- see DEFECT-1
F7 within facility open/close AND start <= 21:00   (RULE005, else human approval)
F8 no overlapping dock_status_event               (breakdown / maintenance)
F9 finish <= driver's stated latest finish        (THR002: "leave before 1:30")
Feasibility is recomputed at offer time AND again inside the commit transaction.

### D5. What does "available" mean while another driver is deciding?
Three states, not two:
  OPEN   - no claim
  HELD   - exclusive, owned by one thread, TTL 120s, enforced in the database
  BOOKED - an active appointment exists
A hold is REAL capacity removal, not a UI label. Enforced by a partial unique index
on slot_holds(slot_id) WHERE released = 0 AND expires_at > now.
Without this, two readers see one free slot and the race in the brief's figure happens.

### D6. Hold vs request vs reservation vs confirmed
  Showing is not reserving. Reserving is not confirming.
  ASK-ONLY intent (THR011)      -> show options, NO hold, labelled "not reserved"
  BOOKING intent                -> hold top N options (N=2) for 120s
  driver accepts                -> commit: revalidate -> INSERT appointment
  warehouse confirmation needed -> PENDING_CONFIRMATION, expires in 15 min
  warehouse confirms            -> CONFIRMED  (the only state the driver is promised)
  truck docks                   -> IN_PROGRESS -> COMPLETED
The driver is told the true state at every step. Never "confirmed" before CONFIRMED.

### D7. Ordering simultaneous requests when capacity is short (THE policy)

**The first version of this answer was wrong, and the code proved it.** It specified a
deterministic sort over competing requests — priority, then commitment, then ETA, then
FIFO. That function (`rank_requests`) was written, tested by eye, and **never called once**,
because requests do not arrive as a list. They arrive one at a time, seconds apart. Sorting
a batch is the wrong shape for an online service. The function has now been deleted rather
than left to contradict this document.

The real question is narrower: *when this driver asks, may they take this slot, given who
else is waiting?*

**The policy.** A lower-priority truck may not take the **last** slot a higher-priority
truck could use, **while the lower-priority truck still has an alternative of its own.**

Both halves carry weight. Without the first, a CRITICAL reefer loses its only bay to a LOW
load that had four other choices. Without the second, we starve the driver actually in
front of us to protect one who may never ask.

**Where it runs.** `priority_gate()` filters the feasible set *before anything is shown*. A
slot withheld there was never offered, which is the only moment priority can be honoured
without taking a booking back. Re-allocating after a promise is a human decision — see the
brief's boundary, "whether a booking committed in the system of record".

**What never happens:** physical early arrival does not jump the queue; IN_PROGRESS work is
never preempted; a CONFIRMED booking is never displaced automatically.

**Evidence** — `scripts/priority_test.py`:

- On the brief's level-4 trucks (SHP1006, SHP1012, SHP1013, SHP1014, SHP1009): **0 policy
  violations**, and nothing withheld. The gate does not interfere when capacity is adequate.
- That alone is a **vacuous pass** — no truck in the snapshot is down to a single option, so
  the gate never engages and the test could not fail. The script therefore also manufactures
  the scarcity: with CRITICAL SHP1014 reduced to one feasible chain, **D4 13:00–14:00 is
  withheld from NORMAL SHP1013 and protected for SHP1014**.
- And when that same slot is also SHP1013's only option, it is **offered anyway**.

Every withheld slot is written to `decision_audit` as `priority_withheld`, carrying the slot,
who it was protected for, and both priorities — so "why did driver B not get 18:00?" is
answerable weeks later.

### D8. (Optional) Scheduler objective
Objective, in plain words: minimise weighted driver waiting, then missed appointments,
then changes to a schedule already told to a driver (churn).
Start with a transparent dispatch rule; CP-SAT only if the rule is beaten measurably.

### D9. Stale options, cancellations, duplicates, retries
  duplicates  -> dedupe_key = driver-shipment-YYYYMMDD-sha1(normalised text)[:8].
                 NOTE: keyed on the DAY, not the minute, and it ignores thread_id. So the
                 same wording from the same driver about the same shipment replays for the
                 rest of that day, across threads. That is right for a retry and wrong for
                 a driver who genuinely sends the same short message twice - a known
                 limitation, recorded rather than hidden.
                 THR009 is a byte-identical retry of THR001: suppress, do not re-process.
  retries     -> every write carries an idempotency key = dedupe_key + action
  stale offer -> revalidated at commit; if lost, re-offer immediately with reason
  cancelled   -> SHP1019 is CANCELLED: answer the question, offer nothing
  lapsed      -> a CONFIRMED window whose end time has passed while the truck is still in
                 the yard is NOT reported as confirmed. commitment_state() classifies every
                 commitment against the clock as upcoming / in_window / being_served /
                 lapsed. SHP1005 holds D3 09:00-10:00; at 10:05 the service used to answer
                 "confirmed", which was true of the row and false of the world.
Metric: duplicate suppression = 1.00.

### D10. No feasible slot exists
Escalate. Never invent. The hand-off record MUST contain:
  shipment, driver, declared ETA + confidence, every constraint that failed (F1-F9),
  the slots considered and why each was rejected, the facility contact, the thread
  transcript, and the time the driver was told.
Evidence: SHP1015 - reefer, D5 only, maintenance after new ETA 18:30, RULE005 blocks
a new start after 21:00. There is no answer. That case must escalate, by design.
Metric: escalation recall = 1.00; hand-off completeness = 100%.

### D11. Refusal wording when the preferred slot is not granted
Four parts, always: (1) what we could not do, (2) the real reason in plain words,
(3) what we did instead, (4) what happens next and who holds it now.
Never a bare "not available". A refusal without a reason costs trust.

### D12. What needs human approval or takeover
  - no feasible slot (D10)
  - a new unload start after 21:00 (RULE005)
  - displacing any CONFIRMED booking
  - contradictory data (driver says arrived, no gate-in row)
  - a request against a CANCELLED shipment
  - zero compatible capacity for reefer/heavy
  - penalty, compensation or customer-commitment implications
  - LOW-confidence ETA on a CRITICAL shipment
  - more than 2 failed re-offers in one thread
  - any safety-related decision

### D13. Proving no double-booking
A race test: N=20 clients attempt the SAME slot inside one millisecond window.
Expected: exactly 1 appointment row, 19 clean "lost the race" paths, 0 exceptions
surfaced to a driver as a broken promise, 19 re-offers made.
The partial unique index is the arbiter; the application turns the UNIQUE violation
into a graceful re-offer. Run against the real schema, recorded, and repeated 10x.

The index now also enforces this on Postgres, which is the engine the deployed service
uses: inserting a second active appointment onto a taken slot is refused by
`ux_active_appointment_per_slot`, verified directly against the live database. This matters
because the earlier SQLite demo could not have proved it - Lambda's /tmp is per execution
environment, so two warm containers held two different database files and could have sold
the same slot twice. No Lambda setting fixes that; reserved concurrency caps simultaneous
executions, not the number of warm environments. One shared Postgres does.

---

## PART 2 — DEFECTS FOUND IN THE DATA (each provable by query)

DEFECT-1  Slot grid vs unload duration. Slots are a fixed 60 min (90 for heavy), but
  4 of 21 shipments need 70-75 min on a STANDARD dock: SHP1005, SHP1009, SHP1014 (+15),
  SHP1002 (+10). An appointment binds to ONE slot_id, so these bookings are guaranteed
  to overrun into the next slot, which another driver can book independently.
  Two of the four are CRITICAL priority. 19% of loads. SYSTEMIC, not a scenario.
  Consequence: reserve a contiguous MINUTE RANGE; treat the grid as presentation.

DEFECT-2  No defined "now". Check-ins stop at 09:25; EXC005 is raised at 16:24.
  "What is available now" is undefined. We declare the clock as a parameter.

DEFECT-3  Unresolvable conversation. THR010 has shipment_id NULL; DRV004 holds two
  active shipments. Cannot be auto-resolved; must ask.

DEFECT-4  Duplicate exception. EXC009 duplicates EXC001 (identical dedupe_key).

DEFECT-5  Precision laundering. SHP1013 confidence LOW but ETA stored as exact 11:00.
  A vague claim is persisted as a precise fact.

DEFECT-6  Failed delivery treated as sent. OM004 delivery_status failed - a confirmation
  that never arrived must not count as a confirmation.

DEFECT-7  Missing contact. CON005 has no email; the escalation path has a hole exactly
  where it is needed.

---

## PART 3 — EDGE-CASE LADDER (maps to the brief's 6 levels)

L1 ask-only, no booking            THR011 / SHP1017    -> options shown, nothing held
L2 message missing key facts       THR003 / SHP1013    -> ask before calling a tool
L3 rules narrow the choice         SHP1010/1015/1016   -> feasibility is more than an empty row
L4 several trucks chase one slot   SHP1006/12/13/14    -> allocation policy applies
L5 world changes mid-conversation  SHP1005 / APT1008   -> revalidate, recover, re-offer
L6 no feasible slot                SHP1015 / THR005    -> escalate with a full record
L+ early + late + in-progress      SHP1002/03/04/06    -> facility objective, protected work
