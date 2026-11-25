# SetuHaul — Latency, throughput and scale budget
Performance is designed in, not tuned in. Every number below is a budget with an owner.

## 1. The real load (measured against the brief's operating profile)

| Quantity | Brief | Per second | Verdict |
|---|---|---|---|
| Loads coordinated / day | 280-360 | 0.004/s | trivial |
| Receiving appointments / day | 190-240 | 0.003/s | trivial |
| Exception messages, normal day | 15-25 | 0.0003/s | trivial |
| Exception messages, SPIKE | 20-35 in 30 min | **~0.02/s (1.2/min)** | trivial |
| Docks across all facilities | 32 | - | the scarce thing |

CONCLUSION: this system is NOT throughput-bound. One message per minute at peak.
A single process handles the whole company with 3 orders of magnitude of headroom.
We design for 10x the spike (12 msg/min) and still never need a queue for load reasons.

**The bottleneck is CONTENTION, not throughput.** Many readers, one slot.
Therefore the engineering goes into correctness under concurrency and into
per-turn latency, NOT into horizontal scale-out.

## 2. Latency budget (p95 per driver turn, target <= 5s, stretch 2.5s)

| Stage | Budget | How it is met |
|---|---|---|
| ingest + PII/injection screen | 150 ms | regex + small model, parallel |
| extract intent/ETA/constraints | 700 ms | ONE structured-output call, small model |
| resolve shipment + ETA + rules | 50 ms | 3 SQL queries issued in PARALLEL |
| feasibility search | **< 30 ms** | pure SQL, indexed, no model in the loop |
| rank + hold | 20 ms | deterministic, one short transaction |
| draft reply | 900 ms | small model; large model only on escalation |
| output checks | 100 ms | regex + set-membership against tool output |
| **total** | **~2.0 s** | 2.5x headroom to the 5 s gate |

Perceived latency is lower still: the reply streams token-by-token.

## 3. The eight optimisations, and why each is free

O1 **No model in the capacity lane.** Feasibility and ranking are SQL + code.
   This is the single largest latency win and it falls out of the architecture,
   not out of tuning. A model in this path would cost ~1 s AND be wrong sometimes.
O2 **One extraction call, not a chain.** Intent + ETA + constraints + shipment hint
   come back in a single structured output. Chains multiply latency and failure.
O3 **Parallel reads.** Shipment binding, latest ETA and facility rules are
   independent -> issued concurrently, cost = max(), not sum().
O4 **Model tiering.** Small/fast model for extract + draft; large model only for
   escalation summaries and ambiguous cases. Measured per scenario.
O5 **Facility-day cache.** 32 docks x ~14 slots = ~450 rows per facility-day.
   Held in memory, invalidated on every write. Reads never touch disk in the hot path.
O6 **Cache the language, never the capacity.** A repeated message (THR009 duplicates
   THR001) reuses the EXTRACTION result via dedupe_key. Availability is NEVER cached
   or reused - it is recomputed every time, because a cached slot is a broken promise.
   This is the one place where a cache would be actively harmful.
O7 **Short transactions, and never across a turn.** A DB transaction is opened only
   around validate+insert (~ms) and is NEVER held across an LLM call or while a
   human thinks. That is what the hold table is for.
O8 **Streaming replies** so time-to-first-token, not total time, is what the driver feels.

## 4. Concurrency design (the part that actually matters)

Invariant: one active appointment per slot, enforced by the DATABASE, not by code.
  - partial unique index on appointments(slot_id) WHERE status IN
    (PENDING_CONFIRMATION, CONFIRMED, IN_PROGRESS)   [already in the given schema]
  - partial unique index on slot_holds(slot_id) WHERE released=0 AND expires_at > now
Commit protocol:
  1. revalidate feasibility inside the transaction (not before it)
  2. INSERT appointment
  3. UNIQUE violation == "lost the race" -> rollback, re-rank, re-offer, tell the driver
     the truth. Never a bare error, never a silent retry loop.
Holds expire by TIME, so a crashed client cannot strand capacity.
No distributed lock, no queue, no leader election. The database is the arbiter.

**Scaling path:** scheduling is independent per facility, so the natural shard key is
facility_id. 6 facilities -> 6 independent contention domains. Nothing is global.

**SQLite caveat, stated honestly:** SQLite has a single writer. It will PASS the race
test, but it passes it by serialising, which is not proof for a multi-writer world.
The service therefore runs on Postgres seeded from the given snapshot; the snapshot
stays untouched as audit evidence. The same partial unique index is the arbiter in both.

## 5. What we will actually measure (no estimates in the readout)

  - p50 / p95 / p99 turn latency, per stage, from traces
  - tokens and USD per resolved exception
  - race test: N=20 concurrent, 10 repeats -> appointments created must equal 1
  - sustained 12 msg/min for 10 minutes (10x spike) -> no error, no latency drift
  - feasibility query time at 32 docks x 6 facilities on the generated scale fixture
