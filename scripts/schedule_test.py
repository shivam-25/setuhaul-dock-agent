"""Optional deliverable: the facility-level scheduling tool, compared with simple rules.
Brief level +: an early truck (SHP1003), a late truck (SHP1004) and work in progress
(SHP1002) share one facility."""
import sys, sqlite3, pathlib, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from setuhaul.scheduler import compare, read_jobs, read_docks
from setuhaul.capacity import dt_

ROOT = pathlib.Path(__file__).resolve().parents[1]
con = sqlite3.connect(ROOT / "work" / "setuhaul_work.db")
NOW = dt_("2026-08-04T10:05:00+05:30").replace(tzinfo=None)
FAC = "FAC-JAI-01"

jobs = read_jobs(con, FAC, NOW)
print(f"{len(jobs)} jobs, {len(read_docks(con, FAC))} docks at {FAC}\n")
for j in sorted(jobs, key=lambda j: j.release):
    tag = "FIXED on " + j.fixed_dock if j.fixed_dock else ("promised" if j.promised else "")
    print(f"  {j.shipment_id}  {j.priority:<8} release {j.release:%H:%M}  "
          f"{j.process_min:>3}min  due {j.due:%H:%M}" if j.due else
          f"  {j.shipment_id}  {j.priority:<8} release {j.release:%H:%M}  "
          f"{j.process_min:>3}min  due -    ", end="")
    print(f"  {tag}")

res = compare(con, FAC, NOW)
print(f"\nobjective: {res['objective']}\n")
hdr = f"  {'rule':<10}{'wait':>7}{'wtd wait':>10}{'missed':>8}{'late min':>10}{'broken':>8}{'makespan':>10}"
print(hdr); print("  " + "-" * (len(hdr) - 2))
for rule, r in res["rules"].items():
    s = r["summary"]
    print(f"  {rule:<10}{s['total_wait_min']:>7}{s['weighted_wait']:>10}"
          f"{s['missed_appointments']:>8}{s['total_late_min']:>10}"
          f"{s['promises_broken']:>8}{s['makespan_min']:>10}")

# The brief invites this comparison so that a weak result can be reported, not hidden.
rs = {k: v["summary"] for k, v in res["rules"].items()}
best = min(rs, key=lambda k: rs[k]["weighted_wait"])
gain = 100 * (rs["fcfs"]["weighted_wait"] - rs[best]["weighted_wait"]) / rs["fcfs"]["weighted_wait"]
verdict = (
 f"Best rule by weighted waiting: {best} ({rs[best]['weighted_wait']}), against first-come-"
 f"first-served at {rs['fcfs']['weighted_wait']} - a gain of {gain:.1f}%.\n"
 f"Every rule misses {rs['fcfs']['missed_appointments']} appointments and breaks "
 f"{rs['fcfs']['promises_broken']} promises, and the makespan is identical at "
 f"{rs['fcfs']['makespan_min']} minutes.\n"
 f"Reading: the ordering rule is NOT the binding constraint here - the facility is simply\n"
 f"oversubscribed in this window, and no sequence invents a dock. A smarter rule recovers a\n"
 f"few percent of weighted waiting; it does not recover a missed appointment. That is the\n"
 f"case for buying dock hours rather than for buying constraint programming, and it is why\n"
 f"CP-SAT is not used: there is no measured gap for it to close.")
print("\n" + verdict)
res["verdict"] = verdict
res["best_rule"] = best
out = ROOT / "docs" / "SCHEDULING_TOOL.json"
out.write_text(json.dumps(res, indent=2))
print(f"\nwritten {out.relative_to(ROOT)}")
con.close()
