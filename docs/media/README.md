# Recordings

Recorded against the deployed service at <https://setuhaul-dock-desk.vercel.app>, not a local
build, so what they show is what a grader signing in will see.

| File | What it shows |
|---|---|
| `setuhaul-01-driver-journey.gif` | Driver signs in → two trips with the brief's six stages → an alert raised without being asked → the dock desk opens with the question already written → the booked slot is refused as unreachable → three slots that actually fit. |
| `setuhaul-02-operations.gif` | Coordinator signs in → dock capacity now → searches "reefer" across both sites → the acceptance queue → accepts a CRITICAL booking → the full dock board for the day. |

The driver recording is the one worth watching. At 10:05 the driver's booked window on D4 runs
10:00 to 11:00, so it is open — but he is ninety minutes behind and ready at 11:50. The service
says the slot will not work and refuses to call it confirmed, then offers alternatives. That
behaviour did not exist until this recording was made: the first take answered "D4 from 10:00 to
11:00 is confirmed", which is true of the clock and false of the truck.
