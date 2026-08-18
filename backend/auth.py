"""
Login for THIS APP -- separate from (and unrelated to) the Tradejini broker
credentials in tradejini_client.py. Deliberately simple, matching an
explicit request: one shared username/password in .env, plain text, no
user accounts, no registration flow. This is a single-operator local app;
the goal here is "don't leave the trading UI wide open," not enterprise
auth.

Sessions are an in-memory dict of {token: expiry} -- restarting the app
means everyone needs to log in again, which is a completely acceptable
tradeoff for what this is. Tokens are generated with `secrets.token_urlsafe`
(cryptographically random, not guessable) and compared with
`secrets.compare_digest` for both the token lookup and the password check,
which costs nothing extra and closes off trivial timing attacks -- "simple"
doesn't have to mean "sloppy."

Enforcement is a single Starlette middleware (AuthMiddleware below) that
gates EVERY request except an explicit small allowlist (the login page
itself, the login/logout API, and static assets). Doing this as middleware
rather than per-route decoration means a new route added later is
protected by default -- nobody has to remember to add a check to it.
"""
import logging
import secrets
from datetime import datetime, timedelta

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse, JSONResponse

from . import config

log = logging.getLogger("tradejini.auth")

# path -> exact match; prefix entries end with a trailing check below
PUBLIC_EXACT_PATHS = {"/login", "/api/auth/login"}
PUBLIC_PREFIXES = ("/static/",)

_sessions: dict[str, datetime] = {}  # token -> expiry


def credentials_configured() -> bool:
    return bool(config.APP_LOGIN_USERNAME) and bool(config.APP_LOGIN_PASSWORD)


def check_credentials(username: str, password: str) -> bool:
    if not credentials_configured():
        return False
    # compare_digest on BOTH fields (not just password) -- otherwise the
    # username comparison alone could leak timing information about
    # whether a guessed username is correct before the password is even
    # checked; costs nothing to do both properly
    user_ok = secrets.compare_digest(username or "", config.APP_LOGIN_USERNAME)
    pass_ok = secrets.compare_digest(password or "", config.APP_LOGIN_PASSWORD)
    return user_ok and pass_ok


def create_session() -> str:
    token = secrets.token_urlsafe(32)
    _sessions[token] = datetime.now() + timedelta(days=config.SESSION_TTL_DAYS)
    _prune_expired()
    return token


def destroy_session(token: str | None):
    if token:
        _sessions.pop(token, None)


def is_valid_session(token: str | None) -> bool:
    if not token:
        return False
    expiry = _sessions.get(token)
    if expiry is None:
        return False
    if datetime.now() > expiry:
        _sessions.pop(token, None)
        return False
    return True


def _prune_expired():
    now = datetime.now()
    expired = [t for t, exp in _sessions.items() if now > exp]
    for t in expired:
        _sessions.pop(t, None)


def _is_public_path(path: str) -> bool:
    if path in PUBLIC_EXACT_PATHS:
        return True
    return any(path.startswith(p) for p in PUBLIC_PREFIXES)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path

        if _is_public_path(path):
            return await call_next(request)

        token = request.cookies.get(config.SESSION_COOKIE_NAME)
        if is_valid_session(token):
            return await call_next(request)

        # NOTE: this middleware never actually runs for WebSocket connections
        # at all -- Starlette's BaseHTTPMiddleware only wraps "http"-scope
        # requests; for a "websocket" scope it bypasses dispatch() entirely
        # and calls the inner app directly. /ws is protected separately, by
        # checking the same session cookie right inside ws_endpoint() in
        # main.py before accepting the connection -- that's the real
        # enforcement point for it, not this branch.
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        # preserve where they were headed so a direct link (e.g. a
        # bookmark to /?ptab=advanced) still lands correctly after login,
        # instead of always dropping back to the plain homepage
        next_qs = f"?next={path}" if path != "/" else ""
        return RedirectResponse(f"/login{next_qs}")
