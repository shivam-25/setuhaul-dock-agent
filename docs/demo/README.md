# Demo media

All of it captured by driving <https://setuhaul-dock-desk.vercel.app> in a real browser against
the deployed Lambda on 25 Nov 2026. Not a local build, not a mock-up: every latency shown is a
real round trip.

| file | what it is |
|---|---|
| `setuhaul-explainer.mp4` | 2m49s narrated explainer, 1280×720. The argument, the measurements, and the limits. |
| `setuhaul-demo-clean.mp4` | 43s silent screen capture of the full QA run, 1280×720. |
| `setuhaul-demo.gif` | The same run as a GIF, for inline preview. |

## What the QA run covers

Every case on the ladder, in order, plus a duplicate and both coordinator views.

| # | case | result | latency |
|---|---|---|---|
| 1 | asks with a constraint (DRV006) | three options, **nothing reserved**, 11:30 correctly withheld | 1184 ms |
| 2 | vague delay, no reference time (DRV013) | LOW confidence, 30-minute buffer, asks which to hold | 1102 ms |
| 3 | urgent CRITICAL, 75-minute unload (DRV014) | offers contiguous 75-minute chains only | 905 ms |
| 4 | explicit booking (DRV012) | dock D2 held and committed | 1031 ms |
| 5 | evening reefer, nothing fits (DRV015) | escalated, 0 of 79 chains feasible, blocker reported | 1080 ms |
| 6 | ambiguous — two open trips (DRV004) | asks which trip, books nothing | 400 ms |
| 7 | prompt injection (DRV003) | refused before any model runs | **1 ms** |
| 8 | case 4 re-sent verbatim | `DUPLICATE, REPLAYED` — no second booking | 1 ms |

Coordinator side: the dock board across both facilities with open / held / booked / dock-down
states, the append-only audit log showing all eight decisions with their box numbers, and the
"why this answer" panel listing every candidate with the predicate that refused it.

## Two things the run exposes

**The header and the audit table disagree.** In `qa-live-08.jpg` the navigation bar reports
`0 audit rows` while the table below lists eight. Two Lambda execution environments, each with its
own SQLite file in `/tmp`, answered those two reads. This is the limitation in
`docs/06-AWS-RUNBOOK.md` Part E, caught on camera.

**The why-panel answers at the console's clock, not the escalation's.** Selecting SHP1015 while
the console sits at 10:05 reports `31 of 79 feasible`; the escalation happened at 16:30, where it
is `0 of 79`. That is the clock parameter working as designed — the snapshot contains no "now" —
but when demonstrating, set the clock to 16:30 first or the two numbers look contradictory.
