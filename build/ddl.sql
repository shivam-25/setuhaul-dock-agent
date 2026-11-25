
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
