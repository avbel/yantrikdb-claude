"""Shared fixtures for the yantrikdb-hooks tests (unittest, no third-party deps)."""

from __future__ import annotations

import contextlib
import importlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOKS = REPO / "plugins" / "yantrikdb-hooks" / "hooks"
LIB = HOOKS / "lib"
RUN_SH = HOOKS / "run.sh"

if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))


def load(name: str):
    """Import (or re-import) a lib module so env changes are picked up."""
    if name in sys.modules:
        return importlib.reload(sys.modules[name])
    return importlib.import_module(name)


@contextlib.contextmanager
def env(**overrides: str | None):
    """Temporarily set (or unset, with None) environment variables."""
    saved = {k: os.environ.get(k) for k in overrides}
    for k, v in overrides.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextlib.contextmanager
def sandbox():
    """A throwaway HOME + plugin data dir, wiped afterwards."""
    root = Path(tempfile.mkdtemp(prefix="ydb-hooks-test-"))
    home = root / "home"
    data = root / "data"
    home.mkdir()
    data.mkdir()
    with env(HOME=str(home), CLAUDE_PLUGIN_DATA=str(data)):
        try:
            yield root
        finally:
            shutil.rmtree(root, ignore_errors=True)


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


def transcript_lines() -> list[dict]:
    """A realistic transcript tail: user turns, an assistant turn, meta noise."""
    return [
        {"type": "user", "uuid": "1", "message": {"role": "user", "content":
            "We decided to use Postgres 16 for the indexer and drop the ClickHouse mirror because sync lag was unacceptable."}},
        {"type": "assistant", "uuid": "2", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Understood, I'll migrate the schema."}]}},
        {"type": "user", "uuid": "3", "message": {"role": "user", "content":
            "<command-name>/model</command-name><command-message>model</command-message>"}},
        {"type": "user", "uuid": "4", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "x", "content": "stdout of a tool"}]}},
        {"type": "user", "uuid": "5", "message": {"role": "user", "content": [
            {"type": "text", "text": "Also remember: the deploy target is the GCP VM named indexer-prod-1 in europe-west4, and releases must go through Dokploy, never manual docker run."},
            {"type": "text", "text": "<system-reminder>\nThis is an automated reminder that must not be stored.\n</system-reminder>"}]}},
        {"type": "user", "uuid": "6", "message": {"role": "user", "content":
            "<div>pasted html</div> should still count as a user prompt because it is not a Claude Code meta tag."}},
        {"type": "user", "uuid": "7", "message": {"role": "user", "content":
            "Please keep the CLI flag names stable, downstream scripts parse them. Prefer long descriptive names over abbreviations everywhere in this repo."}},
    ]


def write_transcript(path: Path) -> None:
    path.write_text("\n".join(json.dumps(r) for r in transcript_lines()) + "\n")
