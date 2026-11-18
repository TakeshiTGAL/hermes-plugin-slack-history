"""The public session prompt, after this plugin is enabled."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_SCRIPT = textwrap.dedent(
    """\
    import os, shutil, sys
    from pathlib import Path

    src, plugin, home, token_on = sys.argv[1:]
    os.environ["HERMES_HOME"] = home
    os.environ.pop("SLACK_BOT_TOKEN", None)
    os.environ.pop("SLACK_APP_TOKEN", None)
    if token_on == "yes":
        os.environ["SLACK_BOT_TOKEN"] = "xoxb-unit-not-real"
    sys.path.insert(0, src)
    dest = Path(home) / "plugins" / "slack-history"
    shutil.copytree(
        plugin,
        dest,
        ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", "tests"),
        dirs_exist_ok=True,
    )
    Path(home, "config.yaml").write_text(
        "plugins:\\n  enabled:\\n    - slack-history\\n",
        encoding="utf-8",
    )
    try:
        from gateway.session import Platform, SessionContext, SessionSource, build_session_context_prompt
        from hermes_cli.plugins import discover_plugins
        discover_plugins()
        ctx = SessionContext(
            source=SessionSource(
                platform=Platform.SLACK,
                chat_id="CPUB00001",
                chat_name="general",
                chat_type="channel",
                user_id="U01234567",
                user_name="ada",
            ),
            connected_platforms=[Platform.SLACK],
            home_channels={},
        )
        text = build_session_context_prompt(ctx)
    except Exception as exc:
        message = str(exc).replace("xoxb-unit-not-real", "[redacted]")
        print(type(exc).__name__ + ": " + message.splitlines()[0][:300])
        sys.exit(2)
    note = next((line for line in text.splitlines() if line.startswith("**Platform notes:**")), "NO_PLATFORM_LINE")
    refusal = "cannot search channel history" in note
    tools = "have access to Slack-specific tools" in note
    leaked = "xoxb-" in text
    print("refusal=" + str(refusal))
    print("tools_note=" + str(tools))
    print("leaked=" + str(leaked))
    print("PLATFORM_NOTE=" + note)
    if leaked:
        sys.exit(2)
    if token_on == "yes":
        sys.exit(0 if tools and not refusal else 2)
    sys.exit(0 if refusal and not tools else 2)
    """
)


def _run(token_on: str) -> subprocess.CompletedProcess:
    src = os.environ.get("HERMES_SRC", "")
    if not src or not Path(src).is_dir():
        pytest.skip("HERMES_SRC is unset")
    home = Path(os.environ.get("TMPDIR", "/tmp")) / f"slack-history-note-{token_on}-{os.getpid()}"
    if home.exists():
        shutil.rmtree(home)
    home.mkdir(parents=True)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home / "user"),
        "HERMES_HOME": str(home),
        "HERMES_SRC": src,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": src,
    }
    (home / "user").mkdir()
    return subprocess.run(
        [sys.executable, "-c", _SCRIPT, src, str(ROOT), str(home), token_on],
        cwd="/tmp",
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_token_and_slack_toolset_replace_the_refusal():
    proc = _run("yes")
    assert proc.returncode == 0, (proc.stdout[-500:], proc.stderr[-500:])
    note = next(line for line in proc.stdout.splitlines() if line.startswith("PLATFORM_NOTE="))
    assert "cannot search channel history" not in note
    assert "have access to Slack-specific tools" in note
    assert "xoxb-" not in proc.stdout


def test_missing_token_keeps_the_refusal():
    proc = _run("no")
    assert proc.returncode == 0, (proc.stdout[-500:], proc.stderr[-500:])
    note = next(line for line in proc.stdout.splitlines() if line.startswith("PLATFORM_NOTE="))
    assert "cannot search channel history" in note
