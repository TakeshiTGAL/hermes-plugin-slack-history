"""Top-level questions, long threads, and what the note says was dropped."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import history
from tests.fake_slack import FakeSlack, opener_with

TOKEN = "xoxb-unit-not-real"
CURRENT = "CPUB00001"
PRIVATE = "CPRIV0001"
ASKED = "1710009999.000100"
PARENT = "1710000000.000100"
BOT_USER = "UBOT00001"
BOT_ID = "BBOT00001"


def _post(ts, text, user="U01234567", **extra):
    return {"type": "message", "user": user, "text": text, "ts": ts, **extra}


def _reply(index, text=None, **extra):
    return _post(f"{1710000000 + index}.000200", text or f"reply {index}", **extra)


def _slack(top=(), thread=(), *, page_size=None, last_page=None):
    """Page like Slack: replies run oldest first and history runs newest first."""

    def respond(api, query, headers):
        if api == "auth.test":
            return 200, {}, {
                "ok": True,
                "url": "https://workspace.slack.com/",
                "user_id": BOT_USER,
                "bot_id": BOT_ID,
            }
        if api == "users.info":
            return 200, {}, {"ok": True, "user": {"name": "ada"}}
        if api == "conversations.history":
            rows = sorted(top, key=lambda row: row["ts"], reverse=True)
        else:
            rows = list(thread)
        size = page_size or int(query["limit"][0])
        start = int(query.get("cursor", ["0"])[0])
        stop = start + size
        more = stop < len(rows)
        body = {"ok": True, "messages": rows[start:stop], "has_more": more}
        body["response_metadata"] = {"next_cursor": str(stop) if more else ""}
        if last_page is not None:
            body.update(last_page)
        return 200, {}, body

    return FakeSlack(respond)


def _read(fake, args=None, *, thread=ASKED, extra=()):
    env = {
        "HERMES_SESSION_PLATFORM": "slack",
        "HERMES_SESSION_CHAT_ID": CURRENT,
        "HERMES_SESSION_THREAD_ID": thread,
    }
    return json.loads(
        history.run(
            args or {},
            config=lambda: list(extra),
            opener=opener_with(fake),
            sleeper=lambda _seconds: None,
            secret=lambda: TOKEN,
            session=lambda name: env.get(name, ""),
        )
    )


def _apis(fake):
    return [call["api"] for call in fake.calls if call["api"].startswith("conversations.")]


def _channel():
    return [_post(f"17100000{i:02d}.000100", f"channel message {i}") for i in range(10, 40)]


def _long_thread(replies=120):
    rows = [_post(PARENT, "Which day do we ship?")]
    rows += [_reply(i) for i in range(1, replies)]
    rows.append(_reply(replies, "FINAL-CONCLUSION: Friday"))
    return rows


def test_top_level_question_reads_the_channel():
    asked = _post(ASKED, "What happened in this channel last week?")
    fake = _slack(top=[*_channel(), asked], thread=[asked])
    body = _read(fake)
    assert _apis(fake) == ["conversations.replies", "conversations.history"]
    assert "channel message 39" in body["transcript"]
    assert body["thread"] == ""
    assert body["channel"] == CURRENT
    assert body["incomplete"] is False
    assert "thread_ts=" not in body["transcript"]


@pytest.mark.parametrize("own", [{"user": BOT_USER}, {"user": "", "bot_id": BOT_ID}])
def test_the_bots_own_interim_reply_is_not_a_thread(own):
    asked = _post(ASKED, "What happened in this channel last week?", reply_count=1)
    interim = _post("1710010000.000100", "Let me read the channel.", **own)
    fake = _slack(top=[*_channel(), asked], thread=[asked, interim])
    body = _read(fake)
    assert _apis(fake) == ["conversations.replies", "conversations.history", "conversations.replies"]
    assert [call["query"]["ts"][0] for call in fake.calls if call["api"] == "conversations.replies"] == [ASKED, ASKED]
    assert "channel message 39" in body["transcript"]


@pytest.mark.parametrize(
    "reply",
    [
        _post("1710010000.000100", "a person answered"),
        _post("1710010000.000100", "another bot answered", user="", bot_id="BOTHER001"),
    ],
)
def test_a_real_reply_keeps_the_read_in_the_thread(reply):
    asked = _post(ASKED, "Which day do we ship?")
    fake = _slack(top=_channel(), thread=[asked, reply])
    body = _read(fake)
    assert _apis(fake) == ["conversations.replies"]
    assert body["thread"] == ASKED
    assert body["count"] == 2
    assert body["note"] == "The read finished."


def test_limit_one_does_not_mistake_a_real_thread_for_a_lone_message():
    asked = _post(ASKED, "Which day do we ship?")
    fake = _slack(top=_channel(), thread=[asked, _post("1710010000.000100", "Friday")])
    body = _read(fake, {"limit": 1})
    assert _apis(fake) == ["conversations.replies"]
    assert body["count"] == 1
    assert "Friday" in body["transcript"]
    assert "parent message was omitted" in body["note"]


def test_parent_alone_with_a_reply_count_is_not_called_finished():
    asked = _post(ASKED, "Which day do we ship?", reply_count=3)
    fake = _slack(top=_channel(), thread=[asked])
    body = _read(fake)
    assert _apis(fake) == ["conversations.replies"]
    assert body["incomplete"] is True
    assert "Slack reported 3 replies and returned 0." in body["note"]
    assert "newest replies were not read" not in body["note"]


def test_lone_message_with_more_pages_stays_in_the_thread():
    asked = _post(ASKED, "Which day do we ship?")
    fake = _slack(top=_channel(), thread=[asked], last_page={"has_more": True})
    body = _read(fake)
    assert _apis(fake) == ["conversations.replies"]
    assert body["incomplete"] is True
    assert "missing_cursor" in body["note"]


def test_channel_fallback_never_leaves_the_current_channel():
    asked = _post(ASKED, "Read everything you can.")
    fake = _slack(top=[*_channel(), asked], thread=[asked])
    body = _read(fake, {"ts": "1999999999.000001", "oldest": "0"}, extra=[PRIVATE])
    assert body["channel"] == CURRENT
    reads = [call for call in fake.calls if call["api"].startswith("conversations.")]
    assert {call["query"]["channel"][0] for call in reads} == {CURRENT}


def test_channel_argument_outside_scope_is_still_refused():
    asked = _post(ASKED, "Read the private channel.")
    fake = _slack(top=[asked], thread=[asked])
    body = _read(fake, {"channel": PRIVATE})
    assert body["error_type"] == "channel_out_of_scope"
    assert fake.calls == []


def test_channel_fallback_shares_the_page_cap():
    asked = _post(ASKED, "What happened?")
    top = [_post(f"17100000{i:02d}.000100", f"channel message {i}") for i in range(10, 90)]
    fake = _slack(top=[*top, asked], thread=[asked], page_size=1)
    body = _read(fake)
    assert len(_apis(fake)) == history.MAX_PAGES
    assert body["incomplete"] is True
    assert "page_cap" in body["note"]


def test_long_thread_keeps_the_parent_and_the_conclusion():
    fake = _slack(thread=_long_thread())
    body = _read(fake, thread=PARENT)
    assert body["count"] == history.MAX_MESSAGES
    assert "Which day do we ship?" in body["transcript"]
    assert "FINAL-CONCLUSION: Friday" in body["transcript"]
    assert "reply 1\n" not in body["transcript"]
    assert body["transcript"].index("Which day") < body["transcript"].index("FINAL-CONCLUSION")
    assert body["incomplete"] is True
    assert "newest reply is included" in body["note"]
    assert "71 replies between the parent message and the replies shown were omitted" in body["note"]
    assert "message_cap" in body["note"]
    assert "Older messages" not in body["note"]


def test_parent_repeated_on_every_page_is_counted_once():
    parent = _post(PARENT, "Which day do we ship?")
    pages = {"0": [parent, _reply(1)], "2": [parent, _reply(2, "FINAL-CONCLUSION: Friday")]}

    def respond(api, query, headers):
        if api == "auth.test":
            return 200, {}, {"ok": True, "url": "https://workspace.slack.com/"}
        if api == "users.info":
            return 200, {}, {"ok": True, "user": {"name": "ada"}}
        cursor = query.get("cursor", ["0"])[0]
        more = cursor == "0"
        meta = {"next_cursor": "2" if more else ""}
        return 200, {}, {"ok": True, "messages": pages[cursor], "has_more": more, "response_metadata": meta}

    body = _read(FakeSlack(respond), thread=PARENT)
    assert body["count"] == 3
    assert body["transcript"].count("Which day do we ship?") == 1
    assert body["note"] == "The read finished."


def test_limit_shapes_the_kept_replies():
    fake = _slack(thread=_long_thread(10))
    body = _read(fake, {"limit": 3}, thread=PARENT)
    assert body["count"] == 3
    assert "reply 9" in body["transcript"]
    assert "FINAL-CONCLUSION" in body["transcript"]
    assert "reply 8" not in body["transcript"]


def test_thread_past_the_page_cap_says_the_latest_was_not_read():
    fake = _slack(thread=_long_thread(), page_size=10)
    body = _read(fake, thread=PARENT)
    assert len(_apis(fake)) == history.MAX_PAGES
    assert "FINAL-CONCLUSION" not in body["transcript"]
    assert body["incomplete"] is True
    assert "newest replies were not read" in body["note"]
    assert "does not reach the latest message" in body["note"]
    assert "page_cap" in body["note"]
    assert "newest reply is included" not in body["note"]


@pytest.mark.parametrize("cursor", ["", "bad cursor!"])
def test_thread_with_an_unusable_cursor_is_not_finished(cursor):
    rows = [_post(PARENT, "Which day do we ship?"), _reply(1)]
    last = {"has_more": True, "response_metadata": {"next_cursor": cursor}}
    body = _read(_slack(thread=rows, last_page=last), thread=PARENT)
    assert body["incomplete"] is True
    assert "The read finished." not in body["note"]
    assert "newest replies were not read" in body["note"]


def test_channel_with_has_more_and_no_cursor_is_not_finished():
    last = {"has_more": True, "response_metadata": {"next_cursor": ""}}
    body = _read(_slack(top=_channel(), last_page=last), {"limit": 5}, thread="")
    assert body["incomplete"] is True
    assert "missing_cursor" in body["note"]
    assert "The read finished." not in body["note"]


def test_huge_parent_cannot_push_out_the_conclusion():
    rows = [_post(PARENT, "p" * 30000), _reply(1, "FINAL-CONCLUSION: Friday")]
    body = _read(_slack(thread=rows), thread=PARENT)
    assert "FINAL-CONCLUSION: Friday" in body["transcript"]
    assert "p" * (history.MAX_CHARS + 1) not in body["transcript"]
    assert body["incomplete"] is True
    assert "character_cap" in body["note"]
    assert "cut at the character cap" in body["note"]


def test_character_cap_drops_older_replies_first():
    rows = [_post(PARENT, "Which day do we ship?")]
    rows += [_reply(i, f"{i:02d}" + "x" * 2998) for i in range(1, 9)]
    rows.append(_reply(9, "FINAL-CONCLUSION: Friday"))
    body = _read(_slack(thread=rows), thread=PARENT)
    assert "FINAL-CONCLUSION: Friday" in body["transcript"]
    assert "Which day do we ship?" in body["transcript"]
    assert "\n01xxx" not in body["transcript"]
    assert "omitted" in body["note"]
    assert "character_cap" in body["note"]


def test_short_thread_is_complete_and_in_order():
    rows = [_post(PARENT, "Which day do we ship?"), _reply(1, "Monday"), _reply(2, "Friday")]
    body = _read(_slack(thread=rows), thread=PARENT)
    assert body["count"] == 3
    assert body["incomplete"] is False
    assert body["note"] == "The read finished."
    assert body["transcript"].index("Monday") < body["transcript"].index("Friday")


def _failing_auth(answer, top, thread):
    """Slack whose auth.test fails in the given way while the read methods keep working."""
    healthy = _slack(top=top, thread=thread)

    def respond(api, query, headers):
        if api == "auth.test":
            return answer
        return healthy.responder(api, query, headers)

    return FakeSlack(respond)


def test_character_cap_with_nothing_older_names_the_cut_text():
    rows = [_post(f"17100000{i}.000100", f"M{i}-" + "x" * 2490 + f"-END{i}") for i in range(10, 15)]
    body = _read(_slack(top=rows), thread="")
    assert body["count"] == 5
    assert body["incomplete"] is True
    assert "-END10" not in body["transcript"]
    assert "The end of the oldest message shown was cut at the character cap." in body["note"]
    assert "Older messages" not in body["note"]
    assert "character_cap" in body["note"]


def test_newest_message_over_the_cap_says_its_end_was_cut():
    body = _read(_slack(top=[_post("1710000010.000100", "n" * 13000)]), thread="")
    assert body["count"] == 1
    assert "The end of the newest message was cut at the character cap." in body["note"]
    assert "Older messages" not in body["note"]


def test_cut_message_with_older_messages_left_says_both():
    rows = [_post("1710000010.000100", "older"), _post("1710000020.000100", "n" * 13000)]
    body = _read(_slack(top=rows), thread="")
    assert "The end of the newest message was cut at the character cap." in body["note"]
    assert "Older messages were not read." in body["note"]


def test_cut_on_the_last_message_of_a_page_with_more_pages_says_older_left():
    rows = [_post("1710000010.000100", "older"), _post("1710000020.000100", "n" * 13000)]
    body = _read(_slack(top=rows, page_size=1), thread="")
    assert body["count"] == 1
    assert "Older messages were not read." in body["note"]


def test_exact_fit_is_a_finished_read():
    rows = [_post("1710000010.000100", "a" * 6000), _post("1710000020.000100", "b" * 6000)]
    body = _read(_slack(top=rows), thread="")
    assert body["incomplete"] is False
    assert body["note"] == "The read finished."


def test_message_cap_still_says_older_messages_were_not_read():
    body = _read(_slack(top=_channel()), {"limit": 5}, thread="")
    assert "Older messages were not read." in body["note"]
    assert "cut at the character cap" not in body["note"]


def test_thread_note_names_the_newest_reply_when_it_was_cut():
    rows = [_post(PARENT, "Which day do we ship?"), _reply(1, "r" * 13000)]
    body = _read(_slack(thread=rows), thread=PARENT)
    assert "The end of the newest reply was cut at the character cap." in body["note"]


def test_thread_note_names_the_oldest_reply_shown_when_it_was_cut():
    rows = [_post(PARENT, "Which day do we ship?"), _reply(1, "o" * 7000), _reply(2, "n" * 7000)]
    body = _read(_slack(thread=rows), thread=PARENT)
    assert "The end of the oldest reply shown was cut at the character cap." in body["note"]
    assert "newest reply was cut" not in body["note"]


def test_thread_note_names_the_parent_when_it_was_cut():
    rows = [_post(PARENT, "p" * 30000), _reply(1, "r" * 11000)]
    body = _read(_slack(thread=rows), thread=PARENT)
    assert "The end of the parent message was cut at the character cap." in body["note"]


@pytest.mark.parametrize(
    "answer",
    [
        (200, {}, {"ok": False, "error": "internal_error"}),
        (500, {}, b"<html>oops</html>"),
        (503, {}, {"ok": False, "error": "service_unavailable"}),
        (200, {}, {"ok": True, "url": "https://workspace.slack.com/"}),
    ],
    ids=["internal_error", "http500_html", "service_unavailable", "missing_ids"],
)
def test_failed_auth_with_only_a_bot_reply_reads_the_channel(answer):
    asked = _post(ASKED, "What happened in this channel last week?", reply_count=1)
    interim = _post("1710010000.000100", "Let me read the channel.", user=BOT_USER, bot_id=BOT_ID)
    fake = _failing_auth(answer, [*_channel(), asked], [asked, interim])
    body = _read(fake)
    assert body["ok"] is True
    assert _apis(fake) == ["conversations.replies", "conversations.history", "conversations.replies"]
    assert [call["query"]["ts"][0] for call in fake.calls if call["api"] == "conversations.replies"] == [ASKED, ASKED]
    assert body["thread"] == ""
    assert body["count"] == 31
    assert body["incomplete"] is True
    assert "did not confirm this bot's identity" in body["note"]
    assert "The read finished." not in body["note"]


def test_failed_auth_keeps_a_human_reply_in_the_thread():
    asked = _post(ASKED, "Which day do we ship?")
    reply = _post("1710010000.000100", "Friday")
    fake = _failing_auth((200, {}, {"ok": False, "error": "internal_error"}), _channel(), [asked, reply])
    body = _read(fake)
    assert _apis(fake) == ["conversations.replies"]
    assert body["thread"] == ASKED
    assert body["count"] == 2


def test_failed_auth_fallback_stays_in_the_current_channel():
    asked = _post(ASKED, "Read everything you can.")
    interim = _post("1710010000.000100", "Reading.", user=BOT_USER, bot_id=BOT_ID)
    fake = _failing_auth((200, {}, {"ok": False, "error": "internal_error"}), [asked], [asked, interim])
    _read(fake, extra=[PRIVATE])
    reads = [call for call in fake.calls if call["api"].startswith("conversations.")]
    assert {call["query"]["channel"][0] for call in reads} == {CURRENT}


def test_a_person_sharing_the_bot_id_is_still_a_real_reply():
    asked = _post(ASKED, "Which day do we ship?")
    reply = _post("1710010000.000100", "Friday", bot_id=BOT_ID)
    fake = _slack(top=_channel(), thread=[asked, reply])
    body = _read(fake)
    assert _apis(fake) == ["conversations.replies"]
    assert body["thread"] == ASKED


def test_reply_count_above_the_returned_replies_does_not_claim_the_newest_is_missing():
    rows = [_post(PARENT, "Which day do we ship?", reply_count=5), _reply(1, "Monday"), _reply(2, "Friday")]
    body = _read(_slack(thread=rows), thread=PARENT)
    assert body["incomplete"] is True
    assert "Slack reported 5 replies and returned 2." in body["note"]
    assert "position of the missing replies is unknown" in body["note"]
    assert "newest replies were not read" not in body["note"]
    assert "newest reply is included" not in body["note"]
    assert "replies_missing" in body["note"]


def test_oversized_page_does_not_tell_the_user_to_wait():
    def respond(api, query, headers):
        if api == "auth.test":
            return 200, {}, {"ok": True, "url": "https://workspace.slack.com/"}
        return 200, {}, b"x" * (1_000_000 + 70_000)

    body = _read(FakeSlack(respond), thread="")
    assert body["error_type"] == "response_too_large"
    assert "later" not in body["next_step"].lower()
    assert "lower limit" in body["next_step"]


def test_channel_read_names_each_unread_thread_when_the_reply_page_is_empty():
    """An empty replies page leaves the history count unread and names that parent."""
    asked = _post(ASKED, "What happened in this channel last week?")
    parent = _post("1710000010.000100", "Should we ship on Friday?", reply_count=3)
    fake = _slack(top=[_post("1710000001.000100", "good morning"), parent, asked], thread=[asked])
    body = _read(fake)
    assert _apis(fake) == ["conversations.replies", "conversations.history", "conversations.replies"]
    replies = [call for call in fake.calls if call["api"] == "conversations.replies"]
    assert [call["query"]["ts"] for call in replies] == [[ASKED], ["1710000010.000100"]]
    assert body["thread"] == ""
    assert body["incomplete"] is True
    assert body["note"] == (
        "1 parent message has 3 replies that were not read. "
        "Ask again from that thread in Slack. This tool does not take a thread id. "
        "1710000010.000100: 3 replies."
    )
    assert body["unread_replies"] == [{"ts": "1710000010.000100", "reply_count": 3}]
    assert "Should we ship on Friday?" in body["transcript"]
    assert "The read finished." not in body["note"]


def test_two_parent_messages_sum_their_unread_replies():
    first = _post("1710000010.000100", "one", reply_count=2)
    second = _post("1710000012.000100", "two", reply_count=4)
    fake = _slack(top=[first, second, _post("1710000020.000100", "plain")])
    body = _read(fake, thread="")
    assert body["incomplete"] is True
    assert body["note"] == (
        "2 parent messages have 6 replies that were not read. "
        "Ask again from each thread in Slack. This tool does not take a thread id. "
        "1710000010.000100: 2 replies. "
        "1710000012.000100: 4 replies."
    )
    assert body["unread_replies"] == [
        {"ts": "1710000010.000100", "reply_count": 2},
        {"ts": "1710000012.000100", "reply_count": 4},
    ]
    assert _apis(fake) == ["conversations.history", "conversations.replies", "conversations.replies"]
    assert [call["query"]["ts"][0] for call in fake.calls if call["api"] == "conversations.replies"] == [
        "1710000012.000100",
        "1710000010.000100",
    ]


def test_channel_read_keeps_the_count_when_the_reply_page_is_empty():
    first = _post("1710000010.000100", "one", reply_count=1)
    fake = _slack(top=[first], thread="")
    body = _read(fake, thread="")
    assert body["note"] == (
        "1 parent message has 1 reply that was not read. "
        "Ask again from that thread in Slack. This tool does not take a thread id. "
        "1710000010.000100: 1 reply."
    )
    assert _apis(fake) == ["conversations.history", "conversations.replies"]
    assert [call["query"]["ts"][0] for call in fake.calls if call["api"] == "conversations.replies"] == [
        "1710000010.000100",
    ]


def test_only_a_read_parent_with_replies_is_named():
    older = _post("1710000010.000100", "older", reply_count=9)
    newer = _post("1710000020.000100", "newer", reply_count=2)
    fake = _slack(top=[older, newer], thread="")
    body = _read(fake, {"limit": 1}, thread="")
    assert body["count"] == 1
    assert body["unread_replies"] == [{"ts": "1710000020.000100", "reply_count": 2}]
    assert "1710000010.000100" not in body["note"]
    assert "Older messages were not read." in body["note"]
    assert "1 parent message has 2 replies that were not read." in body["note"]
    assert "The read finished." not in body["note"]
    assert _apis(fake) == ["conversations.history"]


def test_reply_count_below_one_can_still_finish():
    rows = [
        _post("1710000010.000100", "plain"),
        _post("1710000011.000100", "zero", reply_count=0),
        _post("1710000012.000100", "text", reply_count="3"),
        _post("1710000013.000100", "flag", reply_count=True),
    ]
    body = _read(_slack(top=rows), thread="")
    assert body["incomplete"] is False
    assert body["note"] == "The read finished."
    assert "unread_replies" not in body


def test_a_reply_count_on_a_broadcast_does_not_become_a_second_thread():
    """A reply whose thread_ts differs from ts is that thread, not its own unread item."""
    parent_ts = "1710000010.000100"
    rows = [
        _post(parent_ts, "parent", reply_count=3, thread_ts=parent_ts),
        _post("1710000020.000100", "also posted", reply_count=5, thread_ts=parent_ts),
    ]
    fake = _slack(top=rows)
    body = _read(fake, thread="")
    assert body["incomplete"] is True
    assert body["unread_replies"] == [{"ts": parent_ts, "reply_count": 2}]
    assert body["note"] == (
        "1 parent message has 2 replies that were not read. "
        "Ask again from that thread in Slack. This tool does not take a thread id. "
        f"{parent_ts}: 2 replies."
    )
    assert "1710000020.000100" not in body["note"]
    assert "5 replies" not in body["note"]
    assert "8 replies" not in body["note"]
    assert "3 replies" not in body["note"]
    assert _apis(fake) == ["conversations.history", "conversations.replies"]
    assert [call["query"]["ts"][0] for call in fake.calls if call["api"] == "conversations.replies"] == [parent_ts]


def test_a_visible_reply_does_not_keep_a_zero_parent_count():
    """Integer 0 on the parent is not the count when a reply from that thread is in the history."""
    parent_ts = "1710000010.000100"
    rows = [
        _post(parent_ts, "parent", reply_count=0, thread_ts=parent_ts),
        _post("1710000020.000100", "also posted", reply_count=4, thread_ts=parent_ts),
    ]
    fake = _slack(top=rows)
    body = _read(fake, thread="")
    assert body["incomplete"] is True
    assert body["unread_replies"] == [{"ts": parent_ts, "reply_count": None}]
    assert body["note"] == (
        "1 parent message has replies that were not read. The reply count is unknown. "
        "Ask again from that thread in Slack. This tool does not take a thread id. "
        f"{parent_ts}: reply count unknown."
    )
    assert "may have replies" not in body["note"]
    assert "4 replies" not in body["note"]
    assert "1710000020.000100" not in json.dumps(body["unread_replies"])
    assert "The read finished." not in body["note"]
    assert _apis(fake) == ["conversations.history"]


def test_each_missing_parent_is_one_unknown_item_when_replies_carry_counts():
    """Without the parent, each thread_ts is one null item. A reply's own count is not used."""
    first = "1710000010.000100"
    second = "1710000040.000100"
    rows = [
        _post("1710000020.000100", "also posted", reply_count=4, thread_ts=first),
        _post("1710000021.000100", "again", thread_ts=first),
        _post("1710000030.000100", "other", reply_count=2, thread_ts=second),
    ]
    fake = _slack(top=rows)
    body = _read(fake, thread="")
    assert body["incomplete"] is True
    assert body["unread_replies"] == [
        {"ts": first, "reply_count": None},
        {"ts": second, "reply_count": None},
    ]
    assert body["note"] == (
        "2 threads may have replies that were not read. The reply count is unknown. "
        "Ask again from each thread in Slack. This tool does not take a thread id. "
        f"{first}: reply count unknown. "
        f"{second}: reply count unknown."
    )
    dumped = json.dumps(body["unread_replies"])
    assert "1710000020.000100" not in dumped
    assert "1710000021.000100" not in dumped
    assert "1710000030.000100" not in dumped
    assert "4 replies" not in body["note"]
    assert "2 replies" not in body["note"]
    assert "The read finished." not in body["note"]
    assert _apis(fake) == ["conversations.history"]


def test_reply_count_zero_without_a_visible_reply_can_still_finish():
    """Integer 0 on a parent, with no reply message in the history, is not unread."""
    rows = [
        _post(
            "1710000010.000100",
            "parent",
            reply_count=0,
            thread_ts="1710000010.000100",
            latest_reply="1710000019.000100",
            reply_users_count=4,
        ),
    ]
    body = _read(_slack(top=rows), thread="")
    assert body["incomplete"] is False
    assert body["note"] == "The read finished."
    assert "unread_replies" not in body


def test_reply_count_zero_on_a_visible_reply_names_that_thread_once():
    """A reply with integer 0 is still a reply. It does not finish the thread or name its own ts."""
    parent_ts = "1710000010.000100"
    rows = [
        _post(
            parent_ts,
            "parent",
            reply_count=0,
            thread_ts=parent_ts,
            latest_reply="1710000019.000100",
            reply_users_count=4,
        ),
        _post("1710000020.000100", "also posted", reply_count=0, thread_ts=parent_ts),
    ]
    body = _read(_slack(top=rows), thread="")
    assert body["incomplete"] is True
    assert body["unread_replies"] == [{"ts": parent_ts, "reply_count": None}]
    assert "1710000019.000100" not in body["note"]
    assert "1710000020.000100" not in json.dumps(body["unread_replies"])
    assert "The read finished." not in body["note"]
    assert "may have replies" not in body["note"]


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"reply_count": None},
        {"reply_count": "3"},
        {"reply_count": True},
        {"reply_count": False},
        {"reply_count": 3.0},
        {"reply_count": -5},
        {"reply_count": ["3"]},
    ],
)
def test_thread_parent_without_a_usable_count_is_unknown(extra):
    ts = "1710000010.000100"
    parent = _post(ts, "Should we ship on Friday?", thread_ts=ts, **extra)
    fake = _slack(top=[parent])
    body = _read(fake, thread="")
    assert body["ok"] is True
    assert body["incomplete"] is True
    assert body["note"] == (
        "1 parent message has replies that were not read. The reply count is unknown. "
        "Ask again from that thread in Slack. This tool does not take a thread id. "
        f"{ts}: reply count unknown."
    )
    assert body["unread_replies"] == [{"ts": ts, "reply_count": None}]
    assert "The read finished." not in body["note"]
    assert "3 replies" not in body["note"]
    assert "-5" not in body["note"]
    assert _apis(fake) == ["conversations.history", "conversations.replies"]
    assert [call["query"]["ts"][0] for call in fake.calls if call["api"] == "conversations.replies"] == [ts]


def test_latest_reply_without_thread_ts_is_an_unknown_count():
    ts = "1710000010.000100"
    parent = _post(ts, "Should we ship on Friday?", latest_reply="1710000013.000100")
    fake = _slack(top=[parent])
    body = _read(fake, thread="")
    assert body["unread_replies"] == [{"ts": ts, "reply_count": None}]
    assert "1710000013.000100" not in json.dumps(body["unread_replies"])
    assert "The read finished." not in body["note"]
    assert body["incomplete"] is True
    assert _apis(fake) == ["conversations.history", "conversations.replies"]
    assert [call["query"]["ts"][0] for call in fake.calls if call["api"] == "conversations.replies"] == [ts]


def test_reply_users_count_without_thread_ts_is_an_unknown_count():
    ts = "1710000010.000100"
    parent = _post(ts, "Should we ship on Friday?", reply_users_count=2, reply_count="3")
    fake = _slack(top=[parent])
    body = _read(fake, thread="")
    assert body["unread_replies"] == [{"ts": ts, "reply_count": None}]
    assert "3 replies" not in body["note"]
    assert "The read finished." not in body["note"]
    assert _apis(fake) == ["conversations.history", "conversations.replies"]
    assert [call["query"]["ts"][0] for call in fake.calls if call["api"] == "conversations.replies"] == [ts]


def test_weak_side_fields_without_thread_ts_can_still_finish():
    rows = [
        _post("1710000010.000100", "zero users", reply_users_count=0),
        _post("1710000011.000100", "bool users", reply_users_count=True),
        _post("1710000012.000100", "text users", reply_users_count="2"),
        _post("1710000013.000100", "float users", reply_users_count=2.0),
        _post("1710000014.000100", "empty latest", latest_reply=""),
        _post("1710000015.000100", "plain"),
    ]
    body = _read(_slack(top=rows), thread="")
    assert body["incomplete"] is False
    assert body["note"] == "The read finished."
    assert "unread_replies" not in body


def test_channel_posted_reply_names_that_thread_once():
    parent_ts = "1710000010.000100"
    rows = [
        _post("1710000020.000100", "also posted", thread_ts=parent_ts, subtype="thread_broadcast"),
        _post("1710000021.000100", "again", thread_ts=parent_ts),
    ]
    fake = _slack(top=rows)
    body = _read(fake, thread="")
    assert body["incomplete"] is True
    assert body["unread_replies"] == [{"ts": parent_ts, "reply_count": None}]
    assert body["note"] == (
        "1 thread may have replies that were not read. The reply count is unknown. "
        "Ask again from that thread in Slack. This tool does not take a thread id. "
        f"{parent_ts}: reply count unknown."
    )
    assert "The read finished." not in body["note"]
    assert _apis(fake) == ["conversations.history"]


def test_channel_posted_reply_collapses_into_the_counted_parent():
    parent_ts = "1710000010.000100"
    rows = [
        _post(parent_ts, "parent", reply_count=3, thread_ts=parent_ts),
        _post("1710000020.000100", "also posted", thread_ts=parent_ts),
    ]
    fake = _slack(top=rows)
    body = _read(fake, thread="")
    assert body["note"] == (
        "1 parent message has 2 replies that were not read. "
        "Ask again from that thread in Slack. This tool does not take a thread id. "
        f"{parent_ts}: 2 replies."
    )
    assert body["unread_replies"] == [{"ts": parent_ts, "reply_count": 2}]
    assert _apis(fake) == ["conversations.history", "conversations.replies"]
    assert [call["query"]["ts"][0] for call in fake.calls if call["api"] == "conversations.replies"] == [parent_ts]


def test_a_channel_broadcast_leaves_one_reply_unread():
    """Real Slack shape: reply_count 2, one thread_broadcast in the channel, the other reply absent."""
    parent_ts = "1710000010.000100"
    rows = [
        _post("1710000001.000100", "plain"),
        _post(
            parent_ts,
            "parent",
            reply_count=2,
            thread_ts=parent_ts,
            latest_reply="1710000020.000100",
            reply_users_count=1,
        ),
        _post(
            "1710000020.000100",
            "also in the channel",
            thread_ts=parent_ts,
            subtype="thread_broadcast",
        ),
    ]
    fake = _slack(top=rows)
    body = _read(fake, thread="")
    assert body["incomplete"] is True
    assert body["unread_replies"] == [{"ts": parent_ts, "reply_count": 1}]
    assert body["note"] == (
        "1 parent message has 1 reply that was not read. "
        "Ask again from that thread in Slack. This tool does not take a thread id. "
        f"{parent_ts}: 1 reply."
    )
    assert "2 replies" not in body["note"]
    assert "also in the channel" in body["transcript"]
    assert "The read finished." not in body["note"]
    assert _apis(fake) == ["conversations.history", "conversations.replies"]
    assert [call["query"]["ts"][0] for call in fake.calls if call["api"] == "conversations.replies"] == [parent_ts]


def test_one_visible_reply_that_covers_the_count_finishes_the_channel_read():
    """A remainder of 0 is not an unread item, so a read with nothing else missing can finish."""
    parent_ts = "1710000010.000100"
    rows = [
        _post("1710000001.000100", "plain"),
        _post(parent_ts, "parent", reply_count=1, thread_ts=parent_ts),
        _post("1710000020.000100", "also posted", thread_ts=parent_ts, subtype="thread_broadcast"),
    ]
    fake = _slack(top=rows)
    body = _read(fake, thread="")
    assert body["incomplete"] is False
    assert body["note"] == (
        "The read finished. It includes 1 reply from 1 thread, so no reply is left unread."
    )
    assert "unread_replies" not in body
    assert "also posted" in body["transcript"]
    assert _apis(fake) == ["conversations.history", "conversations.replies"]
    assert [call["query"]["ts"][0] for call in fake.calls if call["api"] == "conversations.replies"] == [parent_ts]


def test_more_visible_replies_than_the_count_leave_no_unread_item():
    parent_ts = "1710000010.000100"
    rows = [
        _post(parent_ts, "parent", reply_count=1, thread_ts=parent_ts),
        _post("1710000020.000100", "first", thread_ts=parent_ts, subtype="thread_broadcast"),
        _post("1710000021.000100", "second", thread_ts=parent_ts, subtype="thread_broadcast"),
    ]
    fake = _slack(top=rows)
    body = _read(fake, thread="")
    assert body["incomplete"] is False
    assert body["note"] == (
        "The read finished. It includes 2 replies from 1 thread, so no reply is left unread."
    )
    assert "unread_replies" not in body
    assert _apis(fake) == ["conversations.history", "conversations.replies"]
    assert [call["query"]["ts"][0] for call in fake.calls if call["api"] == "conversations.replies"] == [parent_ts]


def test_a_covered_thread_drops_out_while_another_count_stays():
    covered = "1710000010.000100"
    open_ts = "1710000040.000100"
    rows = [
        _post(covered, "covered", reply_count=1, thread_ts=covered),
        _post("1710000020.000100", "seen", thread_ts=covered, subtype="thread_broadcast"),
        _post(open_ts, "open", reply_count=2, thread_ts=open_ts),
    ]
    fake = _slack(top=rows)
    body = _read(fake, thread="")
    assert body["incomplete"] is True
    assert body["unread_replies"] == [{"ts": open_ts, "reply_count": 2}]
    assert covered not in body["note"]
    assert body["note"] == (
        "1 parent message has 2 replies that were not read. "
        "Ask again from that thread in Slack. This tool does not take a thread id. "
        f"{open_ts}: 2 replies."
    )
    assert _apis(fake) == ["conversations.history", "conversations.replies", "conversations.replies"]
    assert [call["query"]["ts"][0] for call in fake.calls if call["api"] == "conversations.replies"] == [open_ts, covered]


def test_duplicate_reply_timestamps_are_subtracted_once():
    """Two broadcasts and one repeated timestamp leave one unread reply from a count of 3."""
    parent_ts = "1710000010.000100"
    rows = [
        _post(parent_ts, "parent", reply_count=3, thread_ts=parent_ts),
        _post("1710000020.000100", "also posted", thread_ts=parent_ts, subtype="thread_broadcast"),
        _post("1710000020.000100", "same timestamp again", thread_ts=parent_ts, subtype="thread_broadcast"),
        _post("1710000030.000100", "second", thread_ts=parent_ts, subtype="thread_broadcast"),
    ]
    fake = _slack(top=rows)
    body = _read(fake, thread="")
    assert body["incomplete"] is True
    assert body["unread_replies"] == [{"ts": parent_ts, "reply_count": 1}]
    assert body["note"] == (
        "1 parent message has 1 reply that was not read. "
        "Ask again from that thread in Slack. This tool does not take a thread id. "
        f"{parent_ts}: 1 reply."
    )
    assert "2 replies" not in body["note"]
    assert "3 replies" not in body["note"]
    assert _apis(fake) == ["conversations.history", "conversations.replies"]
    assert [call["query"]["ts"][0] for call in fake.calls if call["api"] == "conversations.replies"] == [parent_ts]


def test_a_visible_reply_is_subtracted_only_from_its_own_thread():
    parent_ts = "1710000010.000100"
    other = "1710000040.000100"
    missing = "1710000070.000100"
    rows = [
        _post(
            parent_ts,
            "parent",
            reply_count=2,
            thread_ts=parent_ts,
            latest_reply="1710000020.000100",
            reply_users_count=1,
        ),
        _post("1710000020.000100", "broadcast parent", thread_ts=parent_ts, subtype="thread_broadcast"),
        _post(other, "other parent", reply_count=4, thread_ts=other),
        _post("1710000080.000100", "broadcast missing", thread_ts=missing, subtype="thread_broadcast"),
    ]
    fake = _slack(top=rows)
    body = _read(fake, thread="")
    assert body["unread_replies"] == [
        {"ts": parent_ts, "reply_count": 1},
        {"ts": other, "reply_count": 4},
        {"ts": missing, "reply_count": None},
    ]
    assert body["note"] == (
        "2 parent messages have 5 replies that were not read. "
        "1 thread may have replies that were not read. The reply count is unknown. "
        "Ask again from each thread in Slack. This tool does not take a thread id. "
        f"{parent_ts}: 1 reply. "
        f"{other}: 4 replies. "
        f"{missing}: reply count unknown."
    )
    assert "1710000020.000100" not in json.dumps(body["unread_replies"])
    assert "1710000080.000100" not in json.dumps(body["unread_replies"])
    assert _apis(fake) == ["conversations.history", "conversations.replies", "conversations.replies"]
    assert [call["query"]["ts"][0] for call in fake.calls if call["api"] == "conversations.replies"] == [other, parent_ts]


def test_a_reply_dropped_by_the_message_cap_is_not_subtracted():
    parent_ts = "1710000030.000100"
    rows = [
        _post("1710000040.000100", "kept broadcast", thread_ts=parent_ts, subtype="thread_broadcast"),
        _post(parent_ts, "parent", reply_count=3, thread_ts=parent_ts),
        _post("1710000020.000100", "dropped broadcast", thread_ts=parent_ts, subtype="thread_broadcast"),
    ]
    fake = _slack(top=rows)
    body = _read(fake, {"limit": 2}, thread="")
    assert body["count"] == 2
    assert "kept broadcast" in body["transcript"]
    assert "dropped broadcast" not in body["transcript"]
    assert body["unread_replies"] == [{"ts": parent_ts, "reply_count": 2}]
    assert "Older messages were not read." in body["note"]
    assert "1 parent message has 2 replies that were not read." in body["note"]
    assert f"{parent_ts}: 2 replies." in body["note"]
    assert "1 reply" not in body["note"]
    assert "3 replies" not in body["note"]
    assert "The read finished." not in body["note"]
    assert _apis(fake) == ["conversations.history"]


def test_a_cut_reply_keeps_the_character_note_and_is_not_still_fully_unread():
    parent_ts = "1710000030.000100"
    broadcast = "B" * 12000
    rows = [
        _post(parent_ts, "parent", reply_count=2, thread_ts=parent_ts),
        _post("1710000020.000100", broadcast, thread_ts=parent_ts, subtype="thread_broadcast"),
    ]
    fake = _slack(top=rows)
    body = _read(fake, thread="")
    assert body["incomplete"] is True
    assert "The end of the oldest message shown was cut at the character cap." in body["note"]
    assert "Reason: character_cap." in body["note"]
    assert body["unread_replies"] == [{"ts": parent_ts, "reply_count": 1}]
    assert "1 parent message has 1 reply that was not read." in body["note"]
    assert f"{parent_ts}: 1 reply." in body["note"]
    assert "2 replies" not in body["note"]
    assert "The read finished." not in body["note"]
    assert "B" * 100 in body["transcript"]
    assert broadcast not in body["transcript"]
    assert _apis(fake) == ["conversations.history"]


def test_channel_posted_reply_collapses_into_a_zero_parent():
    parent_ts = "1710000010.000100"
    rows = [
        _post(parent_ts, "parent", reply_count=0, thread_ts=parent_ts),
        _post("1710000020.000100", "also posted", thread_ts=parent_ts),
    ]
    body = _read(_slack(top=rows), thread="")
    assert body["incomplete"] is True
    assert body["unread_replies"] == [{"ts": parent_ts, "reply_count": None}]
    assert body["note"] == (
        "1 parent message has replies that were not read. The reply count is unknown. "
        "Ask again from that thread in Slack. This tool does not take a thread id. "
        f"{parent_ts}: reply count unknown."
    )
    assert "may have replies" not in body["note"]
    assert "1710000020.000100" not in json.dumps(body["unread_replies"])
    assert "The read finished." not in body["note"]


def test_channel_posted_reply_collapses_into_an_unknown_parent():
    parent_ts = "1710000010.000100"
    rows = [
        _post(parent_ts, "parent", thread_ts=parent_ts),
        _post("1710000020.000100", "also posted", thread_ts=parent_ts),
        _post("1710000021.000100", "again", thread_ts=parent_ts),
    ]
    body = _read(_slack(top=rows), thread="")
    assert body["unread_replies"] == [{"ts": parent_ts, "reply_count": None}]
    assert body["note"].count(parent_ts) == 1
    assert "may have replies" not in body["note"]
    assert "The read finished." not in body["note"]


def test_known_and_unknown_counts_stay_in_separate_sentences():
    rows = [
        _post("1710000010.000100", "known", reply_count=3, thread_ts="1710000010.000100"),
        _post("1710000012.000100", "unknown", thread_ts="1710000012.000100"),
        _post("1710000030.000100", "broadcast", thread_ts="1710000040.000100"),
    ]
    body = _read(_slack(top=rows), thread="")
    assert body["note"] == (
        "1 parent message has 3 replies that were not read. "
        "1 parent message has replies that were not read. The reply count is unknown. "
        "1 thread may have replies that were not read. The reply count is unknown. "
        "Ask again from each thread in Slack. This tool does not take a thread id. "
        "1710000010.000100: 3 replies. "
        "1710000012.000100: reply count unknown. "
        "1710000040.000100: reply count unknown."
    )
    assert body["unread_replies"] == [
        {"ts": "1710000010.000100", "reply_count": 3},
        {"ts": "1710000012.000100", "reply_count": None},
        {"ts": "1710000040.000100", "reply_count": None},
    ]
    for number in ("4", "5", "6", "7"):
        assert f"{number} replies" not in body["note"]


def test_a_broken_timestamp_is_not_copied_into_the_note():
    parent = _post("not-a-ts", "parent", reply_count=4, thread_ts="also not a ts")
    body = _read(_slack(top=[parent]), thread="")
    dumped = json.dumps(body)
    assert "not-a-ts" not in dumped
    assert "also not a ts" not in dumped
    assert body["ok"] is True
    assert body["incomplete"] is True
    assert "unread_replies" not in body
    assert body["note"] == (
        "1 parent message has 4 replies that were not read. "
        "Ask again from that thread in Slack. This tool does not take a thread id."
    )
    assert "The read finished." not in body["note"]


def test_a_broken_thread_ts_is_not_copied_and_does_not_raise():
    rows = [
        _post("1710000010.000100", "parent", thread_ts="not a thread", reply_count="3"),
        _post("1710000011.000100", "object", thread_ts={"ts": "1710000011.000100"}, reply_count=["3"], latest_reply={"ts": "nope"}, reply_users_count={"n": 2}),
        _post("1710000012.000100", "nested", thread_ts=["1710000099.000100"], reply_count={"n": -1}),
    ]
    body = _read(_slack(top=rows), thread="")
    assert body["ok"] is True
    dumped = json.dumps(body)
    assert "not a thread" not in dumped
    assert "1710000099.000100" not in dumped
    assert "nope" not in dumped


def test_a_real_thread_does_not_use_the_channel_unread_note():
    rows = [_post(PARENT, "Which day do we ship?", reply_count=2), _reply(1, "Monday"), _reply(2, "Friday")]
    body = _read(_slack(thread=rows), thread=PARENT)
    assert body["incomplete"] is False
    assert body["note"] == "The read finished."
    assert "unread_replies" not in body


def test_passing_the_current_channel_inside_a_thread_still_reads_the_thread():
    fake = _slack(top=_channel(), thread=[_post(PARENT, "parent"), _reply(1, "reply")])
    body = _read(fake, {"channel": CURRENT}, thread=PARENT)
    assert body["ok"] is True
    assert body["thread"] == PARENT
    assert body["count"] == 2
    assert "conversations.history" not in _apis(fake)


def test_long_bot_only_thread_is_read_when_auth_test_fails_temporarily():
    rows = [_post(PARENT, "question", reply_count=250)]
    rows += [_post(f"{1710000000 + i}.000200", "bot", user=BOT_USER, bot_id=BOT_ID) for i in range(1, 251)]
    fake = _failing_auth((200, {}, {"ok": False, "error": "internal_error"}), [_post("1710000099.000100", "channel")], rows)
    body = _read(fake, thread=PARENT)
    assert body["ok"] is True
    assert body["thread"] == PARENT
    assert body["incomplete"] is True
    assert "conversations.history" not in _apis(fake)
    assert len([call for call in fake.calls if call["api"] == "conversations.replies"]) == 2


def test_skill_searches_the_deferred_catalog_before_saying_unavailable():
    text = (Path(__file__).resolve().parents[1] / "skills" / "slack-history" / "SKILL.md").read_text(encoding="utf-8")
    assert "absent from the tool list, say" not in text
    assert "tool_search" in text
    assert "tool_call" in text
    assert text.index("tool_search") < text.index("unavailable")
    assert "If tool_search finds no slack_history" in text
