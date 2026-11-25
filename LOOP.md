# LOOP.md — the build loop
No unit is "done" because it runs. A unit is done when it has produced its PROOF artefact.
Order is dependency order. Do not start a unit whose predecessor has no proof.

## The loop, for every unit
  BUILD -> PROVE -> RECORD -> STOP AND CHECK
  BUILD   write the smallest thing that could work
  PROVE   run the named test; capture real output, not a claim
  RECORD  write the number/artefact into docs/EVIDENCE.md with a timestamp
  CHECK   if the proof fails, fix before moving on. Never carry a red unit forward.

## Units

U0  DATA AUDIT                                  [deliverable 1]
    build  audit queries over the given snapshot, one query per defect
    prove  docs/AUDIT.md: each defect + the exact SQL + its output
    gate   >= 7 defects proven by query, none asserted without one
    note   the given SQLite file is READ-ONLY evidence. Never written to.

U1  SCHEMA + SEED (Postgres)
    build  import the snapshot into Postgres; add slot_holds + decision_audit
    prove  row counts match the source table-by-table; constraints verified present
    gate   zero row-count drift; partial unique indexes confirmed by \d output

U2  CAPACITY CORE (no model anywhere in this unit)
    build  feasibility F1..F9, ranking D7, hold manager, commit protocol
    prove  unit tests per predicate + a SQL oracle comparison
    gate   feasibility precision 1.00, recall >= 0.90 vs the oracle
    note   this is the heart. If time runs out, this is what must exist.

U3  INTEGRITY PROOF                              [deliverable 5]
    build  race harness: N=20 clients, one slot, same millisecond window
    prove  exactly 1 appointment row; 19 clean re-offers; 0 broken promises; 10 repeats
    gate   appointments_created == 1 on every repeat
    note   do U3 IMMEDIATELY after U2. It is a whole deliverable and it is cheap.

U4  CONVERSATION LAYER
    build  boxes 1-6 and 11: screen, dedupe, extract, resolve, eta, reply + C1-C8
    prove  the 7 ladder cases produce correct transcripts
    gate   faithfulness 1.00, escalation recall 1.00, ambiguity detection 1.00

U5  ESCALATION + AUDIT                           [feeds deliverables 4 and 6]
    build  hand-off record, coordinator queue, decision_audit writes
    prove  SHP1015 produces a complete hand-off record with every F-check reason
    gate   100% of hand-off fields present

U6  EVAL HARNESS                                 [evidence for the readout]
    build  labelled set (~60-100 cases), oracle labels derived BY QUERY, LangSmith traces
    prove  the metric table in docs/01 filled with measured numbers
    gate   every gate metric at its pass line; no metric reported as an estimate

U7  UIs
    build  driver chat + operations console (slot board, holds, queue, audit, approve)
    prove  a scripted scenario run through the real UI
    gate   all 7 "scenario run must show" items visible on screen

U8  SCENARIO RUN + READOUT                       [deliverables 4 and 6]
    build  recorded run ending in one escalation; plain-language client note
    prove  the recording exists and plays
    gate   REHEARSED OUT LOUD at least twice

## Hard rules
  - The given SQLite file is never modified. It is audit evidence.
  - Generated data lives in data/generated/ only, with a fixed seed, never merged in.
  - No metric is written down unless it was measured. Estimates are labelled ESTIMATE.
  - Any unit may be cut. U2 and U3 may not.
