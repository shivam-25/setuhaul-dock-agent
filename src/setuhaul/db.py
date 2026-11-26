"""One connection interface over SQLite and Postgres.

The capacity core was written against sqlite3: `?` placeholders, `con.execute(...)`,
`cur.rowcount`, `BEGIN IMMEDIATE`. Rewriting every call site to psycopg would be a large
diff across the exact code the race test certifies, so instead this wraps a psycopg
connection in the sqlite3 shape the rest of the codebase already speaks.

What actually differs between the two engines, and how each is handled:

  placeholders    `?` -> `%s`, skipping anything inside a quoted string literal.
  BEGIN IMMEDIATE Postgres has no IMMEDIATE; it takes row locks on first write instead.
                  Translated to BEGIN, which is the equivalent for our use.
  rowcount        Both report affected rows, so the conditional-UPDATE arbitration in
                  decide_appointment and claim() behaves identically.
  autocommit      psycopg defaults to an open transaction like sqlite3 does; both need an
                  explicit commit, so call sites are unchanged.

The locking design never relied on SQLite semantics - slot_locks is a unique index and the
INSERTs are conditional - which is why this port is a connection swap and not a redesign.
"""
from __future__ import annotations
import os, re, sqlite3

_PLACEHOLDER = re.compile(r"'(?:[^']|'')*'|\?")


def _to_pg(sql: str) -> str:
    """`?` -> `%s`, leaving `?` inside string literals alone, and `%` escaped for psycopg."""
    sql = sql.replace("%", "%%")
    out = _PLACEHOLDER.sub(lambda m: "%s" if m.group(0) == "?" else m.group(0), sql)
    return out.replace("BEGIN IMMEDIATE", "BEGIN")


class _Cursor:
    """Carries rowcount and iteration, so `con.execute(...).fetchone()` keeps working."""
    __slots__ = ("_cur",)

    def __init__(self, cur): self._cur = cur
    def fetchone(self):  return self._cur.fetchone()
    def fetchall(self):  return self._cur.fetchall()
    def __iter__(self):  return iter(self._cur)
    @property
    def rowcount(self):  return self._cur.rowcount
    @property
    def description(self): return self._cur.description


class PgConnection:
    """A psycopg connection wearing the sqlite3 Connection interface."""

    def __init__(self, dsn: str, row_factory=None):
        import psycopg
        from psycopg.rows import dict_row
        self._c = psycopg.connect(dsn, autocommit=False,
                                  row_factory=dict_row if row_factory else None)
        self._dict = bool(row_factory)

    def execute(self, sql, params=()):
        cur = self._c.cursor()
        cur.execute(_to_pg(sql), tuple(params))
        return _Cursor(cur)

    def executemany(self, sql, seq):
        cur = self._c.cursor()
        cur.executemany(_to_pg(sql), [tuple(p) for p in seq])
        return _Cursor(cur)

    def commit(self):   self._c.commit()
    def rollback(self): self._c.rollback()
    def close(self):    self._c.close()

    @property
    def row_factory(self): return self._dict
    @row_factory.setter
    def row_factory(self, v):
        # sqlite3 callers set this after connecting; psycopg fixes it at connect time, so
        # honour it by swapping the cursor factory rather than silently ignoring it.
        from psycopg.rows import dict_row, tuple_row
        self._c.row_factory = dict_row if v else tuple_row
        self._dict = bool(v)


# Integrity errors differ by driver; call sites catch this tuple instead of sqlite3's class.
def integrity_errors():
    errs = [sqlite3.IntegrityError]
    try:
        import psycopg
        errs.append(psycopg.errors.IntegrityError)
    except Exception:
        pass
    return tuple(errs)


def connect(path_or_dsn: str | None = None, *, row_factory=None):
    """Postgres when DATABASE_URL is set (or a postgres DSN is passed), else SQLite."""
    dsn = path_or_dsn or os.environ.get("DATABASE_URL")
    if dsn and str(dsn).startswith(("postgres://", "postgresql://")):
        return PgConnection(str(dsn), row_factory=row_factory)
    con = sqlite3.connect(path_or_dsn)
    if row_factory:
        con.row_factory = sqlite3.Row
    return con
