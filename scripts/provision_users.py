"""Create one login per actor, with a generated first password.

Credentials are written to credentials.local.md, which is gitignored. They are never
printed to a terminal transcript and never committed: a demo password in git history is
still a password in git history.

Every account starts with must_change_pw=1, so the first sign-in forces a change. That is
what makes handing these out safe.
"""
import sys, os, pathlib, secrets, string
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from setuhaul.db import connect
from setuhaul import auth

ROOT = pathlib.Path(__file__).resolve().parents[1]
DSN  = os.environ.get("DATABASE_URL")
if not DSN: sys.exit("DATABASE_URL not set (source .env)")

WORDS = ("dock yard route cargo axle crane pallet freight haul depot ramp "
         "gate ledger transit bay").split()

def passphrase() -> str:
    return "-".join(secrets.choice(WORDS) for _ in range(3)) + "-" + str(secrets.randbelow(90) + 10)

con = connect(DSN, row_factory=True)
now = auth.iso(auth.now_utc())
rows = []

drivers = con.execute(
    "SELECT d.driver_id, d.driver_name, d.phone FROM drivers d ORDER BY d.driver_id").fetchall()
coords = con.execute(
    "SELECT contact_id, facility_id, contact_name, contact_role FROM facility_contacts "
    "ORDER BY contact_id").fetchall()

def upsert(username, role, display, driver_id=None, facility_id=None):
    pw = passphrase()
    salt, h, iters = auth.hash_password(pw)
    uid = auth.new_id("USR")
    existing = con.execute("SELECT user_id FROM app_users WHERE username=?", (username,)).fetchone()
    if existing:
        uid = existing["user_id"]
        con.execute("UPDATE app_users SET pw_salt=?, pw_hash=?, pw_iterations=?, "
                    "must_change_pw=1, failed_logins=0, locked_until=NULL, is_active=1, "
                    "updated_at=? WHERE user_id=?", (salt, h, iters, now, uid))
    else:
        con.execute(
            "INSERT INTO app_users(user_id,username,role,driver_id,facility_id,display_name,"
            "pw_algo,pw_iterations,pw_salt,pw_hash,must_change_pw,failed_logins,is_active,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,'pbkdf2_sha256',?,?,?,1,0,1,?,?)",
            (uid, username, role, driver_id, facility_id, display, iters, salt, h, now, now))
    rows.append((username, role, display, pw))
    return uid

for d in drivers:
    uname = d["driver_id"].lower()                    # drv001 ... drv015
    upsert(uname, "driver", d["driver_name"], driver_id=d["driver_id"])

# One SetuHaul operations coordinator: sees every driver, every facility, every alert.
upsert("ops", "coordinator", "SetuHaul Operations")

# One warehouse login per facility contact, scoped to that facility only.
seen = set()
for c in coords:
    fac = c["facility_id"]
    if fac in seen: continue
    seen.add(fac)
    uname = "wh-" + fac.split("-")[1].lower()          # wh-jai, wh-ggn
    upsert(uname, "warehouse", f"{c['contact_name']} ({fac})", facility_id=fac)

con.commit()

out = ROOT / "credentials.local.md"
lines = ["# SetuHaul demo logins", "",
         "Generated locally. This file is gitignored and must stay that way.",
         "Every account is forced to change its password on first sign-in.", "",
         "| username | role | who | first password |", "|---|---|---|---|"]
for u, r, disp, pw in rows:
    lines.append(f"| `{u}` | {r} | {disp} | `{pw}` |")
out.write_text("\n".join(lines) + "\n")
out.chmod(0o600)
print(f"{len(rows)} accounts provisioned -> {out}")
print("  drivers    :", sum(1 for r in rows if r[1] == "driver"))
print("  coordinator:", sum(1 for r in rows if r[1] == "coordinator"))
print("  warehouse  :", sum(1 for r in rows if r[1] == "warehouse"))
con.close()
