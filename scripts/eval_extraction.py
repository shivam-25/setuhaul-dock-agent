"""Extraction accuracy and the model comparison (requirements 13 and 14).

Scored on what the SYSTEM depends on, not on raw model fields: the resolved ETA after the
deterministic rule, the intent, and the two driver-stated constraints. A model that reports
'relative_delay_min: 60' and one that reports 'declared_eta_time: 11:00' are both right if the
resolved ETA lands on the same minute.
"""
import sys, json, pathlib, statistics as st
from datetime import datetime
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import setuhaul.agent as A
from setuhaul.capacity import dt_

MODELS = ["apac.amazon.nova-micro-v1:0", "apac.amazon.nova-lite-v1:0",
          "global.amazon.nova-2-lite-v1:0", "apac.amazon.nova-pro-v1:0"]
CASES = json.loads((ROOT / "eval" / "extraction_cases.json").read_text())["cases"]

def hhmm(d): return f"{d:%H:%M}"

def run(model):
    A.MODEL_FAST = model
    rows, lat, tin, tout = [], [], 0, 0
    for c in CASES:
        base = dt_(f"2026-08-04T{c['orig']}:00+05:30")
        try:
            facts, ms, usage = A.extract(c["msg"], f"Planned arrival {c['orig']}, standard dock, 45 min unload.")
        except Exception as e:
            rows.append({"id": c["id"], "error": str(e)[:80], "ok": False}); continue
        lat.append(ms); tin += usage.get("inputTokens", 0); tout += usage.get("outputTokens", 0)
        eta, conf, _ = A.apply_eta_rule(facts, base)
        g = c["gold"]
        got = {"intent": facts.get("intent"), "eta": hhmm(eta),
               "early": facts.get("earliest_acceptable_time"),
               "latest": facts.get("latest_finish_time")}
        want = {"intent": g["intent"], "eta": c["orig"] if g["eta"] == "orig" else g["eta"],
                "early": g["early"], "latest": g["latest"]}
        def eq(k):
            a, b = got[k], want[k]
            if k in ("early", "latest"):
                pa, pb = A.parse_hhmm(a), A.parse_hhmm(b)
                if pa is None and pb is None: return True
                if pa is None or pb is None: return False
                return pa == pb
            return a == b
        hits = {k: eq(k) for k in want}
        rows.append({"id": c["id"], "msg": c["msg"][:46], **hits,
                     "ok": all(hits.values()), "got": got, "want": want})
    n = len([r for r in rows if "error" not in r])
    f = lambda k: sum(1 for r in rows if r.get(k)) / n if n else 0
    return {"model": model, "n": n, "errors": len(rows) - n,
            "intent": f("intent"), "eta": f("eta"), "early": f("early"),
            "latest": f("latest"), "all_four": f("ok"),
            "p50_ms": int(st.median(lat)) if lat else 0,
            "p95_ms": int(sorted(lat)[int(len(lat) * .95) - 1]) if lat else 0,
            "in_tok": tin, "out_tok": tout, "rows": rows}

results = [run(m) for m in MODELS]
print(f"{'model':<34}{'intent':>8}{'eta':>8}{'early':>8}{'latest':>8}{'ALL 4':>8}"
      f"{'p50':>7}{'p95':>7}{'tok':>8}")
for r in results:
    print(f"{r['model'].split('.')[-1]:<34}{r['intent']:>8.3f}{r['eta']:>8.3f}{r['early']:>8.3f}"
          f"{r['latest']:>8.3f}{r['all_four']:>8.3f}{r['p50_ms']:>7}{r['p95_ms']:>7}"
          f"{r['in_tok']+r['out_tok']:>8}")

# The system no longer branches on five-way intent: it asks one question, "did the driver
# explicitly ask to book?", because only that takes a hold. Measure THAT, since that is what a
# wrong answer costs. Over-calling BOOK consumes another driver's slot; under-calling only
# costs an extra turn.
for r in results:
    rows = [x for x in r["rows"] if "error" not in x]
    tp = sum(1 for x in rows if x["got"]["intent"] == "BOOK" and x["want"]["intent"] == "BOOK")
    fp = sum(1 for x in rows if x["got"]["intent"] == "BOOK" and x["want"]["intent"] != "BOOK")
    fn = sum(1 for x in rows if x["got"]["intent"] != "BOOK" and x["want"]["intent"] == "BOOK")
    r["book_precision"] = tp / (tp + fp) if tp + fp else 1.0
    r["book_recall"] = tp / (tp + fn) if tp + fn else 1.0
print(f"\n{'model':<34}{'BOOK prec':>11}{'BOOK rec':>10}   (precision is the one that costs capacity)")
for r in results:
    print(f"{r['model'].split('.')[-1]:<34}{r['book_precision']:>11.3f}{r['book_recall']:>10.3f}")

best = max(results, key=lambda r: (r["all_four"], -r["p50_ms"]))
print(f"\nbest all-four: {best['model']}  ({best['all_four']:.3f})")
print(f"gate for box 4 is >= 0.95 on intent and eta\n")
for r in results:
    bad = [x for x in r["rows"] if not x.get("ok")]
    print(f"--- {r['model'].split('.')[-1]}: {len(bad)} failures")
    for x in bad[:6]:
        if "error" in x: print(f"    {x['id']} ERROR {x['error']}"); continue
        wrong = [k for k in ("intent","eta","early","latest") if not x[k]]
        print(f"    {x['id']} {x['msg']:<48} {wrong} got={[x['got'][k] for k in wrong]} "
              f"want={[x['want'][k] for k in wrong]}")
(ROOT / "eval" / "extraction_results.json").write_text(json.dumps(
    {"generated": datetime.now().isoformat(timespec="seconds"),
     "cases": len(CASES), "results": results}, indent=2))
