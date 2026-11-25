"""U1 - build the working database from the supplied snapshot.
The snapshot is READ-ONLY evidence. We copy it, we never write to it."""
import os, sqlite3, pathlib, shutil, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
# The supplied snapshot. Vendored under data/snapshot/ so the repository is self-contained;
# override with SETUHAUL_SNAPSHOT to point at the original package instead.
SRC  = pathlib.Path(os.environ.get(
    "SETUHAUL_SNAPSHOT", ROOT / "data" / "snapshot" / "setuhaul_freight_operations.db"))
DST  = ROOT / "work" / "setuhaul_work.db"

DDL = """
PRAGMA foreign_keys = ON;

-- One row per slot. The PRIMARY KEY is the arbiter; expiry is a column, never an index predicate
-- (a partial index on `held_until > now()` is illegal in Postgres: predicates must be IMMUTABLE).
CREATE TABLE IF NOT EXISTS slot_locks (
    slot_id     TEXT PRIMARY KEY,
    thread_id   TEXT NOT NULL,
    shipment_id TEXT NOT NULL,
    held_until  TEXT NOT NULL,
    acquired_at TEXT NOT NULL
);

-- Append-only decision log. Rejected candidates are stored WITH their reason.
CREATE TABLE IF NOT EXISTS decision_audit (
    audit_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT NOT NULL,
    request_id     TEXT NOT NULL,
    thread_id      TEXT,
    shipment_id    TEXT,
    actor          TEXT NOT NULL CHECK (actor IN ('agent','human','system')),
    box            TEXT NOT NULL,
    decision       TEXT NOT NULL,
    detail_json    TEXT,
    policy_version TEXT NOT NULL,
    latency_ms     INTEGER
);

-- Box 3. A repeated message replays its previous outcome instead of acting twice.
-- The UNIQUE key is the enforcement; a read-then-write check loses under concurrency.
CREATE TABLE IF NOT EXISTS request_log (
    dedupe_key TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    thread_id  TEXT,
    shipment_id TEXT,
    route      TEXT NOT NULL,
    reply      TEXT NOT NULL,
    handled_at TEXT NOT NULL
);

-- D08: de-duplication must be enforced by a constraint, not by a read-then-write check.
-- NOTE: the plain form of this index FAILS on the snapshot as shipped, because EXC009 repeats
-- EXC001's key. That failure is the proof of audit finding D08. The partial predicate below
-- follows the schema's own idiom (appointments use is_current the same way): a row explicitly
-- marked DUPLICATE is history and is excluded from uniqueness.
CREATE UNIQUE INDEX IF NOT EXISTS ux_dedupe_key
    ON driver_exceptions(dedupe_key)
    WHERE dedupe_key IS NOT NULL AND exception_status <> 'DUPLICATE';
"""

def main():
    if not SRC.exists(): sys.exit(f"missing snapshot: {SRC}")
    DST.parent.mkdir(exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        p = pathlib.Path(str(DST) + suffix)
        if p.exists(): p.unlink()
    shutil.copy2(SRC, DST)

    con = sqlite3.connect(DST)
    con.execute("PRAGMA journal_mode=WAL")       # multi-reader, single-writer
    con.execute("PRAGMA busy_timeout=5000")
    try:
        con.executescript(DDL)
    except sqlite3.IntegrityError as e:
        sys.exit(f"FAILED to add unique index on dedupe_key: {e}\n"
                 "That means the snapshot already contains duplicate keys (see audit D08).")
    con.commit()

    # verification: row counts must match the source, table by table
    src = sqlite3.connect(f"file:{SRC}?mode=ro", uri=True)
    tables = [r[0] for r in src.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    drift = []
    for t in tables:
        a = src.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        b = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        if a != b: drift.append((t, a, b))
        print(f"  {t:<24} {a:>5} -> {b:>5} {'DRIFT' if a != b else 'ok'}")
    print(f"\n{len(tables)} tables copied, {len(drift)} with drift")
    if drift: sys.exit("ROW COUNT DRIFT - stop and investigate")
    fk = con.execute("PRAGMA foreign_key_check").fetchall()
    print(f"foreign_key_check: {len(fk)} violations" + (" <-- see audit D16" if fk else ""))
    print(f"\nwrote {DST}")

main()
