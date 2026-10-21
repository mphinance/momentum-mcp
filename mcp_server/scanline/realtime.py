"""Optional real-time mode: a TradingView session cookie for the screener.

Without a cookie TradingView serves delayed data. With the account's
`sessionid` cookie the same scanner endpoint serves whatever real-time feeds
that account is entitled to. This module only stores and hands out the cookie.

Hard rule: the cookie value is a login credential. It must never reach a
response body, a log line, an exception message or a cache key. The only thing
that leaves this module for display is the MODE string. The one consumer of the
raw value is scanline/screener.py, which passes it to requests.

Sources, in order: env SCANLINE_TV_SESSIONID (+ optional
SCANLINE_TV_SESSIONID_SIGN), then a 0600 JSON file at
$XDG_CONFIG_HOME/scanline/tv_cookie.json (default ~/.config/scanline/...).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path

MODE_DELAYED = "delayed"
MODE_REALTIME = "realtime"
MODE_REJECTED = "cookie_rejected"

# After TradingView refuses a cookie we stop sending it for this long, then
# try again, so one transient 403 cannot disable real-time mode forever.
REJECT_COOLDOWN_SECONDS = 600

# Cookie values are opaque tokens. Allow only token-ish characters so a pasted
# "sessionid=abc; other=..." header or a value with whitespace is refused.
_VALUE_RE = re.compile(r"^[A-Za-z0-9._~+/=:-]{8,512}$")

_lock = threading.Lock()
_rejected_until = 0.0


def config_path() -> Path:
    """Where the cookie file lives (it may not exist)."""
    base = os.environ.get("XDG_CONFIG_HOME", "").strip()
    root = Path(base) if base else Path.home() / ".config"
    return root / "scanline" / "tv_cookie.json"


def _from_env() -> dict | None:
    sid = os.environ.get("SCANLINE_TV_SESSIONID", "").strip()
    if not sid:
        return None
    out = {"sessionid": sid}
    sign = os.environ.get("SCANLINE_TV_SESSIONID_SIGN", "").strip()
    if sign:
        out["sessionid_sign"] = sign
    return out


def _from_file() -> dict | None:
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    sid = data.get("sessionid")
    if not isinstance(sid, str) or not sid:
        return None
    out = {"sessionid": sid}
    sign = data.get("sessionid_sign")
    if isinstance(sign, str) and sign:
        out["sessionid_sign"] = sign
    return out


def stored_cookies() -> dict | None:
    """The configured cookie jar, or None. Callers must not log or return it."""
    return _from_env() or _from_file()


def configured() -> bool:
    """True when a cookie is stored, whether or not TradingView accepts it."""
    return stored_cookies() is not None


def active_cookies() -> dict | None:
    """The cookie to send right now: None when unset or in rejection cooldown."""
    with _lock:
        if time.time() < _rejected_until:
            return None
    return stored_cookies()


def mode() -> str:
    """delayed | realtime | cookie_rejected. Safe to expose and to cache on."""
    if not configured():
        return MODE_DELAYED
    with _lock:
        if time.time() < _rejected_until:
            return MODE_REJECTED
    return MODE_REALTIME


def mark_rejected() -> None:
    """TradingView refused the cookie: fall back to cookieless for a while."""
    global _rejected_until
    with _lock:
        _rejected_until = time.time() + REJECT_COOLDOWN_SECONDS


def _reset_rejection() -> None:
    global _rejected_until
    with _lock:
        _rejected_until = 0.0


def validate_value(value: object) -> bool:
    """True for a plausible cookie token. Never echoes the value."""
    return isinstance(value, str) and bool(_VALUE_RE.match(value))


def save_cookie(sessionid: str, sessionid_sign: str | None = None) -> None:
    """Persist the cookie to a 0600 file (atomic replace). Raises ValueError
    with a fixed message on bad input, never including the value."""
    if not validate_value(sessionid):
        raise ValueError("sessionid is not a valid cookie value")
    if sessionid_sign is not None and not validate_value(sessionid_sign):
        raise ValueError("sessionid_sign is not a valid cookie value")
    payload = {"sessionid": sessionid}
    if sessionid_sign:
        payload["sessionid_sign"] = sessionid_sign
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tv_cookie.")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _reset_rejection()


def clear_cookie() -> None:
    """Delete the cookie file. An env-provided cookie cannot be cleared here."""
    try:
        config_path().unlink()
    except FileNotFoundError:
        pass
    _reset_rejection()
