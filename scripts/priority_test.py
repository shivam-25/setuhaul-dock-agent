"""D7 evidence - "a policy tested on competing trucks".

The brief's level 4: SHP1006, SHP1012, SHP1013, SHP1014, SHP1009 chase nearby slots at
one facility. This prints, for each truck, what it was offered and what was withheld from
it and for whom, so the policy can be argued with rather than taken on trust.
"""
import sys, sqlite3, pathlib, shutil, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from setuhaul.capacity import (load_ctx, candidates, rank_options, priority_gate,
                               contenders, dt_, PRIORITY_RANK)

ROOT = pathlib.Path(__file__).resolve().parents[1]
DB   = ROOT / "work" / "priority.db"
shutil.copy2(ROOT / "work" / "setuhaul_work.db", DB)
NOW  = dt_("2026-08-04T10:05:00+05:30")
TRUCKS = ["SHP1006", "SHP1012", "SHP1013", "SHP1014", "SHP1009"]

con = sqlite3.connect(DB)
rows, violations = [], []
for shp in TRUCKS:
    ctx = load_ctx(con, shp, now=NOW)
    ok  = rank_options([c for c in candidates(con, ctx, NOW) if c.ok])
    allowed, withheld = priority_gate(con, ctx, ok, NOW)
    rows.append({"shipment": shp, "priority": ctx.priority_code, "feasible": len(ok),
                 "offered": [c.label for c in allowed[:3]],
                 "withheld": [{"slot": w["label"], "for": w["protected_for"][0],
                               "their_priority": w["their_priority"]} for w in withheld]})
    print(f"{shp}  {ctx.priority_code:<8} feasible={len(ok):<3} "
          f"offered={[c.label for c in allowed[:3]]}")
    for w in withheld:
        print(f"           withheld {w['label']:<16} for {w['protected_for'][0]} "
              f"({w['their_priority']} outranks {w['my_priority']})")
    # A violation is a LOWER-priority truck being offered a slot that is the ONLY option
    # for a HIGHER-priority truck. That is exactly what the policy must prevent.
    mine = PRIORITY_RANK.get(ctx.priority_code, 9)
    for h in contenders(con, ctx, NOW):
        if PRIORITY_RANK.get(h.priority_code, 9) >= mine: continue
        theirs = {c.slot_ids[0] for c in candidates(con, h, NOW) if c.ok}
        if len(theirs) == 1 and theirs & {c.slot_ids[0] for c in allowed}:
            violations.append({"offered_to": shp, "my_priority": ctx.priority_code,
                               "stole_sole_slot_of": h.shipment_id,
                               "their_priority": h.priority_code})

print(f"\npriority-policy violations: {len(violations)}")
for v in violations: print("  ", v)
out = ROOT / "docs" / "PRIORITY_POLICY_TEST.json"
out.write_text(json.dumps({"as_of": NOW.isoformat(), "policy": "last-slot protection",
                           "trucks": rows, "violations": violations}, indent=2))
print(f"written {out.relative_to(ROOT)}")

# ------------------------------------------------------------------ the policy must FIRE
# The run above passes vacuously: in this snapshot no higher-priority truck is down to a
# single option, so the gate never engages. A test that cannot fail proves nothing. So
# manufacture the scarcity the policy exists for and check it actually bites.
print("\n--- forced scarcity: a CRITICAL truck down to one slot ---")
from setuhaul.capacity import ts
crit, low = "SHP1014", "SHP1013"
cctx = load_ctx(con, crit, now=NOW)
cands = [c for c in candidates(con, cctx, NOW) if c.ok]
# A long load needs two contiguous slots, so "one option" means one whole CHAIN. Keeping a
# single slot_id would break every chain including the one meant to survive - which is
# exactly what the first attempt at this test did, leaving the truck with zero.
keep_chain = set(cands[len(cands)//2].slot_ids)
keep = cands[len(cands)//2].slot_ids[0]
block = [s for c in cands for s in c.slot_ids if s not in keep_chain]
con.executemany("UPDATE appointment_slots SET slot_status='BLOCKED' WHERE slot_id=?",
                [(s,) for s in block])
con.commit()
cctx = load_ctx(con, crit, now=NOW)
remaining = [c.slot_ids[0] for c in candidates(con, cctx, NOW) if c.ok]
print(f"  {crit} ({cctx.priority_code}) now has {len(remaining)} feasible slot(s)")

lctx = load_ctx(con, low, now=NOW)
lok  = rank_options([c for c in candidates(con, lctx, NOW) if c.ok])
allowed, withheld = priority_gate(con, lctx, lok, NOW)
print(f"  {low} ({lctx.priority_code}) feasible={len(lok)} offered={len(allowed)} withheld={len(withheld)}")
fired = False
for w in withheld:
    print(f"    WITHHELD {w['label']} -> protected for {w['protected_for'][0]} "
          f"({w['their_priority']} outranks {w['my_priority']})")
    if crit in w["protected_for"]: fired = True

# And the other half of the rule: never starve the driver in front of us.
print("  --- when it is also the lower-priority truck's only option ---")
only = [c for c in lok if c.slot_ids[0] == keep]
a2, w2 = priority_gate(con, lctx, only, NOW)
starved = (len(a2) == 0)
print(f"    sole option offered anyway: {len(a2) == 1 and not w2}  (starved: {starved})")

ok_fire = fired and len(remaining) == 1 and not starved
print(f"\npolicy engaged under scarcity: {ok_fire}")
con.close()
sys.exit(0 if (not violations and ok_fire) else 1)
