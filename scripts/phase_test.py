"""Proves the dashboard's in-Python commitment phase agrees with commitment_state().

The dashboard cannot afford two queries per truck, so it recomputes the phase from columns
it already has. That is a duplicated rule, and duplicated rules drift. This fails if they
ever disagree, for any shipment, at any of several clocks.
"""
import os, sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT))
from setuhaul.capacity import commitment_state, dt_
from app.server import phase_of, _trip_rows, db

CLOCKS = ["2026-08-04T06:00:00+05:30", "2026-08-04T09:30:00+05:30",
          "2026-08-04T10:05:00+05:30", "2026-08-04T13:00:00+05:30",
          "2026-08-04T18:30:00+05:30", "2026-08-04T23:00:00+05:30"]

con = db()
bad, n = [], 0
for clock in CLOCKS:
    now = dt_(clock)
    for r in _trip_rows(con, "1=1", (), clock):
        d = dict(r)
        mine = phase_of(d, now)
        theirs = commitment_state(con, d["shipment_id"], now)
        theirs = theirs["phase"] if theirs else None
        n += 1
        if mine != theirs:
            bad.append((clock[11:16], d["shipment_id"], mine, theirs))
con.close()

for c, s, a, b in bad[:12]:
    print(f"  MISMATCH {c} {s}: dashboard={a} commitment_state={b}")
print(f"\n{n - len(bad)}/{n} agree across {len(CLOCKS)} clocks")
print("PASS" if not bad else "FAIL")
sys.exit(1 if bad else 0)
