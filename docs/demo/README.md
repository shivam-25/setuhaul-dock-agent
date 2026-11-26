# Demo media

All of it captured by driving <https://setuhaul-dock-desk.vercel.app> in a real browser against
the deployed Lambda on 25 Nov 2026. Not a local build, not a mock-up: every latency shown is a
real round trip.

| file | what it is |
|---|---|
| `setuhaul-explainer.mp4` | 4m00s narrated explainer, 1280x720. Six slides of argument, then four acts driven live against the deployed stack: a driver's journey, a second driver's queries and the three it refuses, the warehouse accepting, and the coordinator's fleet view. Rebuilt by `scripts/build_explainer_cards.py` then `scripts/build_explainer.py`; the footage is the `setuhaul-e2e-*` GIFs in `docs/media`. |
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
this measured nothing, because `dedupe_key` was then keyed on the date, so re-sending the same text
replayed the stored reply in 1 ms instead of calling the model. That key has since been narrowed
(`REPLAY_WINDOW_S`, 120 seconds, scoped to one thread), which is what made this re-measurement
straightforward rather than a fight with the cache. All eight report `37 of 79`, name
all three slots with times, and pass every check. Oracle eval 1.0000 / 1.0000 and race test 10/10
still pass.

## The four demo acts

Each captured in a real browser against <https://setuhaul-dock-desk.vercel.app> on 26 Nov 2026,
signed in as the actual account named - not simulated from one session.

| act | signed in as | what it shows |
|---|---|---|
| `setuhaul-e2e-01-driver.gif` | `drv006` Manoj Sharma, driver | D4 10:00-11:00 refused as unreachable (ready 11:50), three reachable windows offered with nothing reserved, "the second one works" resolved to D1 13:00-14:00 and held |
| `setuhaul-e2e-04-queries-failures.gif` | `drv012` Arjun Das, driver | A status question answered as status; an explicit booking taking a hold; two constraints that cannot both hold, met with a question rather than a shortage report (1.7s); a prompt injection refused and handed to a person (0.5s); a gate arrival |
| `setuhaul-e2e-03-warehouse.gif` | `wh-jai` Neha Bansal, warehouse | The acceptance queue scoped to FAC-JAI-01 - six docks where the coordinator sees nine - and the hold becoming a booking, audited as the warehouse rather than the agent |
| `setuhaul-e2e-05-driver-confirmed.gif` | `drv006` Manoj Sharma, driver | The close of the loop: the dashboard no longer warns, because 11:50 clears a 13:00 window, and the same question DRV012 asked returns "confirmed" rather than "waiting for confirmation" |
| `setuhaul-e2e-02-ops-warehouse.gif` | `ops`, coordinator | Every truck today grouped one card per truck, "what can it get?" reporting 79 chains checked, and the append-only log carrying the whole chain |

The explainer trims the first 6.4s of the coordinator capture, which contains an Accept that the
warehouse act now covers properly; the same booking accepted twice reads as a mistake rather than
as two roles.

## A sixth bug, found while filming the fifth act

Filming the driver's view after acceptance was supposed to be a victory lap. The dashboard
still read "Running 60 minutes behind. Your booked window may no longer fit" - on a 13:00
window the truck reaches at 11:50.

`_trip_alerts` fired that warning on `delay_min >= 30 and appt_id`, with no reachability test
at all. The whole F6b apparatus - effective ETA, confidence buffer, minutes over - sat unused
one module away. So the alert was loudest exactly when it was wrong: after the driver had
taken the system's advice and moved to a window that fits. The dashboard contradicted the chat,
which said "confirmed".

It now compares the buffered ready time against the window, the way commitment_unreachable
does, and says one of three things: the window will be missed and by how much, part of it is
gone, or it still works and there is nothing to do. The buffer matters - the raw ETA is 11:20
and the buffered one 11:50, and claiming a window "still works" against the unbuffered number
would repeat in miniature the error this whole project is about.

Caught by filming, not by a test. Same as the other five.

## One case the UI cannot reach

Box 5 asks which trip a driver means when they have more than one open and name none. The scenario
run exercises it (L7, `scripts/run_scenarios.py`) and `docs/TRANSCRIPTS.md` records it. It cannot be
filmed, because `web/app/chat/page.js` renders the trip selector from the driver's active trips with
no blank option and defaults to the first, so the request always carries a `shipment_id`.

That is arguably the better product - removing an ambiguity beats asking about it - but it is not
free. A driver with two trips who does not notice the selector asks about whichever one happened to
be first. Either the selector needs a "not sure which" option that omits `shipment_id`, or the
default needs to be no selection. Recorded here rather than quietly left, because the branch is
tested, documented, and unreachable.
