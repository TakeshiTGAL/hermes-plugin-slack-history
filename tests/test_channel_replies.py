"""Channel reads include thread replies. A thread read does not use that path."""

from __future__ import annotations

import json

import history
from tests.fake_slack import FakeSlack, opener_with
from tests.test_thread_read import PARENT, _apis, _post, _read, _reply, _slack

TOKEN = "xoxb-unit-not-real"
CURRENT = "CPUB00001"


def _channel_read(top, threads, args=None, reply_page_size=None):
    """History is newest first. Each replies call returns only that parent thread."""

    def respond(api, query, headers):
        if api == "auth.test":
            return 200, {}, {
                "ok": True,
                "url": "https://workspace.slack.com/",
                "user_id": "UBOT00001",
                "bot_id": "BBOT00001",
            }
        if api == "users.info":
            user = query.get("user", ["U01234567"])[0]
            return 200, {}, {"ok": True, "user": {"id": user, "name": "ada", "profile": {"display_name": "Ada"}}}
        if api == "conversations.history":
            rows = sorted(top, key=lambda row: row["ts"], reverse=True)
            return 200, {}, {"ok": True, "messages": rows, "has_more": False}
        if api == "conversations.replies":
            parent = query["ts"][0]
            rows = list(threads.get(parent, []))
            limit = int(query["limit"][0])
            if reply_page_size is not None:
                limit = min(limit, reply_page_size)
            page = rows[:limit]
            more = len(rows) > limit
            body = {"ok": True, "messages": page, "has_more": more}
            if more:
                body["response_metadata"] = {"next_cursor": "more"}
            return 200, {}, body
        return 200, {}, {"ok": False, "error": "unknown_method"}

    env = {
        "HERMES_SESSION_PLATFORM": "slack",
        "HERMES_SESSION_CHAT_ID": CURRENT,
        "HERMES_SESSION_THREAD_ID": "",
        "HERMES_CRON_SESSION": "",
    }
    fake = FakeSlack(respond)
    body = json.loads(
        history.run(
            args or {},
            config=lambda: [],
            opener=opener_with(fake),
            sleeper=lambda _seconds: None,
            secret=lambda: TOKEN,
            session=lambda name: env.get(name, ""),
        )
    )
    return fake, body


def _reply_calls(fake):
    return [call for call in fake.calls if call["api"] == "conversations.replies"]


def test_replies_are_marked_and_sit_directly_under_the_parent():
    parent_ts = "1710000010.000100"
    parent = _post(parent_ts, "parent text", reply_count=2, thread_ts=parent_ts)
    between = _post("1710000015.000100", "between channels")
    first = _post("1710000020.000100", "reply text", thread_ts=parent_ts)
    second = _post("1710000021.000100", "second reply", thread_ts=parent_ts)
    fake, body = _channel_read([parent, between], {parent_ts: [parent, first, second]})
    text = body["transcript"]
    assert body["incomplete"] is False
    assert body["note"] == (
        "The read finished. It includes 2 replies from 1 thread, so no reply is left unread."
    )
    assert "unread_replies" not in body
    assert text.index("parent text") < text.index("reply text") < text.index("second reply")
    assert text.index("second reply") < text.index("between channels")
    assert "\n  reply [" in text
    assert [call["query"]["ts"][0] for call in _reply_calls(fake)] == [parent_ts]
    assert all("cursor" not in call["query"] for call in _reply_calls(fake))


def test_a_broadcast_already_in_the_channel_is_not_repeated():
    parent_ts = "1710000010.000100"
    parent = _post(parent_ts, "parent text", reply_count=2, thread_ts=parent_ts)
    broadcast = _post(
        "1710000020.000100",
        "also in the channel",
        thread_ts=parent_ts,
        subtype="thread_broadcast",
    )
    later = _post("1710000030.000100", "later channel")
    hidden = _post("1710000040.000100", "only in the thread", thread_ts=parent_ts)
    fake, body = _channel_read(
        [parent, broadcast, later],
        {parent_ts: [parent, broadcast, hidden]},
    )
    text = body["transcript"]
    assert body["incomplete"] is False
    assert body["note"] == (
        "The read finished. It includes 2 replies from 1 thread, so no reply is left unread."
    )
    assert "unread_replies" not in body
    assert text.count("also in the channel") == 1
    assert text.count("only in the thread") == 1
    assert text.index("parent text") < text.index("only in the thread") < text.index("later channel")
    assert [call["query"]["ts"][0] for call in _reply_calls(fake)] == [parent_ts]


def test_threads_past_the_cap_stay_in_the_note():
    assert history.MAX_REPLY_THREADS == 5
    top = []
    threads = {}
    stamps = [f"17100000{index:02d}.000100" for index in range(1, 7)]
    for stamp in stamps:
        parent = _post(stamp, f"parent {stamp}", reply_count=1, thread_ts=stamp)
        reply = _post(stamp[:-6] + "200", f"reply {stamp}", thread_ts=stamp)
        top.append(parent)
        threads[stamp] = [parent, reply]
    fake, body = _channel_read(top, threads)
    newest = list(reversed(stamps))
    assert [call["query"]["ts"][0] for call in _reply_calls(fake)] == newest[:5]
    assert body["incomplete"] is True
    assert body["unread_replies"] == [{"ts": stamps[0], "reply_count": 1}]
    assert "1 parent message has 1 reply that was not read." in body["note"]
    assert f"{stamps[0]}: 1 reply." in body["note"]
    assert "The read finished" not in body["note"]
    for stamp in newest[:5]:
        assert f"reply {stamp}" in body["transcript"]
        assert stamp not in json.dumps(body["unread_replies"])


def test_threads_inside_the_cap_finish():
    top = []
    threads = {}
    stamps = [f"17100000{index:02d}.000100" for index in range(1, 6)]
    for stamp in stamps:
        parent = _post(stamp, f"parent {stamp}", reply_count=1, thread_ts=stamp)
        reply = _post(stamp[:-6] + "200", f"reply {stamp}", thread_ts=stamp)
        top.append(parent)
        threads[stamp] = [parent, reply]
    fake, body = _channel_read(top, threads)
    assert len(_reply_calls(fake)) == 5
    assert body["incomplete"] is False
    assert body["note"] == (
        "The read finished. It includes 5 replies from 5 threads, so no reply is left unread."
    )
    assert "unread_replies" not in body
    assert "not read" not in body["note"]


def test_a_thread_read_does_not_fetch_other_parents():
    child = "1710000005.000200"
    rows = [
        _post(PARENT, "Which day do we ship?", reply_count=2),
        _reply(1, "Monday"),
        _post(child, "nested parent", reply_count=3, thread_ts=child),
    ]
    fake = _slack(thread=rows)
    body = _read(fake, thread=PARENT)
    assert _apis(fake) == ["conversations.replies"]
    assert [call["query"]["ts"][0] for call in _reply_calls(fake)] == [PARENT]
    assert body["thread"] == PARENT
    assert body["incomplete"] is False
    assert body["note"] == "The read finished."
    assert "unread_replies" not in body
    assert "Monday" in body["transcript"]
    assert "\n  reply [" not in body["transcript"]


def test_a_reply_cut_at_the_character_cap_names_the_remainder():
    parent_ts = "1710000010.000100"
    parent = _post(parent_ts, "P", reply_count=2, thread_ts=parent_ts)
    huge = _post("1710000020.000100", "R" * 20000, thread_ts=parent_ts)
    tail = _post("1710000021.000100", "SECOND", thread_ts=parent_ts)
    _fake, body = _channel_read([parent], {parent_ts: [parent, huge, tail]})
    assert body["incomplete"] is True
    assert "The end of a reply was cut at the character cap." in body["note"]
    assert "Reason: character_cap." in body["note"]
    assert "1 parent message has 1 reply that was not read." in body["note"]
    assert body["unread_replies"] == [{"ts": parent_ts, "reply_count": 1}]
    assert f"{parent_ts}: 1 reply." in body["note"]
    assert "2 replies" not in body["note"]
    assert "The read finished" not in body["note"]
    assert "SECOND" not in body["transcript"]
    assert "R" * (history.MAX_CHARS - 1) in body["transcript"]
    assert "R" * history.MAX_CHARS not in body["transcript"]


def test_a_reply_thread_stops_after_one_page():
    parent_ts = "1710000010.000100"
    parent = _post(parent_ts, "parent", reply_count=4, thread_ts=parent_ts)
    replies = [
        _post(f"17100000{20 + index}.000100", f"kept {index}", thread_ts=parent_ts)
        for index in range(2)
    ]
    extra = [
        _post(f"17100000{30 + index}.000100", f"later {index}", thread_ts=parent_ts)
        for index in range(2)
    ]
    fake, body = _channel_read([parent], {parent_ts: [parent, *replies, *extra]}, reply_page_size=3)
    calls = _reply_calls(fake)
    assert len(calls) == 1
    assert "cursor" not in calls[0]["query"]
    assert calls[0]["query"]["limit"] == ["200"]
    assert body["incomplete"] is True
    assert body["unread_replies"] == [{"ts": parent_ts, "reply_count": 2}]
    assert "kept 0" in body["transcript"]
    assert "kept 1" in body["transcript"]
    assert "later 0" not in body["transcript"]
    assert "The read finished" not in body["note"]
    assert "no reply is left unread" not in body["note"]


def test_a_channel_with_no_replies_keeps_the_finished_note():
    plain = _post("1710000010.000100", "just a channel message")
    _fake, body = _channel_read([plain], {})
    assert body["incomplete"] is False
    assert body["note"] == "The read finished."
    assert "unread_replies" not in body


def test_a_finished_channel_read_names_one_reply_from_one_thread():
    parent_ts = "1710000010.000100"
    parent = _post(parent_ts, "parent", reply_count=1, thread_ts=parent_ts)
    reply = _post("1710000020.000100", "only reply", thread_ts=parent_ts)
    _fake, body = _channel_read([parent], {parent_ts: [parent, reply]})
    assert body["incomplete"] is False
    assert "unread_replies" not in body
    assert body["note"] == (
        "The read finished. It includes 1 reply from 1 thread, so no reply is left unread."
    )


def test_a_finished_channel_read_names_the_replies_it_included():
    first_ts = "1710000010.000100"
    second_ts = "1710000030.000100"
    first = _post(first_ts, "parent one", reply_count=1, thread_ts=first_ts)
    second = _post(second_ts, "parent two", reply_count=2, thread_ts=second_ts)
    threads = {
        first_ts: [first, _post("1710000020.000100", "only reply", thread_ts=first_ts)],
        second_ts: [
            second,
            _post("1710000040.000100", "reply a", thread_ts=second_ts),
            _post("1710000041.000100", "reply b", thread_ts=second_ts),
        ],
    }
    _fake, body = _channel_read([first, second], threads)
    assert body["incomplete"] is False
    assert "unread_replies" not in body
    assert body["count"] == 5
    assert body["note"] == (
        "The read finished. It includes 3 replies from 2 threads, so no reply is left unread."
    )


def test_older_messages_left_unread_do_not_claim_every_reply_was_read():
    """A page cap still has older channel messages, so the finished-reply sentence stays off."""
    parent_ts = "1710000060.000100"
    stamps = [f"17100000{index}0.000100" for index in range(1, 7)]
    top = []
    for stamp in stamps:
        if stamp == parent_ts:
            top.append(_post(stamp, "parent", reply_count=1, thread_ts=stamp))
        else:
            top.append(_post(stamp, f"channel {stamp}"))
    reply = _post("1710000061.000100", "reply text", thread_ts=parent_ts)

    def respond(api, query, headers):
        if api == "auth.test":
            return 200, {}, {
                "ok": True,
                "url": "https://workspace.slack.com/",
                "user_id": "UBOT00001",
                "bot_id": "BBOT00001",
            }
        if api == "users.info":
            user = query.get("user", ["U01234567"])[0]
            return 200, {}, {"ok": True, "user": {"id": user, "name": "ada", "profile": {"display_name": "Ada"}}}
        if api == "conversations.history":
            rows = sorted(top, key=lambda row: row["ts"], reverse=True)
            start = int(query.get("cursor", ["0"])[0] or "0")
            page = rows[start:start + 1]
            more = start + 1 < len(rows)
            body = {"ok": True, "messages": page, "has_more": more}
            if more:
                body["response_metadata"] = {"next_cursor": str(start + 1)}
            return 200, {}, body
        if api == "conversations.replies":
            return 200, {}, {"ok": True, "messages": [top[-1], reply], "has_more": False}
        return 200, {}, {"ok": False, "error": "unknown_method"}

    env = {
        "HERMES_SESSION_PLATFORM": "slack",
        "HERMES_SESSION_CHAT_ID": CURRENT,
        "HERMES_SESSION_THREAD_ID": "",
        "HERMES_CRON_SESSION": "",
    }
    body = json.loads(
        history.run(
            {},
            config=lambda: [],
            opener=opener_with(FakeSlack(respond)),
            sleeper=lambda _seconds: None,
            secret=lambda: TOKEN,
            session=lambda name: env.get(name, ""),
        )
    )
    assert body["incomplete"] is True
    assert "reply text" in body["transcript"]
    assert "Older messages were not read." in body["note"]
    assert "The read finished" not in body["note"]
    assert "no reply is left unread" not in body["note"]
