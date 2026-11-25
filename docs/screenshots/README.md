# QA screenshots

Captured by driving the interface in a real browser on 25 Nov 2026 — first against the local
service, then against the deployed stack. Two of the defects recorded in the commit history were
found here and nowhere else: duplicate CORS headers (which curl reported as 200) and state split
across Lambda containers.

| file | what it shows |
|---|---|
| qa-01 | Driver channel, nothing sent. Header confirms the API is reachable. |
| qa-02 | Booking, escalation and injection in one thread, with routes and latencies. |
| qa-03 | The eleven-box trace expanded on the escalated turn. |
| qa-04 | The hand-off record, including the BLOCKER naming the unreachable escalation address. |
| qa-05 | Coordinator dock board: open, held, booked, and dock-down slots across both facilities. |
| qa-06 | Audit trail with the timestamp column fixed and the stored decision detail. |
| qa-07 | "Why this answer" for SHP1015: 0 of 79 feasible, every candidate with its refusing rule. |
| qa-08 | The deployed site failing to reach the backend — the duplicate CORS header bug. |
| qa-09 | The same page after the fix, connected to Lambda. |
