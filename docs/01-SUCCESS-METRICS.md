# SetuHaul — Success metrics
Defined BEFORE architecture. Every metric names its measurement source and its pass line.
Snapshot under test: 4 Aug 2026, Asia/Kolkata. Declared clock for "now": 2026-08-04T10:05:00+05:30 (see §4).

## 0. North star

**Promise Integrity Rate (PIR) = promises kept / promises made = 100%.**

A "promise" is any statement to a driver that names a dock and a time. If the system says it,
the facility honours it. PIR is a GATE, not a target: one broken promise fails the build.
Rationale: the driver's enemy is not delay, it is a retracted promise.

## 1. The brief's nine measures (we are scored on these)

| # | Measure | Our definition | Source of truth | Pass line |
|---|---|---|---|---|
| M1 | Time to usable outcome | first driver msg -> option accepted or escalated | scenario transcript timestamps | p50 <= 3 turns, <= 60s |
| M2 | Resolved without manual takeover | cases closed with no human action | scenario set count | >= 70% (6 of 7 ladder cases; SHP1015 must escalate) |
| M3 | Conflicting/duplicate allocations | double-bookings created under load | integrity test | **0, hard gate** |
| M4 | Options later found infeasible | shown options invalid at confirm time | re-check at commit | **0 silent failures**; re-offer path required |
| M5 | No-slot escalations correct | hand-off record completeness | escalation schema validator | 100% fields present |
| M6 | Driver waiting after reschedule | gate_in -> dock_in minutes | facility_checkins | report value; no invented target |
| M7 | Slot utilisation | slots used / slots open in window | v_slot_availability | report; must not fall vs baseline |
| M8 | Priority-policy violations | decisions contradicting stated policy | policy checker over decision log | **0** |
| M9 | Clarification turns | questions asked per resolved case | transcript | <= 2 avg; 0 for facts the DB holds |

M3, M4, M5, M8 are gates. M1, M2, M6, M7, M9 are reported numbers.

## 2. Agent-quality metrics (no RAG; corpus measured at 379 bytes)

| Metric | Definition | Pass line |
|---|---|---|
| Tool-selection accuracy | correct tool chosen per turn vs labelled set | >= 95% |
| Slot-feasibility precision | offered slots that are truly feasible | **1.00** (never offer an infeasible slot) |
| Slot-feasibility recall | feasible slots found / feasible slots existing (SQL ground truth) | >= 0.90 |
| Faithfulness | reply contains no dock/time absent from tool output | **1.00** (hard gate) |
| Escalation recall | no-feasible-slot cases escalated / cases that should | **1.00** (hard gate) |
| Clarification necessity | questions asked that the DB could not answer | >= 0.90 (penalises asking what we know) |
| Ambiguity detection | THR010-type cases where agent asks which shipment | **1.00** |
| Duplicate suppression | THR009 retry not double-processed | **1.00** |

Precision/recall are computed against a deterministic SQL oracle, not human judgement.
MRR and context-recall are NOT reported: there is no ranked document retrieval to rank.

## 3. Cost and latency

| Metric | Pass line |
|---|---|
| Cost per resolved exception | report measured USD; budget at 20-35 msgs in a 30-min spike |
| p95 turn latency | <= 5s |
| Tool calls per resolved case | <= 6 |

## 4. Stated assumptions (not derivable from the data)

1. **"Now" is undefined in the snapshot.** Check-ins stop at 09:25 but EXC005 is raised at 16:24.
   We declare now = 10:05 IST for the main scenario set and 16:30 for the SHP1015 escalation case.
   Every availability query is relative to a declared clock, never to wall time.
2. Slot hold TTL is a design choice, not a data fact. Initial value 120s, measured and defended.
3. Unload duration comes from shipments.expected_unload_min; we do not model labour variance.
