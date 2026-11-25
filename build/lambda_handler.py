"""Lambda entrypoint. The working database is built once per cold container into /tmp, because
the Lambda bundle itself is read-only. State therefore lives for the life of the container: fine
for a demo, and the reason the production design puts this on Postgres instead."""
import os, pathlib, sys
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("SETUHAUL_SNAPSHOT",
                      str(pathlib.Path(__file__).parent / "data/snapshot/setuhaul_freight_operations.db"))
WORK = pathlib.Path("/tmp/work"); WORK.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("SETUHAUL_BASE_DB", str(WORK / "setuhaul_work.db"))
os.environ.setdefault("SETUHAUL_LIVE_DB", str(WORK / "live.db"))

if not pathlib.Path(os.environ["SETUHAUL_BASE_DB"]).exists():
    import build_workdb_shim        # noqa: F401

from app.server import app
from mangum import Mangum
handler = Mangum(app, lifespan="off")
