# Shapes copied from Slack's docs

No live Slack workspace was called while building this plugin. Response fields below are copied from the public method pages on 2026-11-18.

- `conversations.history` and `conversations.replies` are GET `https://slack.com/api/<method>`. A success body has `ok`, `messages` (`user`, `text`, `ts`), `has_more`, and `response_metadata.next_cursor`. Documented errors used here include `not_in_channel`, `missing_scope`, `channel_not_found`, `thread_not_found`, `invalid_auth`, `token_expired`, `token_revoked`, `account_inactive`, and `ratelimited`. `ratelimited` points at the `Retry-After` header. Source: https://docs.slack.dev/reference/methods/conversations.history and https://docs.slack.dev/reference/methods/conversations.replies
- `users.info` is GET and returns `user.id`, `user.name`, `user.real_name`, and `user.profile.display_name`. `users:read` is the documented bot scope. This plugin drops `email` and image fields when they are present. A failure of this call leaves the user id in the transcript. Source: https://docs.slack.dev/reference/methods/users.info
- `auth.test` is POST and returns `url`, `team`, `user`, `team_id`, and `user_id` for a bot token. The permalink is built from `url` as `{url}/archives/{channel}/p{ts without the dot}`. A thread adds `thread_ts` and `cid`. Source: https://docs.slack.dev/reference/methods/auth.test

- `conversations.replies` is treated as parent first, then replies oldest first, from the response example on its method page. The page gives no general ordering rule, so the plugin sorts by `ts` and drops a repeated `ts`.

The client allowlist is `conversations.history`, `conversations.replies`, `users.info`, and `auth.test`.
