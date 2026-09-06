"""Bounded, defensive reader for a Claude Code transcript (.jsonl).

The transcript schema is not a stable contract, so every field access is
tolerant: unknown shapes yield fewer turns, never an exception.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

_MAX_BYTES = 4 * 1024 * 1024  # only ever read the tail of a long transcript

# Claude Code writes its own bookkeeping into user turns: slash-command echoes,
# system reminders, task notifications. None of it is something the user said.
# Only these known tags are meta; a prompt that starts with "<div>" is not.
_META_PREFIXES = (
    "<command-name>", "<command-message>", "<command-args>",
    "<local-command-stdout>", "<local-command-caveat>",
    "<system-reminder>", "<task-notification>", "<user-prompt-submit-hook>",
    "<ide_", "<bash-input>", "<bash-stdout>", "<bash-stderr>", "<antml",
)
_META_SPANS = re.compile(
    r"<(system-reminder|task-notification|local-command-stdout|local-command-caveat)>"
    r".*?</\1>",
    re.S,
)


def is_meta(text: str) -> bool:
    return text.lstrip().startswith(_META_PREFIXES)


def clean_text(text: str) -> str:
    """Strip embedded meta spans and collapse whitespace."""
    return " ".join(_META_SPANS.sub(" ", text or "").split())


def _text_of(content) -> str:
    if isinstance(content, str):
        return "" if is_meta(content) else content
    if isinstance(content, list):
        parts = []
        for blk in content:
            if isinstance(blk, dict) and blk.get("type") == "text":
                t = blk.get("text")
                if isinstance(t, str) and not is_meta(t):
                    parts.append(t)
        return "\n".join(parts)
    return ""


def turns(path: str | None, limit: int = 40) -> list[tuple[str, str]]:
    """Return the last `limit` (role, text) pairs, oldest-first."""
    if not path:
        return []
    p = Path(path)
    if not p.is_file():
        return []
    try:
        size = p.stat().st_size
        with p.open("rb") as fh:
            if size > _MAX_BYTES:
                fh.seek(size - _MAX_BYTES, os.SEEK_SET)
                fh.readline()  # discard the partial line
            raw = fh.read().decode("utf-8", "replace")
    except Exception:
        return []

    out: list[tuple[str, str]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if not isinstance(rec, dict):
            continue
        if rec.get("isMeta") or rec.get("isSidechain"):
            continue
        msg = rec.get("message")
        role = None
        content = None
        if isinstance(msg, dict):
            role = msg.get("role") or rec.get("type")
            content = msg.get("content")
        elif rec.get("type") in ("user", "assistant"):
            role = rec.get("type")
            content = rec.get("content")
        if role not in ("user", "assistant"):
            continue
        text = clean_text(_text_of(content))
        if not text:
            continue
        out.append((role, text))

    return out[-limit:]


def lines(path: str | None, *, max_turns: int = 40, roles: str = "user") -> list[str]:
    """Cleaned, sentence-terminated lines from the transcript tail.

    Default is user turns only. Assistant turns are the model's own claims;
    drafting them into long-term memory is how a memory store poisons itself —
    a guess restated next session as a remembered fact. Set roles="all" to
    include them anyway.

    Role labels are deliberately NOT written into the text: the engine
    atomizes this text into memory records close to verbatim, so a "user:"
    prefix ends up stored inside the fact.
    """
    wanted = ("user", "assistant") if roles.strip().lower() == "all" else ("user",)
    out = []
    for role, text in turns(path, limit=max_turns):
        if role not in wanted:
            continue
        if len(text) < 12:
            continue
        out.append(text if text.endswith((".", "!", "?")) else text + ".")
    return out


def clamp(rows: list[str], max_chars: int = 6000) -> str:
    """Join lines newest-first-preserving, trimmed to max_chars."""
    blob = "\n".join(rows)
    if len(blob) > max_chars:
        blob = blob[-max_chars:]
        nl = blob.find("\n")
        if nl != -1:
            blob = blob[nl + 1 :]
    return blob


def summary(path: str | None, *, max_turns: int = 40, max_chars: int = 6000,
            roles: str = "user") -> str:
    return clamp(lines(path, max_turns=max_turns, roles=roles), max_chars)
