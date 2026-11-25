# What we built, and what it will and will not do
For the Head of Operations and the Jaipur warehouse planner. No jargon, one page.

## The problem we went after

When a truck runs late, the driver messages you. On a bad morning twenty to thirty-five of them
message within half an hour, and the number of usable dock slots does not grow to match. Two
conversations can read the same free slot and both drivers can be told yes. Only one of those
promises can be kept, and the driver who finds out second has already driven there.

## What the service does

A driver writes in plain language — "tyre damaged, ninety minutes late, anything after seven?" —
and gets back either a real slot or a straight answer that there isn't one.

Three things make that safe:

**The computer decides capacity, not the AI.** The AI reads the driver's message and writes the
reply. Which dock is free, who gets it when two drivers want it, and whether a booking is allowed
are all decided by ordinary code against your database. The AI cannot overrule any of it.

**Showing a slot is not the same as holding one.** When a driver is choosing, the slot is held for
two minutes and genuinely removed from everyone else. A driver who is only asking what's possible
is told the options are not reserved, and nothing is taken out of your schedule.

**When two drivers want the same slot, exactly one gets it.** We tested this with twenty
simultaneous requests for one dock door, ten times over. Every single time: one booking, nineteen
drivers told immediately that it had gone and offered the next option. Nobody saw an error, and
nobody was promised something we then took away.

## What it will not do

It will not promise a dock when none is available. One of our test cases is a refrigerated load
arriving at 18:30 when the only reefer dock is under maintenance and the 21:00 last-start rule
blocks the rest of the evening. There is no good answer, so the service does not invent one — it
hands the case to a coordinator with the full record of every option it considered and why each
was rejected.

It will not decide anything that carries risk. A person still approves: any start after 21:00, any
change to a booking you have already confirmed, anything involving penalties or safety, and any
case where the data contradicts itself.

## Four things we found in your data

These were in the system before we touched it. Each one is proven by a query you can re-run.

**1. The out-of-hours escalation goes nowhere.** The one case that has to reach a human was sent to
a night-shift address that matches no contact in your records. The only night-shift contact on file
is an unfilled position with no email — and it is still marked active, so nothing flags it. The
service now detects this at the moment it escalates and says so.

**2. Two live bookings break your own weight rules.** A load is booked onto a dock rated below its
weight, and one of those bookings is already confirmed.

**3. Four loads cannot fit the slot they are booked into.** Six loads need more than sixty minutes.
Two of them are heavy loads needing ninety, and those are fine — the heavy bay runs ninety-minute
slots. The other four are ordinary loads needing seventy to seventy-five minutes on sixty-minute
slots, and two of those four are your critical ones. Each of those bookings was always going to run
into the next slot, which another driver could book. We now reserve the real number of minutes.

**4. Dock breakdowns are copied into the schedule by hand, and the copy is wrong.** One breakdown
left a forty-five-minute window where the dock was down but the schedule said otherwise. A second
capacity event appears in the schedule not at all.

## Limits you should know about

The data has no live GPS, so every arrival time is a claim, not a fact. When a driver is vague we
add a buffer rather than pretending to precision — a wrong early slot becomes a no-show, which
costs you a dock.

The snapshot we tested against has no single "now", so our clock is set explicitly in every test.

We have proved correctness under pressure. We have not yet measured how often the AI reads a
message correctly, or what it costs per conversation. Both are being measured and will be reported
as numbers, not estimates.

## The open risk

Your scarcity is not docks in total — it is *compatible* docks at the hour a driver needs. Jaipur
has one refrigerated dock and one heavy dock. Lose either and that category has no capacity at all,
and no amount of software fixes that. The service will tell you clearly and early when it happens,
which is the most honest thing it can do.
