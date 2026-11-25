# SetuHaul — Decision register + edge cases
Answers the brief's 13 decisions. Every answer states the rule, the evidence, and the test that proves it.

## Core principle
Rules in code, judgement in the model. The LLM reads text and writes text.
It never decides feasibility, never ranks scarce capacity, never commits.

---

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
Deterministic, in this order:
  1. hard feasibility (binary gate)
  2. priority_code: CRITICAL > HIGH > NORMAL > LOW
  3. existing appointment commitment (a truck about to miss a CONFIRMED slot
     outranks a truck with no commitment)
  4. earliest effective ETA (ready time)
  5. earliest request timestamp (FIFO) - deterministic fairness tiebreak
Hard rules:
  - Physical early arrival does NOT jump the queue (SHP1003; brief: "early truck
    does not automatically win")
  - IN_PROGRESS work is NEVER preempted
  - A CONFIRMED booking is never displaced automatically - human approval only
Metric: priority-policy violations = 0, checked by a policy checker over the decision log.

### D8. (Optional) Scheduler objective
Objective, in plain words: minimise weighted driver waiting, then missed appointments,
then changes to a schedule already told to a driver (churn).
Start with a transparent dispatch rule; CP-SAT only if the rule is beaten measurably.

### D9. Stale options, cancellations, duplicates, retries
  duplicates  -> driver_exceptions.dedupe_key (driver-shipment-date-hhmm).
                 THR009 is a byte-identical retry of THR001: suppress, do not re-process.
  retries     -> every write carries an idempotency key = dedupe_key + action
  stale offer -> revalidated at commit; if lost, re-offer immediately with reason
  cancelled   -> SHP1019 is CANCELLED: answer the question, offer nothing
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
