#!/usr/bin/env python3
"""PreCompact / SessionEnd — capture what is about to be lost, then maintain.

PreCompact is the one moment where context loss is certain and scheduled, so
that is where capture belongs. SessionEnd closes the tracked session and runs
one incremental hygiene cycle (consolidation, conflict scan, decay), which the
engine is explicitly designed to have called often.

Capture writes DRAFT memories via draft_memories_from_summary — the engine
atomizes the transcript tail into candidate facts rather than storing the raw
log. The text is redacted first. Set YANTRIKDB_HOOKS_CAPTURE=0 to disable if
the drafts get noisy.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import redact  # noqa: E402
import transcript  # noqa: E402
import ydb  # noqa: E402

MIN_DRAFT_CHARS = 200


def _mark(line: str) -> str:
    return hashlib.sha1(line.encode()).hexdigest()[:16]


def capture(db, event: dict, state: dict, session_id: str, ns: str, which: str) -> None:
    rows = transcript.lines(
        event.get("transcript_path"),
        max_turns=ydb.env_int("YANTRIKDB_HOOKS_CAPTURE_TURNS", 40),
        roles=os.environ.get("YANTRIKDB_HOOKS_CAPTURE_ROLES", "user"),
    )
    # PreCompact and SessionEnd both see the same transcript tail. Without a
    # watermark the second run re-drafts what the first already stored, and
    # the engine correctly reports them as 100%-redundant pairs.
    done = set(state.get("captured") or [])
    fresh = [r for r in rows if _mark(r) not in done]
    blob = redact.redact(transcript.clamp(fresh, ydb.env_int("YANTRIKDB_HOOKS_CAPTURE_CHARS", 6000)))
    if len(blob) < MIN_DRAFT_CHARS:
        ydb.log(f"capture({which}) skipped: {len(blob)} new chars")
        return
    try:
        res = ydb.flex(
            lambda **kw: db.draft_memories_from_summary(blob, **kw),
            namespace=ns,
            domain="work",
        )
        ydb.log(f"capture({which}) drafted: {res}")
        ydb.write_state(session_id, captured=list(done | {_mark(r) for r in fresh})[-600:])
    except Exception as e:  # noqa: BLE001
        ydb.log(f"draft failed: {e}")


def finish_session(db, state: dict) -> None:
    rid = state.get("ydb_session_id")
    if rid:
        try:
            db.session_end(rid)
        except Exception as e:  # noqa: BLE001
            ydb.log(f"session_end failed: {e}")
    if not ydb.env_flag("YANTRIKDB_HOOKS_MAINTENANCE", True):
        return
    # The full hygiene cycle is embedded-only; the HTTP gateway exposes think().
    if not ydb.is_http(db):
        try:
            ydb.flex(db.run_maintenance_cycle)
            return
        except Exception as e:  # noqa: BLE001
            ydb.log(f"maintenance failed: {e}")
    try:
        db.think()
    except Exception as e:  # noqa: BLE001
        ydb.log(f"think failed: {e}")


def main() -> None:
    ydb.guard(ydb.env_int("YANTRIKDB_HOOKS_CAPTURE_TIMEOUT", 110))
    event = ydb.read_event()

    which = (sys.argv[1] if len(sys.argv) > 1 else event.get("hook_event_name") or "").strip()
    cwd = event.get("cwd")
    ydb.adopt_mcp_env(cwd)
    session_id = event.get("session_id") or ""
    ns = ydb.namespace_for(cwd)
    state = ydb.read_state(session_id)

    db = ydb.open_db()
    if db is None:
        if which == "SessionEnd":
            ydb.drop_state(session_id)
        ydb.emit()

    if ydb.env_flag("YANTRIKDB_HOOKS_CAPTURE", True):
        capture(db, event, state, session_id, ns, which)

    if which == "SessionEnd":
        finish_session(db, state)
        ydb.drop_state(session_id)

    ydb.close_db(db)
    ydb.emit()


ydb.run(main)
