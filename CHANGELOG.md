# Changelog

## 0.1.0

A channel read includes replies inside threads. It reads at most 5 parent messages, newest first, and one page of `conversations.replies` for each. Reply text counts toward the same 50 message cap and the 12000 character cap. Channel history still stops after at most 4 pages. Replies past those caps stay in the note. A reply already posted to the channel is not repeated. A thread read is unchanged.
