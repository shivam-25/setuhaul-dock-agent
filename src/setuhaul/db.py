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
# `IS NOT %s`, but never `IS NOT NULL/TRUE/FALSE/DISTINCT`, which Postgres accepts as-is.
_IS_NOT_EXPR = re.compile(r"\bIS\s+NOT\s+(?!NULL\b|TRUE\b|FALSE\b|DISTINCT\b)", re.I)


def _to_pg(sql: str) -> str:
    """`?` -> `%s`, leaving `?` inside string literals alone, and `%` escaped for psycopg."""
    sql = sql.replace("%", "%%")
    out = _PLACEHOLDER.sub(lambda m: "%s" if m.group(0) == "?" else m.group(0), sql)
    out = out.replace("BEGIN IMMEDIATE", "BEGIN")
    # SQLite spells null-safe comparison `IS` / `IS NOT` against any expression. Postgres
    # allows IS NOT only before NULL/TRUE/FALSE and spells the general form
    # IS DISTINCT FROM. Both engines mean the same thing: NULL compares without poisoning
    # the result, which is exactly why the slot-lock query uses it.
    out = _IS_NOT_EXPR.sub("IS DISTINCT FROM ", out)
    return out


class DualRow(dict):
    """A row readable both ways, because sqlite3.Row is.

    sqlite3.Row supports row["name"] AND row[0]; psycopg's dict_row supports only the first
    and its tuple_row only the second. Call sites in this codebase use both styles - the
    metrics helpers index [0] on a COUNT, the agent reads columns by name - so a row that
    answers only one of them breaks working code in a way the SQLite tests cannot catch.
    Subclassing dict keeps dict(row), .get and ** working unchanged.
    """
    __slots__ = ("_order",)

    def __init__(self, mapping, order):
        super().__init__(mapping)
        self._order = order

    def __getitem__(self, k):
        if isinstance(k, int):
            return super().__getitem__(self._order[k])
        if isinstance(k, slice):
            return tuple(dict.__getitem__(self, c) for c in self._order[k])
        return super().__getitem__(k)

    def keys(self):   return list(self._order)
    def __iter__(self):
        # sqlite3.Row iterates values, not keys; dict iterates keys. Values is the one that
        # makes tuple(row) and sequence unpacking behave as the existing code expects.
        # dict.__getitem__ explicitly: a zero-argument super() inside a generator
        # expression runs in its own scope and loses the class binding.
        return iter([dict.__getitem__(self, c) for c in self._order])


def dual_row(cur):
    cols = [d[0] for d in cur.description] if cur.description else []
    def make(values):
        return DualRow(dict(zip(cols, values)), cols)
    return make


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
        self._c = psycopg.connect(dsn, autocommit=False,
                                  row_factory=dual_row if row_factory else None)
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
        from psycopg.rows import tuple_row
        self._c.row_factory = dual_row if v else tuple_row
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
