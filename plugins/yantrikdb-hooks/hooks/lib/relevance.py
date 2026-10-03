"""Relevance gate applied to recalled memories before anything is injected.

Measured on a real store (2026-10): 65 % of active records descended from
auto-captured user prompts, and consolidation relabels those merges as
source=user / domain=general, so the source field alone cannot catch them.
Merges of curated facts were never shorter than 825 chars, merges of prompts
had a median of 194. The 64-dim bundled embedder squeezes blended scores into
0.3-0.65 for relevant and irrelevant hits alike, so the gate uses the cosine of
the semantic lane that the engine reports in `why_retrieved` instead. With
the server on MiniLM-384, a 0.50 floor kept 13/18 known answers and silenced
12/12 irrelevant prompts (the bundled 64-dim embedder needs about 0.60).
"""

from __future__ import annotations

import json
import os
import re
import statistics

from ydb import capture_enabled, env_flag, env_float

AUTO_CAPTURE = "session_auto_capture"
_SIMILARITY = re.compile(r"semantically similar \(([0-9]*\.?[0-9]+)\)")
_MERGE_MIN_CHARS = 600
_MERGE_MIN_SEGMENT = 160


def _field(row, name, default=None):
    if isinstance(row, dict):
        return row.get(name, default)
    return getattr(row, name, default)


def _metadata(row) -> dict:
    value = _field(row, "metadata")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def semantic_similarity(why) -> float | None:
    """Cosine of the semantic lane, or None when only keyword/graph lanes matched."""
    for item in why if isinstance(why, (list, tuple)) else [why]:
        match = _SIMILARITY.search(str(item or ""))
        if match:
            return float(match.group(1))
    return None


def is_prompt_merge(text: str, metadata: dict) -> bool:
    if not metadata.get("consolidated_from"):
        return False
    segments = [s for s in text.split(" | ") if s.strip()] or [text]
    return len(text) < _MERGE_MIN_CHARS or statistics.median(len(s) for s in segments) < _MERGE_MIN_SEGMENT


def noise_reason(row, *, include_captured: bool = False, excluded: tuple[str, ...] = ()) -> str | None:
    """Why a hit must not be injected, or None when it may be."""
    namespace = str(_field(row, "namespace") or "")
    if any(namespace.startswith(prefix) for prefix in excluded):
        return "namespace"
    if include_captured:
        return None
    metadata = _metadata(row)
    if _field(row, "source") == AUTO_CAPTURE or metadata.get("kind") == AUTO_CAPTURE:
        return "auto-capture"
    if is_prompt_merge(str(_field(row, "text") or ""), metadata):
        return "prompt-merge"
    return None


def filter_hits(hits, *, min_similarity: float = 0.0, include_captured: bool = False,
                excluded: tuple[str, ...] = (), limit: int | None = None) -> list:
    kept = []
    for row in hits or []:
        if noise_reason(row, include_captured=include_captured, excluded=excluded):
            continue
        if min_similarity > 0:
            similarity = semantic_similarity(_field(row, "why_retrieved"))
            if similarity is None or similarity < min_similarity:
                continue
        kept.append(row)
        if limit is not None and len(kept) >= limit:
            break
    return kept


def config() -> dict:
    raw = os.environ.get("YANTRIKDB_HOOKS_EXCLUDE_NAMESPACES", "")
    excluded = tuple(p.strip() for p in raw.split(",") if p.strip())
    return {
        "excluded": excluded,
        "min_similarity": env_float("YANTRIKDB_HOOKS_MIN_SIMILARITY", 0.50),
        "include_captured": env_flag("YANTRIKDB_HOOKS_RECALL_CAPTURED", capture_enabled()),
    }
