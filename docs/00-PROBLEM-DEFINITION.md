# Problem definition sheet

Four kinds of claim, kept apart on purpose. **Fact** is in the brief or provable by a query against
the snapshot. **Estimate** is derived by me and shows its working. **Assumption** is a design choice
that could be wrong and would change the build. **Unknown** cannot be settled from what we were
given, and nothing in the system depends on guessing it.

## The problem, in one paragraph

A truck runs late. The driver messages SetuHaul. Somebody must decide whether a different dock slot
exists, which one, and whether giving it to this driver breaks a promise already made to another.
Today that decision is made in a chat window by a person holding the schedule in their head, and
during a spike there are more conversations than there is capacity to check. Two drivers can be
told yes for the same slot. The failure is not slowness — it is a promise that cannot be kept.

## Facts

| Claim | Source |
|---|---|
| 280–360 loads/day across 6 facilities, 32 docks, 190–240 appointments/day | brief |
| 15–25 exception messages on a normal day; **20–35 inside a 30-minute spike** | brief |
| **No live GPS or telemetry feed exists.** Every ETA is a driver's claim | brief; 0 telemetry tables in the snapshot |
| Snapshot holds 18 tables: 21 shipments, 2 facilities, 9 docks, 106 slots, 20 appointments (14 active), 15 drivers, 20 chat messages, 10 driver exceptions, 12 ETA updates, 3 dock events, 5 operational messages, 6 facility rules | query |
| Scarcity is by dock *type*: 2 reefer docks, **1 heavy dock** | query |
| 6 shipments need more than a 60-minute slot. Two are heavy at 90 min and fit the heavy bay's 90-min slots; **4 are ordinary loads at 70–75 min on 60-minute slots**, two of them CRITICAL | query |
| Jaipur has 5 facility rules. **Gurugram has 1.** No last-start time, no heavy-dock threshold, no early-check-in limit, no reefer rule | query |
| `slot_status` only ever holds OPEN or BLOCKED — it does not record bookings | query |
| `dock_code` is **not unique across facilities**: D3 is a reefer dock at one and a standard dock at the other | query |
| The written policy corpus is **379 bytes across 6 rows** | query |
| The snapshot contains no "now" — no column marks the current time | query |

## Estimates

| Claim | Working |
|---|---|
| Peak arrival rate ≈ **1.2 messages/minute** | 35 messages ÷ 30 minutes |
| The system is **contention-bound, not throughput-bound** | 1.2 msg/min is trivial to serve; the scarce thing is a compatible slot at a given hour, and two conversations can want the same one |
| Measured median turn **1.37 s**, slowest 2.1 s (cold start), against the 5 s gate | 9 scenario cases, live Bedrock |
| Extraction call ≈ **850 ms warm**, so the model is roughly 60% of a turn | scenario boxes |
| Cost is dominated by the two small model calls per turn | no retrieval, no large context |

## Assumptions

Each one is a decision. If it is wrong, the named thing changes.

| Assumption | If wrong |
|---|---|
| A stated range means the **later** end, not the midpoint | Early offers become no-shows. The stored data uses the midpoint (SHP1017 → 12:45); we deliberately use 13:00 |
| A relative delay with no clock time is **LOW** confidence | Buffers shrink and marginal offers increase. Matches all 4 stored `confidence_code` values |
| Confidence buffers of **0 / 15 / 30 min** for HIGH / MEDIUM / LOW | Tunable; no data exists to fit them, so they are stated, not fitted |
| A facility with no last-start rule is limited by its **own closing time** | Previously defaulted to Jaipur's 21:00, which wrongly rejected Gurugram slots. Fixed |
| A facility with no heavy-dock rule has **no heavy mandate** there | Physical dock weight limits (F2a) still apply, so the exposure is policy, not safety |
| Hold TTL of **120 seconds** | Too short strands deciding drivers; too long starves the queue |
| Asking about options takes **no hold** | If asking reserved capacity, a browsing driver would block a booking one |
| Showing a slot and reserving it are different acts | This is the core of the design; if the client wants ask-to-reserve, box 9 changes |
| One shared database across facilities, with every query filtered by facility | If each facility runs its own, cross-facility ranking disappears |

## Unknowns

| Unknown | How we avoid depending on it |
|---|---|
| Real no-show rate after a buffered offer | Buffers are a declared parameter, not a learned one |
| Whether coordinators will accept automatic booking without approval | Every risky class already routes to a human; the approval list is config |
| True cost per resolved exception | Reported as measured tokens, not as a projected rupee figure |
| Whether the night-shift vacancy at Gurugram is filled | The escalation checks reachability at run time and says so when it fails |
| Driver language mix; how many write in Hindi or transliterated Hindi | The reply prompt targets plain short English; this needs real traffic to settle |
| How 32 docks across 6 facilities are actually governed — one rule set or six | The Gurugram gap suggests rules are added per facility by hand, which is itself a finding |
| Whether unload overruns are systematic or occasional | We reserve the stated duration rather than assume 60 minutes fits |

## What success means

The north star is **promise integrity: of the slots we confirm, the share we can actually honour.**
It is a gate at 100%, not a target to optimise, because a broken promise costs a driver a wasted
trip and is not recoverable by being fast. Everything else — automation rate, latency, cost — is
secondary and may be traded away to hold that gate.
