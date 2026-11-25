# SetuHaul — runtime workflows
Five flows. Each names the boxes it uses and where it can refuse.
Workflow = predefined code path. Agent = chooses its own steps.
Only the clarification sub-loop is agentic. Everything else is a workflow. (Anthropic's test.)

## W1. ASK-ONLY  (THR011 / SHP1017)
  ingest -> screen -> dedupe -> extract(intent=ASK) -> resolve -> eta
  -> feasible -> rank -> REPLY WITH OPTIONS, NO HOLD, labelled "not reserved"
  Refuses at: ambiguous shipment -> W5. No capacity is touched. Ever.

## W2. REPORT DELAY + REBOOK  (THR001 / SHP1006 - the main path)
  ingest -> screen -> dedupe -> extract(intent=BOOK) -> resolve -> eta
  -> feasible -> rank -> HOLD top 2 (TTL 120s) -> present
  -> driver accepts -> REVALIDATE INSIDE TXN -> commit -> reply
  Refuses at: no feasible slot -> W4;  lost race -> W3;  missing facts -> W5

## W3. LOST THE RACE  (the brief's central figure)
  commit raises UNIQUE violation
  -> rollback -> do NOT retry blindly -> re-run feasible+rank
  -> if an option remains: offer it WITH the reason ("that slot was taken while
     we were talking; here is the next one")
  -> if none: W4
  The driver is never shown an error. The promise was never made, so none is broken.

## W4. NO FEASIBLE SLOT -> ESCALATE  (SHP1015 / THR005)
  build hand-off record: shipment, driver, ETA+confidence, EVERY failed F-check,
  every slot considered with its rejection reason, facility contact, transcript
  -> write decision_audit -> route to coordinator queue -> tell the driver plainly
     what happened, who has it now, and when they will hear back
  Never invents an option. Never says "someone will call" without a record existing.

## W5. MISSING OR AMBIGUOUS FACTS  (THR010 / THR003)
  the ONLY agentic loop: the model decides which question to ask next
  bounded: max 2 questions, and only from the D1 allowed-question set
  a question the database could answer is a BUG, caught by the clarification-necessity metric
  -> on answer: resume the interrupted workflow at its box
  -> on no answer within the window: W4

## Human-approval interrupts (can fire inside any workflow)
  new start after 21:00 (RULE005) | displacing a CONFIRMED booking |
  contradictory data | cancelled shipment | zero compatible capacity |
  penalty implications | >2 failed re-offers | anything safety-related
