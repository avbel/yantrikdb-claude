# yantrikdb-hooks

Deterministic [YantrikDB](https://github.com/yantrikos/yantrikdb) memory for Claude Code.

The `yantrikdb-mcp` server exposes memory as tools and asks the model, in its
server instructions, to follow a "golden path": digest at conversation start,
remember/correct while working, consolidate at the end. That is a request, not
a guarantee — the model has to *choose* to call the tools, and it cannot know
it should search before it has searched.

This plugin makes the same path deterministic by wiring it to lifecycle hooks:

| Hook | What runs | Effect |
|------|-----------|--------|
| `SessionStart` | `session_digest()` + `session_start()` | The boot briefing is in context before the first token. Falls back to the most recent records when the digest is still empty. |
| `UserPromptSubmit` | `recall(query=<prompt>)` + `record_turn()` | Relevant memories are injected on every substantive prompt, whether or not the model would have searched. |
| `PreCompact` | `draft_memories_from_summary()` | Captures the transcript tail at the one moment context loss is certain and scheduled. |
| `SessionEnd` | capture + `session_end()` + `run_maintenance_cycle()` | Closes the tracked session and runs one hygiene cycle (consolidation, conflict scan, decay). |

Text that goes into memory (the per-prompt ring and the drafted transcript
tail) is passed through a secret-redaction filter first, and Claude Code's own
bookkeeping inside user turns (slash-command echoes, system reminders, task
notifications) is stripped before drafting.

It complements the MCP server, it does not replace it — the model still needs
`yantrikdb-mcp` connected to write, correct, and query on demand.

## Requirements

- `yantrikdb-mcp` installed and importable (`pip install yantrikdb-mcp`)
- Python 3.10+
- bash

Hooks open the **same** store the MCP server does, resolving it the way
`yantrikdb_mcp.server._LazyDB` does: `YANTRIKDB_SERVER_URL` (HTTP cluster) if
set, otherwise the embedded engine at `YANTRIKDB_DB_PATH`.

Claude Code scopes an MCP server's `env` block to that server's process, so a
hook does not see it. To keep the two from diverging, each hook also reads the
`yantrikdb` server definition itself (`<cwd>/.mcp.json`, then the per-project
and global entries in `~/.claude.json`) and adopts every `YANTRIKDB_*` value it
finds there that is not already set in the environment. Explicit environment
variables always win. See `YANTRIKDB_HOOKS_ADOPT_MCP_ENV` and
`YANTRIKDB_HOOKS_MCP_SERVER` below.

### HTTP backend limitations

The HTTP gateway does not expose everything the embedded engine does. With
`YANTRIKDB_SERVER_URL` set:

| Feature | Embedded | HTTP |
|---------|----------|------|
| Boot digest, recall, drafting, `session_start`/`session_end` | yes | yes |
| Per-prompt ring buffer (`record_turn`) | yes | no, skipped |
| Empty-digest fallback | `list_memories` | broad `recall` |
| Session-end hygiene | `run_maintenance_cycle()` | `think()` |

A cluster that does not answer a health probe within
`YANTRIKDB_HOOKS_HTTP_TIMEOUT` seconds is remembered as down for
`YANTRIKDB_HOOKS_UNREACHABLE_TTL` seconds, so a dead cluster costs one short
probe per minute rather than a stall on every prompt.

## Install

```bash
git clone <this-repo> ~/src/yantrikdb-claude
```

In Claude Code:

```
/plugin marketplace add ~/src/yantrikdb-claude
/plugin install yantrikdb-hooks@yantrikdb-claude
```

Or, for a throwaway test without touching your marketplaces:

```bash
claude --plugin-dir ~/src/yantrikdb-claude/plugins/yantrikdb-hooks
```

Verify with `/hooks` — you should see four events registered under
`yantrikdb-hooks`. Then set `YANTRIKDB_HOOKS_DEBUG=1` and watch stderr on the
first session.

## Interpreter resolution

Claude Code runs hooks with a minimal environment, so `python3` on `PATH` is
often not the interpreter that has yantrikdb. `hooks/run.sh` resolves, in order:

1. `$YANTRIKDB_PYTHON`
2. the cached winner of a previous lookup
3. `~/.yantrikdb/venv/bin/python`
4. the shebang of the `yantrikdb-mcp` console script on `PATH`
5. `python3` / `python`

Candidates from steps 3-5 must actually `import yantrikdb_mcp`; the winner is
cached under the plugin data dir. Steps 1-2 are trusted if the path is
executable, because re-probing the import on every prompt cost about as much
as the hook itself. If the package is later uninstalled, the Python side
evicts the cache and the next run resolves again. If nothing qualifies, every
hook is a silent no-op.

## Configuration

All settings are environment variables, read at hook time.

| Variable | Default | Meaning |
|----------|---------|---------|
| `YANTRIKDB_HOOKS_NAMESPACE` | `default` | Memory namespace. `auto` derives a per-project namespace: the git `origin` repository name when there is one, otherwise `<basename>-<6-char path hash>` so two projects called `api` do not share memories. |
| `YANTRIKDB_HOOKS_ADOPT_MCP_ENV` | `1` | Read `YANTRIKDB_*` from the MCP server definition when not set in the environment. |
| `YANTRIKDB_HOOKS_MCP_SERVER` | `yantrikdb` | Name of that MCP server entry. |
| `YANTRIKDB_HOOKS_HTTP_TIMEOUT` | `3` | Per-request timeout against an HTTP cluster (seconds); also the health-probe budget. |
| `YANTRIKDB_HOOKS_UNREACHABLE_TTL` | `60` | How long a failed probe keeps the cluster marked down (seconds). |
| `YANTRIKDB_HOOKS_REDACT` | `1` | Mask credential-shaped strings before text is stored. |
| `YANTRIKDB_HOOKS_STATE_MAX_AGE_DAYS` | `7` | Per-session state older than this is swept at session start. |
| `YANTRIKDB_HOOKS_DIGEST` | `1` | Inject the boot digest at session start. |
| `YANTRIKDB_HOOKS_GAPS` | `1` | Fold known-unknowns into the digest. |
| `YANTRIKDB_HOOKS_FALLBACK` | `1` | Show recent records when the digest is empty. |
| `YANTRIKDB_HOOKS_RECENT` | `6` | How many records the fallback shows. |
| `YANTRIKDB_HOOKS_TRACK_SESSION` | `1` | Open/close a tracked YantrikDB session. |
| `YANTRIKDB_HOOKS_RECALL` | `1` | Recall on each prompt. |
| `YANTRIKDB_HOOKS_TOP_K` | `5` | Max hits injected per prompt. |
| `YANTRIKDB_HOOKS_MIN_SCORE` | `0.10` | Absolute score floor for an injected hit. |
| `YANTRIKDB_HOOKS_MIN_PROMPT_CHARS` | `24` | Prompts shorter than this are skipped. |
| `YANTRIKDB_HOOKS_RECORD_TURNS` | `1` | Mirror user turns into the working-memory ring. |
| `YANTRIKDB_HOOKS_RING_SIZE` | `20` | Ring buffer size. |
| `YANTRIKDB_HOOKS_CAPTURE` | `1` | Draft memories on compaction / session end. |
| `YANTRIKDB_HOOKS_CAPTURE_ROLES` | `user` | `user` or `all`. See below. |
| `YANTRIKDB_HOOKS_CAPTURE_TURNS` | `40` | Transcript tail length considered. |
| `YANTRIKDB_HOOKS_CAPTURE_CHARS` | `6000` | Cap on drafted text. |
| `YANTRIKDB_HOOKS_MAINTENANCE` | `1` | Run a hygiene cycle at session end. |
| `YANTRIKDB_HOOKS_TIMEOUT` | `25` | Self-deadline for the fast hooks (seconds). |
| `YANTRIKDB_HOOKS_CAPTURE_TIMEOUT` | `110` | Self-deadline for capture hooks. |
| `YANTRIKDB_HOOKS_DEBUG` | `0` | Log to stderr. |
| `YANTRIKDB_PYTHON` | — | Force the interpreter. |

Hook-only settings go in the top-level `env` block of `~/.claude/settings.json`.
Store settings (`YANTRIKDB_SERVER_URL`, `YANTRIKDB_TOKEN`, `YANTRIKDB_DB_PATH`,
`YANTRIKDB_EMBEDDER`) can stay in the MCP server's own `env` block, where the
hooks pick them up automatically:

```json
{
  "env": {
    "YANTRIKDB_HOOKS_NAMESPACE": "auto"
  }
}
```

If you set store settings in both places, the top-level `env` wins for the
hooks, so keep the values identical.

## Design notes

**Capture defaults to user turns only.** Assistant turns are the model's own
claims. Drafting them into long-term memory is how a memory store poisons
itself: a guess written this session comes back next session as a remembered
fact, indistinguishable from something you said. `roles=all` is available and
deliberately not the default.

**Capture is watermarked.** `PreCompact` and `SessionEnd` see overlapping
transcript tails; without a per-line watermark the second run re-drafts what
the first stored and the engine (correctly) files them as 100%-redundant pairs.
Lines already drafted in this session are skipped.

**Pin `YANTRIKDB_EMBEDDER`.** In `auto` mode `load_engine` picks the bundled
64-dim embedder for an empty DB and ONNX 384-dim for one with data — so a store
seeded on `bundled` fails to open once it has rows unless `yantrikdb-mcp[onnx]`
is installed. Set the variable explicitly and set it for both the hooks and the
MCP server.

**Engine version skew is handled.** The Rust engine and the HTTP backend take
different keyword arguments across builds and raise `TypeError` rather than
ignoring extras. Calls go through `ydb.flex()`, which drops a rejected keyword
and retries. Keywords known to exist on only one side (`include_gaps`) are
sent only to that side.

**Redaction is best-effort.** Private-key blocks, bearer tokens and JWTs,
credentials in URLs, well-known key prefixes (`sk-`, `ghp_`, `AKIA`, `xox`,
`ydb_`), `key=value` assignments for names like `password` or `api_key`, and
very long hex strings are masked. It is a conservative filter, not a DLP
engine; do not paste secrets into prompts and expect the memory store to be
the thing that saves you.

**Return-shape skew is handled.** `session_digest` returns JSON text on the
embedded engine and a dict over HTTP; `session_start` returns a bare id string
locally and a dict remotely; `list_memories` returns `{"memories": [...]}` on
one and a list on the other. All normalized.

**Hooks never block the session.** Exit is always 0, exit 2 is never used, and
every failure — missing package, locked DB, unreachable cluster, wedged
engine — produces empty stdout. Each hook also arms a SIGALRM self-deadline
inside Claude Code's own timeout. `stdout` is redirected to `stderr` for the
whole run so a stray print from a dependency cannot corrupt the hook protocol.

**Latency is real and worth knowing.** Each hook is a fresh process that opens
the engine, so `UserPromptSubmit` adds roughly 0.5-1s per prompt with the
bundled 64-dim embedder on an SSD, and more with ONNX 384-dim or a slow cluster
connection. The first open of an empty embedded store can take 10s or more. That cost is paid before the model starts, on every substantive
prompt. `YANTRIKDB_HOOKS_MIN_PROMPT_CHARS` keeps it off short turns; setting
`YANTRIKDB_HOOKS_RECALL=0` leaves only the session-boundary hooks, which cost
nothing per turn.

**Injected text is labeled as untrusted context.** Recalled memories arrive as
`additionalContext` and are explicitly framed as background rather than user
instructions, so a memory whose text reads like a command is not treated as one.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Unit tests need only the standard library. The end-to-end test drives all four
hooks through `run.sh` against a throwaway embedded store and skips itself when
no interpreter with `yantrikdb_mcp` can be found.

## Uninstall

```
/plugin uninstall yantrikdb-hooks@yantrikdb-claude
```

State lives in `${CLAUDE_PLUGIN_DATA}` (default `~/.yantrikdb/claude-hooks`) and
can be deleted freely. Memories are in YantrikDB and are untouched.

## License

MIT
