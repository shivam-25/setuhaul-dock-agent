# Evaluation

Three things are measured, each against an oracle that does not share the implementation's opinion.
Everything here is produced by a script in `scripts/`, and every number can be reproduced.

## 1. Feasibility — the number that must be perfect

`scripts/eval_feasibility.py` compares the Python capacity core against an **independent SQL
oracle** over 18 shipments × 4 clocks = **72 cases**, roughly 1,600 slot decisions.

| metric | result | gate |
|---|---|---|
| Precision | **1.0000** | 1.0 — never offer an infeasible slot |
| Recall | **1.0000** | ≥ 0.90 |
| True positives | 1,584 | |
| False positives / negatives | 0 / 0 | |

**Why precision is a gate and recall is not.** Missing a feasible slot disappoints a driver.
Offering an infeasible one breaks a promise. Only the first is recoverable.

**What this evidence is worth.** Two implementations I wrote agreeing rules out transcription
errors and off-by-one mistakes. It does **not** rule out a shared misreading of the brief, because
the same person read the brief twice. It is strong evidence of internal consistency, not of
correctness against SetuHaul's actual intent.

Getting to agreement surfaced three real bugs, which is the point of writing the oracle separately:

1. **SQLite's `datetime()` converts an offset-bearing timestamp to UTC and drops the offset**, so
   `'2026-08-04 04:10:00'` was string-compared against `'2026-08-04T09:00:00+05:30'`. Space sorts
   before `T`, so the comparison was always true and the duration check silently did nothing.
2. **A missing `LAST_NEW_START_TIME` rule defaulted to Jaipur's 21:00**, applying one facility's
   policy to another. It now falls back to that facility's own closing time.
3. **A missing `HEAVY_DOCK_REQUIRED_KG` rule made `weight <= NULL` evaluate to NULL**, and
   `NULL OR false` excluded the row — silently rejecting *every* Gurugram slot. Gurugram has one
   of the five rules Jaipur has, so this is a live governance gap, not a test artifact.

## 2. Extraction — four models on 44 labelled cases

`scripts/eval_extraction.py`. Labels are deterministic from the message text. Scoring is on what
the system consumes — the ETA **after** the deterministic confidence rule — not on raw model
fields, so two models that phrase the same fact differently both score correct.

| model | intent | eta | earliest | latest | all four | p50 | p95 |
|---|---|---|---|---|---|---|---|
| **nova-micro** | 0.841 | 0.955 | 0.977 | 0.977 | **0.773** | **843 ms** | 4032 ms |
| nova-lite | 0.818 | 0.932 | 1.000 | 1.000 | 0.750 | 910 ms | 3832 ms |
| nova-2-lite | **0.955** | 0.568 | 0.864 | 0.886 | 0.500 | 1119 ms | 3587 ms |
| nova-pro | 0.864 | 0.909 | 0.977 | 1.000 | 0.750 | 907 ms | 1859 ms |

**44 cases means one case is 2.3 points.** Differences under about five points are not meaningful
here, so micro / lite / pro are tied on "all four" and nova-2-lite is genuinely worse.

### Five-way intent accuracy fails the gate on every model — and that is a design signal

No model reaches 0.95 on intent. Scaling up does not fix it: nova-pro is no better than nova-micro.
Looking at the failures explains why. "Reaching around 11:20, any slot after 12?" is scored
REPORT_DELAY by the model and ASK_OPTIONS by my label, and **both readings are defensible** — the
driver did both. The metric was measuring an ambiguity in my labels as if it were a model error.

So the architecture changed rather than the model. The system no longer branches on five-way
intent. It asks **one** question, because only one thing is irreversible: *did the driver
explicitly ask to book?* A hold is taken only on an explicit booking request; everything else shows
options and reserves nothing.

### The number that actually decides the model

| model | BOOK precision | BOOK recall |
|---|---|---|
| **nova-micro** | **1.000** | 0.600 |
| nova-lite | **0.625** | 1.000 |
| nova-2-lite | 0.833 | 1.000 |
| nova-pro | 1.000 | 0.800 |

The costs are asymmetric. A false BOOK takes a hold and consumes capacity another driver could have
used — the exact failure this project exists to prevent. A missed BOOK costs one extra turn.

**nova-micro never books a driver who did not ask.** nova-lite does so in 37.5% of its BOOK calls,
which disqualifies it regardless of its better recall. nova-pro is equally safe with better recall,
but costs roughly 20× per token and is no faster at p50, so it earns its place only off the hot
path.

### Decision

| step | model | reason |
|---|---|---|
| Box 4 — extract | `apac.amazon.nova-micro-v1:0` | BOOK precision 1.000, fastest p50, closed tool schema |
| Box 11 — reply | `apac.amazon.nova-micro-v1:0` | content is already fixed; checks C1–C8 need the final token, so a slow drafter is pure latency |
| Escalation summary | `apac.amazon.nova-pro-v1:0` | off the hot path, where latency and cost stop mattering |

**Revisit if:** BOOK precision drops below 1.000 on a larger set; or extraction ETA accuracy falls
below 0.95; or real traffic contains languages the 44 cases do not. The move would be to **lite**
for the extra recall, not to pro.

## 3. No vector database, and no retrieval metrics

The policy corpus measures **379 bytes across 6 rows**. There is nothing to retrieve, so
**faithfulness is enforced rather than scored**: output checks C1–C8 reject any reply naming a dock
or a time that is not present in the tool output. A reply cannot be unfaithful and still be sent.

**MRR, context recall and context precision are deliberately not reported.** There is no ranked
document retrieval in this system, so those numbers would be decorative. Reporting a retrieval
metric for a system with no retrieval is worse than reporting nothing.

Retrieval gets revisited when any of these becomes true, and the `policy_lookup(facility_id, topic)`
tool seam already exists so it can be added without touching the agent, the prompts or this harness:

1. the policy corpus exceeds roughly 50 KB, or
2. facility rules stop fitting the `facility_rules` key/value shape, or
3. two facilities' policies conflict and need precedence, or
4. coordinators start asking free-text policy questions, or
5. a measured answer needs a document the database does not hold.

## 4. Behavioural evidence

`docs/TRANSCRIPTS.md` — nine cases generated by running the system, covering the ladder plus a
duplicate, an ambiguous driver and an adversarial message.
`docs/INTEGRITY_PROOF.json` — 20 concurrent claims on one slot, 10 repeats, one booking every time.

## What is still not measured

- Cost per resolved exception in money. Tokens are measured; the rupee figure is not stated because
  it would be a projection, not a measurement.
- Accuracy on Hindi or transliterated Hindi messages. The 44 cases are English.
- Whether coordinators accept the automation rate in practice.
