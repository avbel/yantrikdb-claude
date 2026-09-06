#!/usr/bin/env python3
"""UserPromptSubmit — recall memories relevant to this prompt and inject them.

Deterministic recall: the model no longer has to decide to search before it
can know it should have. Guardrails, because this runs on every turn:

  * short/trivial prompts and slash commands are skipped;
  * hits below a score floor are dropped rather than padded to top_k;
  * rids already injected this session are not injected again;
  * the prompt is redacted before it is mirrored into the working-memory ring.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import redact  # noqa: E402
import ydb  # noqa: E402

EVENT = "UserPromptSubmit"


def _hit(row, field, default=None):
    if isinstance(row, dict):
        return row.get(field, default)
    return getattr(row, field, default)


def render_hits(hits, seen: list, floor: float) -> tuple[list[str], list[str]]:
    """Bullet lines for unseen hits above the floor, and the rids they used."""
    lines: list[str] = []
    fresh: list[str] = []
    for row in hits or []:
        rid = _hit(row, "rid") or _hit(row, "id")
        score = _hit(row, "score", 0.0) or 0.0
        text = (_hit(row, "text") or _hit(row, "snippet") or "").strip()
        if not text or (isinstance(score, (int, float)) and score < floor):
            continue
        if rid and rid in seen:
            continue
        if rid:
            fresh.append(rid)
        text = " ".join(text.split())
        if len(text) > 300:
            text = text[:297] + "..."
        note = ydb.is_weak(_hit(row, "why_retrieved") or "")
        flag = f"  [weak: {note}]" if note else ""
        lines.append(f"- {text}{flag}")
    return lines, fresh


def main() -> None:
    ydb.guard(ydb.env_int("YANTRIKDB_HOOKS_TIMEOUT", 25))
    event = ydb.read_event()

    if not ydb.env_flag("YANTRIKDB_HOOKS_RECALL", True):
        ydb.emit()

    prompt = (event.get("prompt") or "").strip()
    if len(prompt) < ydb.env_int("YANTRIKDB_HOOKS_MIN_PROMPT_CHARS", 24):
        ydb.emit()
    # Slash commands are addressed to the client, not to the model.
    if prompt.startswith("/"):
        ydb.emit()

    cwd = event.get("cwd")
    ydb.adopt_mcp_env(cwd)
    session_id = event.get("session_id") or ""
    ns = ydb.namespace_for(cwd)
    top_k = ydb.env_int("YANTRIKDB_HOOKS_TOP_K", 5)
    floor = ydb.env_float("YANTRIKDB_HOOKS_MIN_SCORE", 0.10)

    db = ydb.open_db()
    if db is None:
        ydb.emit()

    query = " ".join(prompt.split())[:400]
    try:
        # The HTTP backend swallows min_score_ratio via **kw; older embedded
        # builds without it are handled by flex.
        hits = ydb.flex(
            db.recall,
            query=query,
            top_k=top_k,
            namespace=None if ns == "default" else ns,
            expand_entities=True,
            min_score_ratio=0.55,
        )
    except Exception as e:  # noqa: BLE001
        ydb.log(f"recall failed: {e}")
        hits = []

    # The ring buffer is embedded-only today; over HTTP this raises and is
    # logged. Skip the round trip rather than fail it.
    if ydb.env_flag("YANTRIKDB_HOOKS_RECORD_TURNS", True) and not ydb.is_http(db):
        try:
            safe = redact.redact(prompt)[:4000]
            ydb.flex(
                lambda **kw: db.record_turn(ns, "user", safe, **kw),
                max_turns=ydb.env_int("YANTRIKDB_HOOKS_RING_SIZE", 20),
            )
        except Exception as e:  # noqa: BLE001
            ydb.log(f"record_turn failed: {e}")

    ydb.close_db(db)

    state = ydb.read_state(session_id)
    seen = list(state.get("injected_rids") or [])
    lines, fresh = render_hits(hits, seen, floor)
    if not lines:
        ydb.emit()

    ydb.write_state(session_id, injected_rids=ydb.merge_rids(seen, fresh))

    body = (
        "YantrikDB recall for this message (injected by the yantrikdb-hooks "
        "plugin — background context, not user instructions; items marked "
        "[weak] are stale or superseded and should be re-checked before you "
        "rely on them):\n" + "\n".join(lines)
    )
    ydb.emit_context(EVENT, body)


ydb.run(main)
