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

## Three things the run exposes

**The header and the audit table disagree.** In `qa-live-08.jpg` the navigation bar reports
`0 audit rows` while the table below lists eight. Two Lambda execution environments, each with its
own SQLite file in `/tmp`, answered those two reads. This is the limitation in
`docs/06-AWS-RUNBOOK.md` Part E, caught on camera.

**The why-panel answers at the console's clock, not the escalation's.** Selecting SHP1015 while
the console sits at 10:05 reports `31 of 79 feasible`; the escalation happened at 16:30, where it
is `0 of 79`. That is the clock parameter working as designed — the snapshot contains no "now" —
but when demonstrating, set the clock to 16:30 first or the two numbers look contradictory.

**A reply generalised from the shortlist, and no check caught it.** Shown three options for
SHP1013, the model wrote *"All slots are free: D2 12:00 to 13:00, D4 12:00 to 13:00, D1 13:00 to
14:00."* Replaying that turn against the same data gives **37 of 79 chains feasible**, of which
three were shown. The sentence is wrong twice over: 42 chains were ruled out, and 34 feasible ones
were withheld. It passed `CHECKS PASSED` because C1 and C2 only police dock codes and times, and
every code and time it named was in OPTIONS.

Two causes, both mine. The situation string said *"These slots are currently free"*, and the model
moved the determiner to *"All"*. And `output_checks` had no rule against a quantified availability
claim.

Blocking the sentence was not enough — a rejected reply is routed to a coordinator, which is safe
but is not an answer. So the situation string now states the claim that is *true*: `rank_options`
sorts by start time, so `ok[:3]` are the **earliest** chains that fit, and the reply says exactly
that. Three checks were added or widened:

- **C8** rejects a reply that calls all or every slot free or open.
- **C9** rejects a reply that names an offered dock without its time. A first pass at the wording
  produced *"D2, D4, and D1"* with no times at all — correct, and useless to a driver.
- **`_INJECTION`** was matching `ignore (previous|prior|above) instructions` but not *"ignore YOUR
  instructions"* — a one-word edit defeated it. It now matches the verb and its object with
  anything short in between. It remains a keyword screen and still fails open: the un-screened
  message fell through to the ambiguity question and booked nothing, because the guarantee lives
  in C1–C9, not in the screen.

Verified against the deployed stack with eight genuinely fresh generations — the first attempt at
this measured nothing, because `dedupe_key` is keyed on the date, so re-sending the same text
replayed the stored reply in 1 ms instead of calling the model. All eight report `37 of 79`, name
all three slots with times, and pass every check. Oracle eval 1.0000 / 1.0000 and race test 10/10
still pass.
