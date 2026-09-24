"""HTTP Basic authentication with a hashed password and a simple brute-force limiter."""
import hmac
import time

from flask import Response, request
from werkzeug.security import check_password_hash

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
OPEN_PATHS = {"/healthz"}
MAX_FAILS = 5
WINDOW_SECONDS = 300


def enabled(cfg):
    """Return True when a username and password hash are configured."""
    a = (cfg or {}).get("auth") or {}
    return bool(a.get("username") and a.get("password_hash"))


def setup(app, cfg):
    """Install a before-request hook that enforces authentication, if configured."""
    if not enabled(cfg):
        return
    user, pw_hash = cfg["auth"]["username"], cfg["auth"]["password_hash"]
    fails = {}

    @app.before_request
    def require_login():
        if request.path in OPEN_PATHS:
            return None
        ip = request.remote_addr or "?"
        now = time.time()
        recent = [t for t in fails.get(ip, []) if now - t < WINDOW_SECONDS]
        fails[ip] = recent
        if len(recent) >= MAX_FAILS:
            return Response("Too many failed attempts. Try again later.", 429)
        a = request.authorization
        ok = bool(a and a.username is not None and a.password is not None
                  and hmac.compare_digest(a.username, user) and check_password_hash(pw_hash, a.password))
        if ok:
            return None
        if a:
            fails[ip].append(now)
        return Response("Authentication required", 401, {"WWW-Authenticate": 'Basic realm="Purrmox"'})
