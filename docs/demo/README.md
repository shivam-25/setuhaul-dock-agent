# Recorded demo

`setuhaul-dock-desk-demo.gif` — 32 frames captured by driving
<https://setuhaul-dock-desk.vercel.app> in a real browser against the deployed Lambda on
25 Nov 2026. Not a local run and not a mock-up; every latency in it is a real round trip.

Sequence: reset the day → ask with a constraint (three options, nothing reserved, 1081 ms) →
book explicitly (dock D2 held, 983 ms) → the evening reefer case with no feasible slot
(escalated, 1069 ms, 0 of 79 chains pass) → the decision trace expanded, including the blocker
naming the escalation address that matches no contact on file → a prompt-injection attempt
refused in 1 ms → the coordinator board → "why this answer" for SHP1015, every candidate with the
rule that refused it.

Still frames from the same run are in `../screenshots/live-*.jpg`.

**One caveat visible at the end of the run:** the audit trail reads empty. That is not a UI bug —
it is the split-container problem described in `docs/06-AWS-RUNBOOK.md` Part E. Two Lambda
execution environments each hold their own SQLite file in `/tmp` and answer alternate requests, so
the decisions recorded by one are invisible to the other. The audit trail works; this deployment
cannot keep one copy of it. See that section for the measurement and the two ways to fix it.
