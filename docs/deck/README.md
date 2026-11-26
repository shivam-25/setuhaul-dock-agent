# Presentation

**The deck lives as a Slides artifact:** <https://claude.ai/artifact/8rJ8qTFbjCdj8DYdjNC1iP>
That is the canonical copy. Export a `.pptx` or PDF from it with **Share › Export**.

Twelve slides, 16:9, speaker notes on every one.

Running order: the failure → the lane boundary → the race test → the data defects → the ten
predicates and the oracle → the model comparison → **what was built, and who sees what** → the
live latencies → **the bugs that using it found** → the case with no good answer → what is
proved and what is not.
predicates and the oracle → the model comparison → **what was built, and who sees what** → the
live latencies → **the four bugs that using it found** → the case with no good answer → what is
proved and what is not.

The notes carry what is worth saying out loud rather than putting on a slide: the three bugs the
independent oracle exposed, why the intent-accuracy failure was a labelling problem rather than a
model problem, and the correction about reserved concurrency.

Every number on a slide appears in a file in this repository. The deck closes on the limits
rather than the wins, because the honest column is what makes the rest credible.

## Recordings

In `../media/`:

| File | What it shows |
|---|---|
| `setuhaul-01-driver-journey.gif` | A driver signs in, sees two trips with their six stages and an alert they did not ask for, opens the dock desk with the question pre-written, is told the booked slot **will not work**, and is then offered three that do. |
| `setuhaul-02-operations.gif` | A coordinator signs in, searches the dock list, sees the acceptance queue and the trucks needing a decision worst-first, accepts a CRITICAL booking, and opens the full dock board. |

Both were recorded against the deployed service, not a local build.

## A note on the earlier version

The previous deck claimed a 986 ms booking turn. That number was real but came from the SQLite
single-container build, which could not have been correct in production: two warm Lambda
environments held two different database files and could have sold the same slot twice. On shared
Postgres, with the database a region away because an organisation policy denies Lambda in
`ap-south-1`, the honest figure is **4.4 seconds**, of which 3.4 is network and 1.1 is the model.
The slide now says so, and says what it would be co-located.
