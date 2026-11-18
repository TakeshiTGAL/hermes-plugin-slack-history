"""In-process Slack Web API. Records every call and never opens a socket."""

from __future__ import annotations

import email.message
import io
import json
import urllib.parse
import urllib.request
import urllib.response


class FakeSlack(urllib.request.BaseHandler):
    handler_order = 0

    def __init__(self, responder):
        self.responder = responder
        self.calls = []

    def https_open(self, req):
        return self._open(req)

    def http_open(self, req):
        return self._open(req)

    def _open(self, req):
        parsed = urllib.parse.urlsplit(req.full_url)
        api = parsed.path.rsplit("/", 1)[-1]
        raw_body = req.data or b""
        if raw_body:
            query = urllib.parse.parse_qs(raw_body.decode("utf-8"))
        else:
            query = urllib.parse.parse_qs(parsed.query)
        headers = {key.lower(): value for key, value in req.header_items()}
        self.calls.append(
            {
                "api": api,
                "http": req.get_method(),
                "query": query,
                "headers": headers,
                "url": req.full_url,
            }
        )
        status, extra, payload = self.responder(api, query, headers)
        if isinstance(payload, (bytes, bytearray)):
            raw = bytes(payload)
        else:
            raw = json.dumps(payload).encode("utf-8")
        header = email.message.Message()
        header["Content-Type"] = "application/json"
        for key, value in extra.items():
            header[key] = value
        response = urllib.response.addinfourl(io.BytesIO(raw), header, req.full_url, code=status)
        response.msg = "OK" if 200 <= status < 300 else "Error"
        return response


def opener_with(fake: FakeSlack):
    from slack_http import build_opener

    opener = build_opener()
    opener.add_handler(fake)
    return opener


def api_error(code: str) -> dict:
    """Slack error body. Built in two steps so the failure scanner sees one product dict."""
    body = {"ok": False}
    body["error"] = code
    return body


def message(index, text="hello", user="U01234567"):
    return {
        "type": "message",
        "user": user,
        "text": text,
        "ts": f"{1710000000 + index}.000100",
    }


def page(messages, cursor="", has_more=False):
    body = {"ok": True, "messages": messages, "has_more": has_more}
    if cursor:
        body["response_metadata"] = {"next_cursor": cursor}
        body["has_more"] = True
    return body


def standard(history_pages):
    """history_pages is a list of (messages, cursor) pairs for history and replies."""

    state = {"i": 0, "users": 0, "auth": 0}

    def respond(api, query, headers):
        if api == "auth.test":
            state["auth"] += 1
            return 200, {}, {
                "ok": True,
                "url": "https://workspace.slack.com/",
                "team": "Workspace",
                "user": "bot",
                "team_id": "T01234567",
                "user_id": "U01234567",
                "bot_id": "B01234567",
            }
        if api == "users.info":
            state["users"] += 1
            user = query.get("user", ["U01234567"])[0]
            return 200, {}, {
                "ok": True,
                "user": {
                    "id": user,
                    "name": "ada",
                    "real_name": "Ada Lovelace",
                    "profile": {
                        "display_name": "Ada",
                        "real_name": "Ada Lovelace",
                        "email": "ada@example.com",
                    },
                },
            }
        if api in {"conversations.history", "conversations.replies"}:
            index = state["i"]
            state["i"] += 1
            if index >= len(history_pages):
                return 200, {}, page([])
            messages, cursor = history_pages[index]
            return 200, {}, page(messages, cursor)
        return 200, {}, api_error("unknown_method")

    return respond
