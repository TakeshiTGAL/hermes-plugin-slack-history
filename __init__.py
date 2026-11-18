"""Register the Slack history tool on the slack toolset."""

from __future__ import annotations

from pathlib import Path

try:
    from .history import TOOL_DESCRIPTION, TOOL_NAME, TOOL_SCHEMA, TOOLSET, run, token_is_set
except ImportError:
    from history import TOOL_DESCRIPTION, TOOL_NAME, TOOL_SCHEMA, TOOLSET, run, token_is_set

_SKILL_DESCRIPTION = (
    "Read the current Slack channel or thread and summarize what was said. "
    "Call slack_history. It does not post."
)


def register(ctx) -> None:
    def handle(args, **kwargs):
        payload = args if isinstance(args, dict) else {}
        return run(payload, config=lambda: ctx.get_config("extra_channels", []))

    ctx.register_tool(
        name=TOOL_NAME,
        toolset=TOOLSET,
        schema=TOOL_SCHEMA,
        handler=handle,
        check_fn=token_is_set,
        description=TOOL_DESCRIPTION,
    )
    ctx.register_skill(
        "slack-history",
        Path(__file__).resolve().parent / "skills" / "slack-history" / "SKILL.md",
        description=_SKILL_DESCRIPTION,
    )
