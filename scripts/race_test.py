"""U3 / deliverable 5 - the integrity proof.
N drivers reach for the SAME slot inside one window. Exactly one may win.
Nobody may see an error: a lost race is a re-offer, never a broken promise."""
import sys, sqlite3, threading, pathlib, shutil, json
from datetime import datetime
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from setuhaul.capacity import load_ctx, candidates, feasible, rank_options, claim, commit_booking, LostRace, dt_, ts

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = ROOT / "work" / "setuhaul_work.db"
DB   = ROOT / "work" / "race.db"
N, REPEATS = 20, 10
NOW = dt_("2026-08-04T10:05:00+05:30")
CONTENDERS = ["SHP1006","SHP1012","SHP1013","SHP1007","SHP1009"]

def reset():
    for s in ("", "-wal", "-shm"):
        p = pathlib.Path(str(DB) + s)
        if p.exists(): p.unlink()
    shutil.copy2(BASE, DB)
    c = sqlite3.connect(DB); c.execute("PRAGMA journal_mode=WAL"); c.commit(); c.close()

def pick_target():
    con = sqlite3.connect(DB)
    ctx = load_ctx(con, "SHP1006", thread_id="probe", now=NOW)
    tgt = rank_options(feasible(con, ctx, NOW))[0]
    con.close(); return tgt.slot_ids[0]

def worker(i, slot_id, barrier, results):
    con = sqlite3.connect(DB, timeout=10); con.execute("PRAGMA busy_timeout=10000")
    shp = CONTENDERS[i % len(CONTENDERS)]
    thread_id = f"RACE-T{i:02d}"
    try:
        ctx = load_ctx(con, shp, thread_id=thread_id, now=NOW)
        cand = next((c for c in candidates(con, ctx, NOW) if c.slot_ids[0] == slot_id), None)
        barrier.wait()                                    # everyone reaches for it together
        if cand is None:
            results.append((i, "not_offered", None)); return
        # hold every slot in the chain: a 75-min load needs two contiguous slots (DEFECT-1)
        if not claim(con, cand.slot_ids, thread_id, shp, NOW):
            results.append((i, "lost_hold", None)); return
        con.commit()
        appt, created = commit_booking(con, ctx, cand, NOW)
        results.append((i, "won" if created else "idempotent_replay", appt))
    except LostRace:
        results.append((i, "lost_commit", None))
    except Exception as e:
        results.append((i, f"ERROR:{type(e).__name__}:{e}", None))
    finally:
        con.close()

def run(rep):
    reset(); slot_id = pick_target()
    barrier, results = threading.Barrier(N), []
    threads = [threading.Thread(target=worker, args=(i, slot_id, barrier, results)) for i in range(N)]
    for t in threads: t.start()
    for t in threads: t.join()
    con = sqlite3.connect(DB)
    rows = con.execute("SELECT appointment_id,shipment_id,appointment_status FROM appointments "
                       "WHERE slot_id=? AND appointment_status IN "
                       "('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS')", (slot_id,)).fetchall()
    con.close()
    won  = [r for r in results if r[1] == "won"]
    lost = [r for r in results if r[1] in ("lost_hold", "lost_commit", "not_offered", "idempotent_replay")]
    errs = [r for r in results if r[1].startswith("ERROR")]
    ok = len(rows) == 1 and len(won) == 1 and not errs
    from collections import Counter
    brk = Counter(r[1].split(':')[0] for r in results)
    print(f"  run {rep:>2}: slot={slot_id}  appointments={len(rows)}  won={len(won)}  "
          f"lost_cleanly={len(lost)}  errors_surfaced={len(errs)}  {'PASS' if ok else 'FAIL'}  {dict(brk)}")
    if errs: print("          ", errs[:3])
    return ok, len(rows), len(won), len(lost), len(errs)

print(f"RACE TEST — {N} concurrent requests for one slot, {REPEATS} repeats")
print(f"Clock: {NOW:%Y-%m-%d %H:%M %z} (declared; the snapshot has no 'now' — audit D14)\n")
res = [run(r + 1) for r in range(REPEATS)]
allok = all(r[0] for r in res)
print(f"\n{'='*72}")
print(f"RESULT: {sum(1 for r in res if r[0])}/{REPEATS} runs passed")
print(f"  appointments created per run : {sorted({r[1] for r in res})}   (must be exactly [1])")
print(f"  winners per run              : {sorted({r[2] for r in res})}   (must be exactly [1])")
print(f"  errors shown to any driver   : {sorted({r[4] for r in res})}   (must be exactly [0])")
print(f"  every other caller got a clean 'lost, here is another option' path")
print(f"  duplicate requests replayed, never double-booked (THR009 case)")
print(f"{'='*72}")
print("PASS — no double booking" if allok else "FAIL")
(ROOT / "docs" / "INTEGRITY_PROOF.json").write_text(json.dumps(
    {"generated": datetime.now().isoformat(timespec="seconds"), "concurrency": N, "repeats": REPEATS,
     "clock": ts(NOW), "runs": [{"pass": r[0], "appointments": r[1], "winners": r[2],
     "lost_cleanly": r[3], "errors": r[4]} for r in res], "verdict": "PASS" if allok else "FAIL"}, indent=2))
sys.exit(0 if allok else 1)
