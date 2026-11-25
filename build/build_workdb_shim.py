"""Runs the real build script against /tmp, without editing it for the Lambda case."""
import os, pathlib, runpy, sys
here = pathlib.Path(__file__).parent
src, dst = pathlib.Path(os.environ["SETUHAUL_SNAPSHOT"]), pathlib.Path(os.environ["SETUHAUL_BASE_DB"])
import shutil, sqlite3
dst.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(src, dst)
con = sqlite3.connect(dst)
con.executescript(open(here / "ddl.sql").read())
con.commit(); con.close()
