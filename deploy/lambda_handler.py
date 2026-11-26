"""Lambda entrypoint.

With DATABASE_URL set the service talks to Postgres and there is nothing to build: the
state is shared, which is the whole point. Without it the bundle falls back to building a
SQLite working copy in /tmp, which is useful for a local container run but is NOT safe for
a deployed demo - /tmp belongs to one execution environment, so two warm containers hold
two different databases and can sell the same slot twice. Reserved concurrency does not
fix that; it caps simultaneous executions, not the number of warm environments.
"""
import os, pathlib, sys
sys.path.insert(0, os.path.dirname(__file__))

if not os.environ.get("DATABASE_URL"):
    os.environ.setdefault("SETUHAUL_SNAPSHOT", str(
        pathlib.Path(__file__).parent / "data/snapshot/setuhaul_freight_operations.db"))
    WORK = pathlib.Path("/tmp/work"); WORK.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("SETUHAUL_BASE_DB", str(WORK / "setuhaul_work.db"))
    os.environ.setdefault("SETUHAUL_LIVE_DB", str(WORK / "live.db"))
    if not pathlib.Path(os.environ["SETUHAUL_BASE_DB"]).exists():
        import build_workdb_shim        # noqa: F401

from app.server import app
from mangum import Mangum
handler = Mangum(app, lifespan="off")
