# Recordings

Recorded against the deployed service at <https://setuhaul-dock-desk.vercel.app>, not a local
build, so what they show is what a grader signing in will see.

| File | What it shows |
|---|---|
| `setuhaul-01-driver-journey.gif` | Driver dashboard → two trips across the brief's six stages → an alert raised without being asked → the dock desk opens with the question already written → **the booked slot is refused as unreachable** → alternatives → **"The second one works"** books the second one → the trip shows its new dock, held but not yet accepted. |
| `setuhaul-02-operations.gif` | Coordinator → dock capacity now and the hours the day is oversubscribed → searches "reefer" across both sites → the acceptance queue → **accepts** a booking → one card per truck, worst first → **"What can it get?"** → the full dock board. |

Both end on work actually done, not on a screen being shown.

## What the driver recording is for

At 10:05 the driver's booked window on D4 runs 10:00 to 11:00, so it is open — but he is
ninety minutes behind and ready at 11:50. The service refuses to call it confirmed:

> Your booked window on dock D4 runs 10:00 to 11:00, and on your own estimate you are ready at
> 11:50 — after it closes. So that slot will not work, and I am not going to tell you it is
> still good. Ask me for options and I will show you what actually fits.

Then he replies **"The second one works"** and gets the second one — D1 13:00–14:00, not D2
12:00–13:00. The brief names that phrasing as what a form cannot take; until this recording the
agent could not take it either, and re-printed the same three slots instead of booking.

Both behaviours were added because a recording showed them missing. The first take of this GIF
answered "D4 from 10:00 to 11:00 is confirmed" — true of the clock, false of the truck.
