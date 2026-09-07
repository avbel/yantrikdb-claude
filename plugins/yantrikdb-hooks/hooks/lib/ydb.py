"""Shared runtime for the yantrikdb-hooks Claude Code plugin.

Design rules, in priority order:

1. A hook MUST NEVER break the session. Every failure path exits 0 with no
   stdout. Exit 2 is never used.
2. stdout is a protocol channel. The engine and its logging write to stderr,
   but a stray print from a dependency would corrupt the hook's JSON, so all
   engine work runs with stdout redirected to stderr and only the final
   payload is written to the real stdout.
3. Engine selection mirrors yantrikdb_mcp.server._LazyDB, so hooks and the
   MCP server talk to the same store (embedded SQLite, or an HTTP cluster
   when YANTRIKDB_SERVER_URL is set). Because Claude Code scopes an MCP
   server's `env` block to that server's process, the hooks also read the
   server definition itself (see adopt_mcp_env) so the two cannot diverge.
4. A hook is bounded in time even when the world is broken: a dead cluster
   is probed once with a short timeout and then remembered as down.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

# ── stdout protection ────────────────────────────────────────────────────────

_REAL_STDOUT = sys.stdout
sys.stdout = sys.stderr


def emit(payload: dict | None = None) -> None:
    """Write the hook payload to the real stdout and exit 0."""
    if payload:
        _REAL_STDOUT.write(json.dumps(payload))
        _REAL_STDOUT.flush()
    _REAL_STDOUT.close()
    os._exit(0)


def emit_context(event: str, text: str) -> None:
    """Inject additionalContext for an event that supports it."""
    text = (text or "").strip()
    if not text:
        emit()
    emit({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}})


# ── config ───────────────────────────────────────────────────────────────────

def env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip())
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip())
    except (TypeError, ValueError):
        return default


# ── MCP server config adoption ───────────────────────────────────────────────

_ADOPTABLE_PREFIX = "YANTRIKDB_"


def _servers_in(data: dict, cwd: str | None) -> list[dict]:
    """All mcpServers maps in a config file, most specific first."""
    maps: list[dict] = []
    if cwd:
        proj = (data.get("projects") or {}).get(cwd) or {}
        if isinstance(proj, dict) and isinstance(proj.get("mcpServers"), dict):
            maps.append(proj["mcpServers"])
    if isinstance(data.get("mcpServers"), dict):
        maps.append(data["mcpServers"])
    return maps


def mcp_env_from(path: Path, cwd: str | None, server: str) -> dict[str, str]:
    """YANTRIKDB_* entries from the named MCP server definition in `path`."""
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    for servers in _servers_in(data, cwd):
        entry = servers.get(server)
        if not isinstance(entry, dict):
            continue
        env = entry.get("env")
        if not isinstance(env, dict):
            return {}
        return {
            k: os.path.expandvars(str(v))
            for k, v in env.items()
            if isinstance(k, str) and k.startswith(_ADOPTABLE_PREFIX)
        }
    return {}


def adopt_mcp_env(cwd: str | None) -> None:
    """Copy YANTRIKDB_* from the MCP server definition into our environment.

    Claude Code scopes `mcpServers.<name>.env` to that server's process; hooks
    only get the plain process environment. Without this, a user whose server
    points at an HTTP cluster gets hooks that silently open a second, embedded
    store. Explicitly set variables always win. Lookup order: <cwd>/.mcp.json,
    then the per-project and global entries of ~/.claude.json; the first
    definition found is used outright.
    """
    if not env_flag("YANTRIKDB_HOOKS_ADOPT_MCP_ENV", True):
        return
    server = os.environ.get("YANTRIKDB_HOOKS_MCP_SERVER", "").strip() or "yantrikdb"
    files: list[Path] = []
    if cwd:
        files.append(Path(cwd) / ".mcp.json")
    files.append(Path.home() / ".claude.json")
    for f in files:
        found = mcp_env_from(f, cwd, server)
        if not found:
            continue
        adopted = []
        for k, v in found.items():
            if k not in os.environ:
                os.environ[k] = v
                adopted.append(k)
        log(f"mcp env from {f}: found {sorted(found)}, adopted {sorted(adopted)}")
        return


# ── namespace ────────────────────────────────────────────────────────────────

def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-.").lower()


def _git_remote_name(root: Path) -> str:
    """Repository name from the origin remote, or "" when there is none."""
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "config", "--get", "remote.origin.url"],
            capture_output=True, text=True, timeout=2,
        )
    except Exception:
        return ""
    url = (r.stdout or "").strip()
    if r.returncode != 0 or not url:
        return ""
    tail = re.split(r"[/:]", url.rstrip("/"))[-1]
    if tail.endswith(".git"):
        tail = tail[:-4]
    return _slug(tail)


def namespace_for(cwd: str | None) -> str:
    """Memory namespace for this session.

    "auto" derives a per-project namespace: the git origin repository name
    when there is one (stable across clones and renames), otherwise the
    directory basename plus a short hash of its absolute path so two projects
    called `api` do not share memories.
    """
    ns = os.environ.get("YANTRIKDB_HOOKS_NAMESPACE", "default").strip() or "default"
    if ns != "auto":
        return ns
    root = Path(cwd or os.getcwd())
    remote = _git_remote_name(root)
    if remote:
        return remote
    try:
        resolved = str(root.resolve())
    except Exception:
        resolved = str(root)
    digest = hashlib.sha1(resolved.encode()).hexdigest()[:6]
    return f"{_slug(root.name) or 'project'}-{digest}"


# ── client id ────────────────────────────────────────────────────────────────

def client_id_for(session_id: str) -> str:
    """Client identifier for a tracked session.

    The server keys an active session on (namespace, client_id), so every
    client that leaves the id empty contends for one slot per namespace: a
    session that never closed (SessionEnd killed, machine slept) blocks every
    later one with a 500. Deriving the id from the Claude Code session id
    gives each session its own slot, so an orphan can only ever block its own
    successor rather than the whole namespace.
    """
    explicit = os.environ.get("YANTRIKDB_HOOKS_CLIENT_ID", "").strip()
    if explicit:
        return explicit
    slug = _slug(session_id or "")[:48]
    return f"claude-code-{slug}" if slug else "claude-code"


# ── plugin state ─────────────────────────────────────────────────────────────

_MARKER = "unreachable.json"
_INTERPRETER = "interpreter"


def state_dir() -> Path:
    root = os.environ.get("CLAUDE_PLUGIN_DATA") or str(Path.home() / ".yantrikdb" / "claude-hooks")
    p = Path(root)
    p.mkdir(parents=True, exist_ok=True)
    return p


def state_path(session_id: str) -> Path:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", session_id or "unknown")[:64]
    return state_dir() / f"{slug}.json"


def read_state(session_id: str) -> dict:
    try:
        return json.loads(state_path(session_id).read_text())
    except Exception:
        return {}


def write_state(session_id: str, **fields) -> None:
    try:
        st = read_state(session_id)
        st.update(fields)
        tmp = state_path(session_id).with_suffix(".tmp")
        tmp.write_text(json.dumps(st))
        tmp.replace(state_path(session_id))
    except Exception:
        pass


def drop_state(session_id: str) -> None:
    with contextlib.suppress(Exception):
        state_path(session_id).unlink()


def sweep_state() -> None:
    """Remove per-session state left behind by sessions that never ended cleanly."""
    max_age = env_int("YANTRIKDB_HOOKS_STATE_MAX_AGE_DAYS", 7) * 86400
    cutoff = time.time() - max_age
    try:
        for p in state_dir().glob("*.json"):
            if p.name == _MARKER:
                continue
            with contextlib.suppress(Exception):
                if p.stat().st_mtime < cutoff:
                    p.unlink()
    except Exception:
        pass


def merge_rids(seen: list, fresh: list, cap: int = 400) -> list:
    """Append new rids in order, dedupe, and trim the oldest past `cap`."""
    out: list = []
    for rid in [*(seen or []), *(fresh or [])]:
        if rid and rid not in out:
            out.append(rid)
    return out[-cap:]


# ── unreachable-cluster marker ───────────────────────────────────────────────

def unreachable_marker_path() -> Path:
    return state_dir() / _MARKER


def cluster_marked_down(url: str) -> bool:
    try:
        m = json.loads(unreachable_marker_path().read_text())
        return m.get("url") == url and float(m.get("until", 0)) > time.time()
    except Exception:
        return False


def mark_cluster_down(url: str) -> None:
    ttl = max(1, env_int("YANTRIKDB_HOOKS_UNREACHABLE_TTL", 60))
    with contextlib.suppress(Exception):
        unreachable_marker_path().write_text(json.dumps({"url": url, "until": time.time() + ttl}))


def _probe_cluster(nodes: list[str], token: str, timeout: float) -> bool:
    """One bounded GET /v1/health per node; True as soon as one answers."""
    import requests

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    for node in nodes:
        try:
            r = requests.get(f"{node}/v1/health", headers=headers, timeout=timeout)
            if r.ok:
                return True
        except Exception:
            continue
    return False


# ── stdin ────────────────────────────────────────────────────────────────────

def read_event() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


# ── engine ───────────────────────────────────────────────────────────────────

def _evict_interpreter_cache() -> None:
    """run.sh trusts its cached interpreter; if the package is gone, forget it."""
    with contextlib.suppress(Exception):
        (state_dir() / _INTERPRETER).unlink()


def open_db():
    """Open the same store the yantrikdb MCP server uses.

    Returns None if the package is not installed, the cluster is down, or the
    store cannot be opened; callers must treat None as "stay silent".
    """
    try:
        server_url = os.environ.get("YANTRIKDB_SERVER_URL", "").strip()
        if server_url:
            return _open_http(server_url)
        return _open_embedded()
    except ModuleNotFoundError as e:
        if (e.name or "").startswith("yantrikdb"):
            _evict_interpreter_cache()
        log(f"engine unavailable: {e}")
        return None
    except Exception as e:  # noqa: BLE001
        log(f"engine unavailable: {e}")
        return None


def _open_http(server_url: str):
    if cluster_marked_down(server_url):
        log("cluster marked unreachable, skipping")
        return None
    from yantrikdb_mcp.http_backend import HttpBackend

    nodes = [u.strip().rstrip("/") for u in server_url.split(",") if u.strip()]
    token = os.environ.get("YANTRIKDB_TOKEN", "")
    timeout = max(1, env_int("YANTRIKDB_HOOKS_HTTP_TIMEOUT", 3))
    if not _probe_cluster(nodes, token, timeout):
        mark_cluster_down(server_url)
        log(f"cluster unreachable: {server_url}")
        return None
    return HttpBackend(server_urls=nodes, token=token, timeout=timeout)


def _open_embedded():
    from yantrikdb_mcp.embedder import load_engine

    db_path = os.environ.get("YANTRIKDB_DB_PATH", str(Path.home() / ".yantrikdb" / "memory.db"))
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    model = os.environ.get("YANTRIKDB_EMBEDDING_MODEL", "all-MiniLM-L6-v2")

    # The MCP server may hold the SQLite handle; a contended open is
    # transient, so back off briefly before giving up.
    last: Exception | None = None
    for attempt in range(3):
        try:
            return load_engine(db_path, model_name=model)
        except ModuleNotFoundError:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.4 * (attempt + 1))
    raise last  # type: ignore[misc]


def is_http(db) -> bool:
    return type(db).__name__ == "HttpBackend"


def close_db(db) -> None:
    with contextlib.suppress(Exception):
        db.close()


def as_obj(value):
    """session_digest returns JSON text on the embedded engine and a dict on
    the HTTP backend. Normalize both."""
    if isinstance(value, (str, bytes)):
        try:
            return json.loads(value)
        except Exception:
            return {}
    return value if isinstance(value, dict) else {}


def flex(fn, **kwargs):
    """Call an engine method, dropping keyword arguments the installed build
    does not accept.

    The embedded Rust engine and the HTTP backend are versioned independently
    of this plugin and raise TypeError rather than ignoring an unknown
    keyword. Retrying without the rejected argument keeps the hook working on
    old and new engines instead of silently producing nothing.
    """
    kw = dict(kwargs)
    for _ in range(len(kw) + 1):
        try:
            return fn(**kw)
        except TypeError as e:
            m = re.search("unexpected keyword argument ['\"]([^'\"]+)['\"]", str(e))
            if m and m.group(1) in kw:
                log(f"dropping unsupported kwarg {m.group(1)}")
                kw.pop(m.group(1))
                continue
            raise
    return fn()


def as_id(value) -> str:
    """session_start returns a bare id string on the embedded engine and a
    dict on the HTTP backend."""
    if isinstance(value, (str, bytes)):
        v = value.decode() if isinstance(value, bytes) else value
        v = v.strip()
        if v.startswith("{"):
            d = as_obj(v)
            return str(d.get("session_id") or d.get("id") or "")
        return v
    if isinstance(value, dict):
        return str(value.get("session_id") or value.get("id") or "")
    return ""


def is_weak(why) -> str:
    """Return a short staleness note when the engine flagged a hit as weak."""
    marks = ("aged", "rarely confirmed", "superseded", "stale", "disputed")
    items = why if isinstance(why, (list, tuple)) else [why]
    for item in items:
        text = " ".join(str(item).split())
        if any(m in text.lower() for m in marks):
            return text[:80]
    return ""


def recent_records(db, ns: str, limit: int) -> tuple[list, str]:
    """Rows for the empty-digest fallback and a label describing them.

    `list_memories` is embedded-only; the HTTP backend raises. Fall back to a
    broad recall there so an HTTP user still sees something on a young store.
    """
    ns_arg = None if ns == "default" else ns
    try:
        rows = flex(db.list_memories, limit=limit, namespace=ns_arg)
        rows = rows.get("memories", []) if isinstance(rows, dict) else (rows or [])
        return list(rows), "most recent records"
    except Exception as e:  # noqa: BLE001
        log(f"list_memories failed: {e}")
    try:
        rows = flex(db.recall, query="recent decisions, preferences and project context",
                    top_k=limit, namespace=ns_arg, expand_entities=True)
        return list(rows or []), "most relevant records"
    except Exception as e:  # noqa: BLE001
        log(f"fallback recall failed: {e}")
    return [], ""


# ── misc ─────────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    if env_flag("YANTRIKDB_HOOKS_DEBUG", False):
        sys.stderr.write(f"[yantrikdb-hooks] {msg}\n")


def guard(seconds: int) -> None:
    """Hard self-deadline: emit nothing and exit 0 rather than stalling the
    session if the engine wedges. Claude Code's own hook timeout kills the
    process, but this keeps the exit clean when we own the clock."""

    def _bail(_sig, _frm):
        os._exit(0)

    with contextlib.suppress(Exception):
        signal.signal(signal.SIGALRM, _bail)
        signal.alarm(max(1, seconds))


def run(fn) -> None:
    """Entry point wrapper — any unhandled failure is silent and non-blocking."""
    try:
        fn()
    except SystemExit:
        raise
    except BaseException as e:  # noqa: BLE001
        log(f"hook failed: {type(e).__name__}: {e}")
    emit()
