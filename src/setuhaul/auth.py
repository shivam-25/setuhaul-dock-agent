"""Password and session handling.

Decisions worth stating, because each one rules something out:

PBKDF2-HMAC-SHA256, not bcrypt or argon2. Those are better algorithms, but both ship as
native wheels, and a Lambda zip built on macOS carries a macOS binary that will not load on
Amazon Linux. PBKDF2 is in the standard library, so the deployment artefact stays pure
Python. 600k iterations is the OWASP figure for SHA-256; pw_iterations is stored per row so
the cost can be raised later without invalidating anyone's password.

Sessions are server-side rows, not stateless JWTs alone. A pure JWT cannot be revoked before
it expires, so "change my password and sign me out everywhere" would be a lie. The cookie
carries a signed JWT whose jti names a row in app_sessions; deleting the row ends the
session immediately. The signature still does the cheap work of rejecting forgeries before
any database hit.

The JWT is signed HS256 with a secret from the environment. There is no RS256 keypair
because there is one issuer and one verifier - the same process.
"""
from __future__ import annotations
import base64, hashlib, hmac, json, os, secrets, uuid
from datetime import datetime, timedelta, timezone

ITERATIONS = 600_000
SESSION_HOURS = 12
RESET_HOURS = 24
MAX_FAILED = 8
LOCK_MINUTES = 15


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def secret() -> bytes:
    s = os.environ.get("SETUHAUL_JWT_SECRET")
    if not s:
        raise RuntimeError("SETUHAUL_JWT_SECRET is not set; refusing to sign with a default")
    return s.encode()


# ---------------------------------------------------------------- passwords
def hash_password(password: str, *, iterations: int = ITERATIONS) -> tuple[str, str, int]:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return _b64u(salt), _b64u(dk), iterations


def verify_password(password: str, salt_b64: str, hash_b64: str, iterations: int) -> bool:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), _b64u_dec(salt_b64), iterations)
    # Constant time: a timing difference here leaks how much of the digest matched.
    return hmac.compare_digest(dk, _b64u_dec(hash_b64))


def password_problem(pw: str, username: str) -> str | None:
    """Returns why a password is unacceptable, or None. Length first, per NIST: a long
    passphrase beats a short one with a symbol in it."""
    if len(pw) < 12:                    return "must be at least 12 characters"
    if len(pw) > 200:                   return "must be under 200 characters"
    if pw.lower() == username.lower():  return "cannot be your username"
    if pw.lower() in _COMMON:           return "is too common; pick something unguessable"
    return None


_COMMON = {"password", "password123", "123456789012", "qwertyuiop12", "letmein12345",
           "changeme1234", "welcome12345", "setuhaul1234", "administrator"}


# ---------------------------------------------------------------- tokens
def sign_jwt(payload: dict) -> str:
    head = _b64u(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64u(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(secret(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64u(sig)}"


def verify_jwt(token: str) -> dict | None:
    try:
        head, body, sig = token.split(".")
    except ValueError:
        return None
    want = hmac.new(secret(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(_b64u_dec(sig), want):
        return None
    try:
        claims = json.loads(_b64u_dec(body))
    except Exception:
        return None
    if claims.get("exp", 0) < int(datetime.now(timezone.utc).timestamp()):
        return None
    return claims


def reset_token() -> tuple[str, str]:
    """Returns (token to hand out, sha256 to store). The token itself is never persisted,
    so leaking app_password_resets does not let anyone reset a password."""
    tok = secrets.token_urlsafe(32)
    return tok, hashlib.sha256(tok.encode()).hexdigest()


def sha(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def iso(dt: datetime) -> str:
    return dt.isoformat()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def session_expiry(now: datetime | None = None) -> datetime:
    return (now or now_utc()) + timedelta(hours=SESSION_HOURS)
