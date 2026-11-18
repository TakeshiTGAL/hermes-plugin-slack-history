"""Slack Web API client for four read methods.

The token is supplied by the caller for each attempt. This module does not
read the environment and does not follow redirects.
"""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request

ALLOWED_METHODS = frozenset(
    {
        "auth.test",
        "conversations.history",
        "conversations.replies",
        "users.info",
    }
)
_GET_METHODS = frozenset(
    {
        "conversations.history",
        "conversations.replies",
        "users.info",
    }
)
_PARAMS = {
    "auth.test": frozenset(),
    "conversations.history": frozenset({"channel", "cursor", "limit"}),
    "conversations.replies": frozenset({"channel", "cursor", "limit", "ts"}),
    "users.info": frozenset({"user"}),
}
DEFAULT_ORIGIN = "https://slack.com"
TIMEOUT_SECONDS = 10
MAX_RESPONSE_BYTES = 1_000_000
RETRY_CAP_SECONDS = 5


class SlackResponse:
    """One Web API attempt. ``error`` is empty when ``body`` is usable."""

    def __init__(self, body=None, error="", retry_after=None, status=0):
        self.body = body if isinstance(body, dict) else None
        self.error = error
        self.retry_after = retry_after
        self.status = status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect. A 3xx must not carry the bot token to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def build_opener():
    """Opener that ignores proxy environment variables and does not follow redirects."""
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect(),
    )


def checked_origin(base_url: str) -> str:
    """Return the origin only for Slack or a loopback test server."""
    if not isinstance(base_url, str) or not base_url or any(ord(ch) < 33 or ord(ch) == 127 for ch in base_url):
        raise ValueError("host")
    parts = urllib.parse.urlsplit(base_url)
    host = parts.hostname
    if host is None or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("host")
    if host != host.strip() or host != host.lower():
        raise ValueError("host")
    host = host.rstrip(".")
    if parts.path not in ("", "/"):
        raise ValueError("host")
    if parts.scheme == "https" and host == "slack.com" and parts.port in (None, 443):
        return DEFAULT_ORIGIN
    if (
        parts.scheme == "http"
        and host in ("127.0.0.1", "localhost")
        and isinstance(parts.port, int)
        and 1 <= parts.port <= 65535
    ):
        return f"http://{host}:{parts.port}"
    raise ValueError("host")


def _retry_after(headers):
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    raw = getter("Retry-After") if getter else None
    if raw is None:
        return None
    text = str(raw).strip()
    if text.isdigit():
        return int(text)
    return None


def _query(method: str, params: dict) -> str:
    allowed = _PARAMS[method]
    if set(params) - allowed:
        raise ValueError("params")
    pairs = []
    for key in sorted(params):
        value = params[key]
        if not isinstance(value, str) or value == "" or any(ord(ch) < 33 or ord(ch) == 127 for ch in value):
            raise ValueError("params")
        if len(value) > 256:
            raise ValueError("params")
        pairs.append((key, value))
    return urllib.parse.urlencode(pairs)


def _read_bounded(stream) -> tuple[bytes, bool]:
    chunks = []
    total = 0
    while total <= MAX_RESPONSE_BYTES:
        block = stream.read(65536)
        if not block:
            return b"".join(chunks), False
        total += len(block)
        chunks.append(block)
    return b"", True


def _once(method: str, token: str, params: dict, opener, base_url: str) -> SlackResponse:
    if method not in ALLOWED_METHODS:
        return SlackResponse(error="method_rejected")
    try:
        origin = checked_origin(base_url)
        query = _query(method, params)
    except ValueError as exc:
        return SlackResponse(error="host_rejected" if str(exc) == "host" else "bad_params")
    http_method = "GET" if method in _GET_METHODS else "POST"
    url = origin + "/api/" + method
    data = None
    if http_method == "GET":
        if query:
            url += "?" + query
    else:
        data = query.encode("ascii") if query else b""
    request = urllib.request.Request(url, data=data, method=http_method)
    request.add_header("Authorization", "Bearer " + token)
    request.add_header("Accept", "application/json")
    try:
        response = opener.open(request, timeout=TIMEOUT_SECONDS)
    except urllib.error.HTTPError as exc:
        status = int(getattr(exc, "code", 0) or 0)
        headers = getattr(exc, "headers", None)
        if 300 <= status < 400:
            return SlackResponse(error="redirect", status=status)
        if status == 429:
            return SlackResponse(error="ratelimited", status=429, retry_after=_retry_after(headers))
        raw = b""
        too_big = False
        if exc.fp is not None:
            try:
                raw, too_big = _read_bounded(exc.fp)
            except (http.client.HTTPException, OSError):
                return SlackResponse(error="transport", status=status)
        if too_big:
            return SlackResponse(error="too_large", status=status)
        return _interpret(status, headers, raw)
    except (urllib.error.URLError, TimeoutError, OSError):
        return SlackResponse(error="transport")
    status = 0
    try:
        status = int(getattr(response, "status", None) or getattr(response, "code", 200) or 200)
        headers = getattr(response, "headers", None)
        if 300 <= status < 400:
            return SlackResponse(error="redirect", status=status)
        try:
            raw, too_big = _read_bounded(response)
        except (http.client.HTTPException, OSError):
            return SlackResponse(error="transport", status=status)
    finally:
        close = getattr(response, "close", None)
        if close:
            close()
    if too_big:
        return SlackResponse(error="too_large", status=status)
    if status == 429:
        return SlackResponse(error="ratelimited", status=429, retry_after=_retry_after(headers))
    return _interpret(status, headers, raw)


def _interpret(status: int, headers, raw: bytes) -> SlackResponse:
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, AttributeError):
        return SlackResponse(error="bad_response", status=status)
    if not isinstance(body, dict):
        return SlackResponse(error="bad_response", status=status)
    if body.get("ok") is True:
        return SlackResponse(body=body, status=status)
    error = body.get("error")
    if not isinstance(error, str) or not error:
        return SlackResponse(error="bad_response", status=status)
    retry = _retry_after(headers) if error == "ratelimited" else None
    return SlackResponse(error=error, status=status, retry_after=retry)


def slack_call(method: str, token_fn, params: dict, *, opener=None, base_url: str = DEFAULT_ORIGIN, sleeper=None) -> SlackResponse:
    """Call one allowlisted method. ``token_fn`` runs on every attempt, including a retry."""
    opener = opener or build_opener()
    sleeper = sleeper or time.sleep
    waited = False
    while True:
        try:
            token = token_fn()
        except Exception as exc:
            if type(exc).__name__ in {"UnscopedSecretError", "_Unscoped"}:
                return SlackResponse(error="unscoped")
            raise
        if not isinstance(token, str) or token == "" or any(ch in token for ch in "\r\n\x00"):
            return SlackResponse(error="missing_token")
        result = _once(method, token, params, opener, base_url)
        if result.error != "ratelimited" or waited:
            return result
        wait = result.retry_after
        if wait is None or wait > RETRY_CAP_SECONDS:
            return result
        sleeper(wait)
        waited = True
