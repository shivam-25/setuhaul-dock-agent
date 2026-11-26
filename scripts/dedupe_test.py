"""Box 3 in isolation: what counts as a retry, and what does not.

The old key was driver + shipment + the DAY + sha1(text). Both of its mistakes are
asserted here as explicit non-replays, so neither can come back unnoticed.
"""
import sys, sqlite3, pathlib, shutil
from datetime import timedelta
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from setuhaul.agent import handle, dedupe_key, REPLAY_WINDOW_S
from setuhaul.capacity import dt_

BASE, DB = ROOT / "work" / "setuhaul_work.db", ROOT / "work" / "dedupe.db"
NOW = dt_("2026-08-04T10:05:00+05:30")
MSG = "Traffic after Shahpura. Reaching around 11:20. Any slot after 12?"

def fresh():
    for s in ("", "-wal", "-shm"):
        p = pathlib.Path(str(DB) + s)
        if p.exists(): p.unlink()
    shutil.copy2(BASE, DB)
    c = sqlite3.connect(DB); c.execute("PRAGMA journal_mode=WAL"); c.commit(); c.close()
    con = sqlite3.connect(DB); con.execute("PRAGMA busy_timeout=5000"); return con

def turn(con, *, thread, now, shipment="SHP1006", msg=MSG, driver="DRV006"):
    return handle(con, message=msg, driver_id=driver, thread_id=thread, now=now,
                  shipment_id=shipment)

fails = []
def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not cond: fails.append(name)

print(f"box 3 - replay window is {REPLAY_WINDOW_S}s\n")

# 1. The key itself. A retry differs from a new question in exactly one of these fields.
print("key composition")
k = dedupe_key("DRV006", "SHP1006", MSG, "THR001")
check("thread_id changes the key", k != dedupe_key("DRV006", "SHP1006", MSG, "THR009"))
check("driver_id changes the key", k != dedupe_key("DRV099", "SHP1006", MSG, "THR001"))
check("shipment_id changes the key", k != dedupe_key("DRV006", "SHP1099", MSG, "THR001"))
check("text changes the key", k != dedupe_key("DRV006", "SHP1006", MSG + "?", "THR001"))
check("the key carries no date", "20260804" not in k, k)
check("whitespace and case are not meaning",
      k == dedupe_key("DRV006", "SHP1006", "  TRAFFIC  AFTER SHAHPURA. REACHING AROUND "
                      "11:20. ANY SLOT AFTER 12?  ", "THR001"))
# Two unresolved trips in two conversations was the live collision: identical words, no
# shipment named, and the old key made them the same request.
check("an UNKNOWN shipment does not collide across threads",
      dedupe_key("DRV006", None, "I am running late.", "THR-A")
      != dedupe_key("DRV006", None, "I am running late.", "THR-B"))

print("\nbehaviour, driven through handle()")
con = fresh()
a = turn(con, thread="THR001", now=NOW)
check("first time seen is answered", a.get("duplicate_of") is None, a["route"])

b = turn(con, thread="THR001", now=NOW)
check("an immediate retry replays", b.get("duplicate_of") == a["request_id"])

c = turn(con, thread="THR001", now=NOW + timedelta(seconds=REPLAY_WINDOW_S - 5))
check("inside the window replays", c.get("duplicate_of") == a["request_id"])

d = turn(con, thread="THR001", now=NOW + timedelta(seconds=REPLAY_WINDOW_S + 5))
check("outside the window is answered again", d.get("duplicate_of") is None)
check("and the expiry is on the record",
      any(x["n"] == 3 and "outside" in x["note"] for x in d["boxes"]))

# Six hours later is the case that mattered: the old key replayed a 10:05 shortlist at
# 16:00, naming slots that had since been taken.
e = turn(con, thread="THR001", now=NOW + timedelta(hours=6))
check("the same question six hours later gets a fresh answer", e.get("duplicate_of") is None)

con2 = fresh()
f1 = turn(con2, thread="THR001", now=NOW)
f2 = turn(con2, thread="THR009", now=NOW)
check("the same words in another thread are answered, not replayed",
      f2.get("duplicate_of") is None)
check("...and that did not double-book the shipment",
      con2.execute("SELECT COUNT(*) FROM appointments WHERE shipment_id='SHP1006' "
                   "AND is_current=1 AND appointment_status NOT IN ('CANCELLED','NO_SHOW')"
                   ).fetchone()[0] <= 1)

con3 = fresh()
g1 = turn(con3, thread="THR-A", now=NOW, shipment=None, msg="I am running late.")
g2 = turn(con3, thread="THR-B", now=NOW, shipment=None, msg="I am running late.")
check("two unresolved trips in two threads are not one request",
      g2.get("duplicate_of") is None, "this was the live collision")

print(f"\n{'PASS' if not fails else 'FAILED: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
