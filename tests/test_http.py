"""HTTP boundaries: hosts, proxy environment, redirects, and Retry-After."""

from __future__ import annotations

import email.message
import http.client
import json
import urllib.error

import pytest

from slack_http import RETRY_CAP_SECONDS, TIMEOUT_SECONDS, build_opener, slack_call
from tests.fake_slack import FakeSlack, api_error, opener_with

TOKEN = "xoxb-unit-not-real"


class _Boom:
    def open(self, *args, **kwargs):
        raise AssertionError("network")


def _token():
    return TOKEN


def test_rejected_hosts_do_not_open(monkeypatch):
    for base in (
        "http://169.254.169.254",
        "http://169.254.169.254:80",
        "http://0.0.0.0",
        "http://192.0.2.1",
        "http://10.1.2.3",
        "https://slack.com.evil.com",
        "https://user:pass@slack.com",
        "http://slack.com",
        "https://127.0.0.1:9",
        "http://127.0.0.1",
        "https://slack.com/api",
        "https://evil.example",
    ):
        result = slack_call("auth.test", _token, {}, opener=_Boom(), base_url=base)
        assert result.error == "host_rejected", base


def test_write_methods_do_not_open():
    result = slack_call("chat.postMessage", _token, {"channel": "CPUB00001"}, opener=_Boom())
    assert result.error == "method_rejected"


def test_timeout_is_passed_to_the_opener():
    seen = {}

    class _Record:
        def open(self, request, timeout=None):
            seen["timeout"] = timeout
            seen["url"] = request.full_url
            raise TimeoutError("stop")

    result = slack_call("auth.test", _token, {}, opener=_Record())
    assert result.error == "transport"
    assert seen["timeout"] == TIMEOUT_SECONDS
    assert seen["url"] == "https://slack.com/api/auth.test"
    assert TOKEN not in seen["url"]


def test_retry_after_within_cap_retries_once():
    state = {"n": 0}
    slept = []

    def respond(api, query, headers):
        state["n"] += 1
        if state["n"] == 1:
            return 429, {"Retry-After": "1"}, api_error("ratelimited")
        return 200, {}, {"ok": True, "url": "https://workspace.slack.com/"}

    fake = FakeSlack(respond)
    result = slack_call("auth.test", _token, {}, opener=opener_with(fake), sleeper=slept.append)
    assert result.error == ""
    assert result.body["ok"] is True
    assert slept == [1]
    assert len(fake.calls) == 2
    assert 1 <= RETRY_CAP_SECONDS


def test_retry_after_above_cap_does_not_sleep():
    slept = []

    def respond(api, query, headers):
        return 200, {"Retry-After": "30"}, api_error("ratelimited")

    fake = FakeSlack(respond)
    result = slack_call("auth.test", _token, {}, opener=opener_with(fake), sleeper=slept.append)
    assert result.error == "ratelimited"
    assert result.retry_after == 30
    assert slept == []
    assert len(fake.calls) == 1


def test_redirect_is_not_followed():
    def respond(api, query, headers):
        return 302, {"Location": "https://evil.example/steal"}, b""

    fake = FakeSlack(respond)
    result = slack_call("auth.test", _token, {}, opener=opener_with(fake))
    assert result.error == "redirect"
    assert len(fake.calls) == 1
    assert TOKEN not in fake.calls[0]["url"]


def test_proxy_environment_is_ignored(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy", "NO_PROXY", "ftp_proxy"):
        monkeypatch.setenv(name, "http://203.0.113.9:9")

    def respond(api, query, headers):
        return 200, {}, {"ok": True, "url": "https://workspace.slack.com/"}

    fake = FakeSlack(respond)
    result = slack_call("auth.test", _token, {}, opener=opener_with(fake))
    assert result.body["ok"] is True
    assert fake.calls[0]["url"].startswith("https://slack.com/api/auth.test")


def test_dotenv_in_the_workdir_is_not_read(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("SLACK_BOT_TOKEN=xoxb-from-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    def respond(api, query, headers):
        return 200, {}, {"ok": True}

    fake = FakeSlack(respond)
    slack_call("auth.test", _token, {}, opener=opener_with(fake))
    assert fake.calls[0]["headers"]["authorization"] == "Bearer " + TOKEN
    assert "xoxb-from-dotenv" not in json.dumps(fake.calls[0]["headers"])


def test_history_is_get_and_auth_test_is_post():
    def respond(api, query, headers):
        return 200, {}, {"ok": True, "messages": [], "has_more": False}

    fake = FakeSlack(respond)
    slack_call(
        "conversations.history",
        _token,
        {"channel": "CPUB00001", "limit": "10"},
        opener=opener_with(fake),
    )
    slack_call("auth.test", _token, {}, opener=opener_with(fake))
    methods = {call["api"]: call["http"] for call in fake.calls}
    assert methods["conversations.history"] == "GET"
    assert methods["auth.test"] == "POST"


@pytest.mark.parametrize(
    "exc",
    [http.client.IncompleteRead(b"xoxb-unit-not-real"), ConnectionResetError("reset")],
)
def test_body_disconnect_is_transport_and_hides_the_token(exc):
    class _Cut:
        def __init__(self):
            self.status = 200
            self.headers = {}
            self.closed = False

        def read(self, _n=-1):
            raise exc

        def close(self):
            self.closed = True

    cut = _Cut()

    class _Opener:
        def open(self, request, timeout=None):
            return cut

    result = slack_call("auth.test", _token, {}, opener=_Opener())
    assert result.error == "transport"
    assert result.body is None
    assert TOKEN not in repr(result.error)
    assert cut.closed is True


def test_error_body_disconnect_is_transport_and_hides_the_token():
    class _FP:
        def read(self, _n=-1):
            raise http.client.IncompleteRead(TOKEN.encode())

        def close(self):
            return None

    class _Opener:
        def open(self, request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 500, "err", email.message.Message(), _FP())

    result = slack_call("auth.test", _token, {}, opener=_Opener())
    assert result.error == "transport"
    assert result.body is None
    assert TOKEN not in (result.error or "")


def test_opener_does_not_install_an_env_proxy(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://203.0.113.9:9")
    monkeypatch.setenv("HTTP_PROXY", "http://203.0.113.9:9")
    opener = build_opener()
    installed = [getattr(handler, "proxies", None) for handler in opener.handlers]
    assert not any(isinstance(item, dict) and item for item in installed)
