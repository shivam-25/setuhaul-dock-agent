"""Sign-in, sessions and per-user scoping for the FastAPI app.

The cookie is HttpOnly, Secure and SameSite=Lax, so page script cannot read the token and
it is not sent on cross-site form posts. It carries a signed JWT whose jti names a row in
app_sessions; the signature rejects forgeries without touching the database, and the row
is what makes "sign me out everywhere" true rather than aspirational.

Scoping is enforced here, once, rather than in each endpoint. A driver sees their own
shipments and nothing else; a warehouse login sees its own facility; a SetuHaul
coordinator sees everything, which is the whole point of the role.
"""
from __future__ import annotations
import os
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from setuhaul import auth

router = APIRouter(prefix="/api/auth", tags=["auth"])
COOKIE = "setuhaul_session"


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


class ChangeBody(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


class ResetBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)


def _set_cookie(resp: Response, token: str):
    resp.set_cookie(
        COOKIE, token, httponly=True, secure=True, samesite="lax",
        max_age=auth.SESSION_HOURS * 3600, path="/")


def current_user(request: Request) -> dict:
    """Resolve the caller, or 401. Checks the signature first (cheap) and only then the
    session row, so a forged cookie never reaches the database."""
    from .server import db
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "not signed in")
    claims = auth.verify_jwt(token)
    if not claims:
        raise HTTPException(401, "session invalid or expired")
    con = db()
    try:
        row = con.execute(
            "SELECT s.jti, s.expires_at, s.revoked_at, u.user_id, u.username, u.role, "
            "       u.driver_id, u.facility_id, u.display_name, u.must_change_pw, u.is_active "
            "FROM app_sessions s JOIN app_users u ON u.user_id = s.user_id "
            "WHERE s.jti = ?", (claims.get("jti"),)).fetchone()
        if not row:
            raise HTTPException(401, "session not found")
        d = dict(row)
        if d.get("revoked_at") or not d.get("is_active"):
            raise HTTPException(401, "session revoked")
        if d["expires_at"] < auth.iso(auth.now_utc()):
            raise HTTPException(401, "session expired")
        return d
    finally:
        con.close()


def require_role(*roles: str):
    def dep(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in roles:
            # 404-style vagueness is not helpful here; the caller is authenticated and
            # simply lacks the role, which is worth saying plainly.
            raise HTTPException(403, f"this needs one of: {', '.join(roles)}")
        return user
    return dep


def scope_clause(user: dict) -> tuple[str, list]:
    """The SQL fragment that limits rows to what this user may see. One definition, used
    everywhere, so a new endpoint cannot accidentally leak another driver's trips."""
    if user["role"] == "driver":
        return ("s.driver_id = ?", [user["driver_id"]])
    if user["role"] == "warehouse":
        return ("s.destination_facility_id = ?", [user["facility_id"]])
    return ("1=1", [])            # coordinator: full visibility, by design


@router.post("/login")
def login(body: LoginBody, response: Response, request: Request):
    from .server import db
    con = db()
    try:
        u = con.execute(
            "SELECT user_id, username, role, driver_id, facility_id, display_name, "
            "       pw_iterations, pw_salt, pw_hash, must_change_pw, failed_logins, "
            "       locked_until, is_active FROM app_users WHERE username = ?",
            (body.username.strip().lower(),)).fetchone()
        now = auth.now_utc()
        # Same message and same work whether the user exists or not, so the endpoint does
        # not become a way to enumerate usernames.
        if not u or not u["is_active"]:
            auth.hash_password(body.password, iterations=1000)
            raise HTTPException(401, "username or password is wrong")
        d = dict(u)
        if d.get("locked_until") and d["locked_until"] > auth.iso(now):
            raise HTTPException(429, "too many attempts; try again shortly")
        if not auth.verify_password(body.password, d["pw_salt"], d["pw_hash"], d["pw_iterations"]):
            fails = int(d["failed_logins"]) + 1
            lock = auth.iso(now + timedelta(minutes=auth.LOCK_MINUTES)) if fails >= auth.MAX_FAILED else None
            con.execute("UPDATE app_users SET failed_logins=?, locked_until=? WHERE user_id=?",
                        (fails, lock, d["user_id"]))
            con.commit()
            raise HTTPException(401, "username or password is wrong")

        jti = auth.new_id("SES")
        exp = auth.session_expiry(now)
        con.execute("INSERT INTO app_sessions(jti,user_id,issued_at,expires_at,user_agent) "
                    "VALUES(?,?,?,?,?)",
                    (jti, d["user_id"], auth.iso(now), auth.iso(exp),
                     (request.headers.get("user-agent") or "")[:200]))
        con.execute("UPDATE app_users SET failed_logins=0, locked_until=NULL, last_login_at=? "
                    "WHERE user_id=?", (auth.iso(now), d["user_id"]))
        con.commit()
        _set_cookie(response, auth.sign_jwt(
            {"sub": d["user_id"], "role": d["role"], "jti": jti,
             "exp": int(exp.timestamp())}))
        return {"username": d["username"], "role": d["role"],
                "display_name": d["display_name"], "driver_id": d["driver_id"],
                "facility_id": d["facility_id"],
                "must_change_password": bool(d["must_change_pw"])}
    finally:
        con.close()


@router.post("/logout")
def logout(response: Response, user: dict = Depends(current_user)):
    from .server import db
    con = db()
    try:
        con.execute("UPDATE app_sessions SET revoked_at=? WHERE jti=?",
                    (auth.iso(auth.now_utc()), user["jti"]))
        con.commit()
    finally:
        con.close()
    response.delete_cookie(COOKIE, path="/")
    return {"signed_out": True}


@router.get("/me")
def me(user: dict = Depends(current_user)):
    return {k: user[k] for k in
            ("username", "role", "driver_id", "facility_id", "display_name")} | {
            "must_change_password": bool(user["must_change_pw"])}


@router.post("/change-password")
def change_password(body: ChangeBody, response: Response, user: dict = Depends(current_user)):
    from .server import db
    con = db()
    try:
        d = dict(con.execute("SELECT pw_salt, pw_hash, pw_iterations FROM app_users "
                             "WHERE user_id=?", (user["user_id"],)).fetchone())
        if not auth.verify_password(body.current_password, d["pw_salt"], d["pw_hash"],
                                    d["pw_iterations"]):
            raise HTTPException(401, "current password is wrong")
        problem = auth.password_problem(body.new_password, user["username"])
        if problem:
            raise HTTPException(400, f"new password {problem}")
        salt, h, iters = auth.hash_password(body.new_password)
        now = auth.iso(auth.now_utc())
        con.execute("UPDATE app_users SET pw_salt=?, pw_hash=?, pw_iterations=?, "
                    "must_change_pw=0, updated_at=? WHERE user_id=?",
                    (salt, h, iters, now, user["user_id"]))
        # Changing a password signs out every other device. This is the reason sessions
        # are rows: a stateless token could not be withdrawn.
        con.execute("UPDATE app_sessions SET revoked_at=? WHERE user_id=? AND jti<>? "
                    "AND revoked_at IS NULL", (now, user["user_id"], user["jti"]))
        con.commit()
        return {"changed": True, "other_sessions_signed_out": True}
    finally:
        con.close()


@router.post("/reset")
def issue_reset(body: ResetBody, actor: dict = Depends(require_role("coordinator"))):
    """A coordinator issues a one-time reset token for a driver who is locked out. Only
    its SHA-256 is stored, so the table is useless to anyone who reads it."""
    from .server import db
    con = db()
    try:
        u = con.execute("SELECT user_id, username FROM app_users WHERE username=?",
                        (body.username.strip().lower(),)).fetchone()
        if not u:
            raise HTTPException(404, "no such user")
        tok, tok_sha = auth.reset_token()
        now = auth.now_utc()
        con.execute("INSERT INTO app_password_resets(reset_id,user_id,token_sha,issued_by,"
                    "issued_at,expires_at) VALUES(?,?,?,?,?,?)",
                    (auth.new_id("RST"), u["user_id"], tok_sha, actor["user_id"],
                     auth.iso(now), auth.iso(now + timedelta(hours=auth.RESET_HOURS))))
        con.execute("UPDATE app_users SET failed_logins=0, locked_until=NULL WHERE user_id=?",
                    (u["user_id"],))
        con.commit()
        # Returned once, to the coordinator, and never stored in readable form.
        return {"username": u["username"], "reset_token": tok,
                "expires_in_hours": auth.RESET_HOURS}
    finally:
        con.close()


class RedeemBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    reset_token: str = Field(min_length=10, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


@router.post("/redeem-reset")
def redeem(body: RedeemBody):
    from .server import db
    con = db()
    try:
        u = con.execute("SELECT user_id, username FROM app_users WHERE username=?",
                        (body.username.strip().lower(),)).fetchone()
        if not u:
            raise HTTPException(400, "reset is not valid")
        now = auth.iso(auth.now_utc())
        r = con.execute("SELECT reset_id FROM app_password_resets WHERE user_id=? "
                        "AND token_sha=? AND used_at IS NULL AND expires_at > ?",
                        (u["user_id"], auth.sha(body.reset_token), now)).fetchone()
        if not r:
            raise HTTPException(400, "reset is not valid")
        problem = auth.password_problem(body.new_password, u["username"])
        if problem:
            raise HTTPException(400, f"new password {problem}")
        salt, h, iters = auth.hash_password(body.new_password)
        con.execute("UPDATE app_users SET pw_salt=?, pw_hash=?, pw_iterations=?, "
                    "must_change_pw=0, failed_logins=0, locked_until=NULL, updated_at=? "
                    "WHERE user_id=?", (salt, h, iters, now, u["user_id"]))
        con.execute("UPDATE app_password_resets SET used_at=? WHERE reset_id=?",
                    (now, r["reset_id"]))
        con.execute("UPDATE app_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                    (now, u["user_id"]))
        con.commit()
        return {"changed": True}
    finally:
        con.close()
