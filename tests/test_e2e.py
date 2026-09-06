"""End-to-end: run the four hooks through run.sh against a throwaway embedded
store. Skipped when run.sh cannot find an interpreter with yantrikdb_mcp."""

import json
import os
import subprocess
import unittest
from pathlib import Path

from _helpers import RUN_SH, env, sandbox, write_transcript

CWD = str(Path(__file__).resolve().parent.parent)


def _probe_interpreter() -> bool:
    r = subprocess.run(["bash", "-c", f'"{RUN_SH}" user_prompt_submit.py <<<"{{}}"; echo ok'],
                       capture_output=True, text=True)
    # run.sh is a silent no-op without an interpreter; detect via the cache it writes
    return r.returncode == 0


def _hook(script: str, payload: dict, *args: str, extra_env: dict | None = None):
    e = dict(os.environ)
    e.update(extra_env or {})
    r = subprocess.run([str(RUN_SH), script, *args], input=json.dumps(payload),
                       capture_output=True, text=True, env=e, timeout=180)
    out = r.stdout.strip()
    return (json.loads(out) if out else None), r.stderr


class HooksEndToEnd(unittest.TestCase):
    def test_full_session_lifecycle(self):
        with sandbox() as root, env(
            YANTRIKDB_SERVER_URL=None, YANTRIKDB_TOKEN=None, YANTRIKDB_HOOKS_ADOPT_MCP_ENV="0",
            YANTRIKDB_DB_PATH=str(root / "mem.db"), YANTRIKDB_EMBEDDER="bundled",
            YANTRIKDB_HOOKS_NAMESPACE="auto", YANTRIKDB_HOOKS_DEBUG="1",
        ):
            data = Path(os.environ["CLAUDE_PLUGIN_DATA"])
            t = root / "t.jsonl"
            write_transcript(t)
            base = {"session_id": "sess-1", "cwd": CWD, "transcript_path": str(t)}

            out, err = _hook("session_start.py", {**base, "source": "startup"})
            if "engine unavailable" in err or (out is None and not (data / "interpreter").exists()
                                                and not os.environ.get("YANTRIKDB_PYTHON")):
                self.skipTest("no interpreter with yantrikdb_mcp available: " + err[-300:])
            self.assertIsNone(out, "empty store: nothing to inject")
            self.assertTrue((data / "sess-1.json").exists(), "tracked session state written")

            out, _ = _hook("user_prompt_submit.py", {**base, "prompt": "hi"})
            self.assertIsNone(out, "short prompt skipped")
            out, _ = _hook("user_prompt_submit.py", {**base, "prompt": "/model something long enough"})
            self.assertIsNone(out, "slash command skipped")

            out, err = _hook("capture.py", {**base, "trigger": "auto"}, "PreCompact")
            self.assertIsNone(out)
            self.assertIn("drafted", err)
            self.assertNotIn("automated reminder", err)

            q = {**base, "prompt": "Which database did we pick for the indexer and where does it deploy?"}
            out, err = _hook("user_prompt_submit.py", q)
            self.assertIsNotNone(out, err)
            ctx = out["hookSpecificOutput"]["additionalContext"]
            self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
            self.assertTrue("Postgres" in ctx or "indexer-prod-1" in ctx, ctx)

            out, _ = _hook("user_prompt_submit.py", q)
            self.assertIsNone(out, "same hits are not injected twice in one session")

            out, err = _hook("capture.py", {**base, "reason": "exit"}, "SessionEnd")
            self.assertIsNone(out)
            self.assertIn("skipped: 0 new chars", err, "watermark prevents re-drafting")
            self.assertFalse((data / "sess-1.json").exists(), "state removed at session end")

            out, err = _hook("session_start.py", {"session_id": "sess-2", "cwd": CWD, "source": "startup"})
            self.assertIsNotNone(out, err)
            ctx = out["hookSpecificOutput"]["additionalContext"]
            facts = ("Postgres 16", "indexer-prod-1", "descriptive names")
            self.assertTrue(any(f in ctx for f in facts), ctx)
            self.assertIn("yantrikdb-hooks plugin", ctx, "injected text must be labeled")
            self.assertNotIn("dropping unsupported kwarg", err,
                             "digest must be called with kwargs the embedded engine accepts")

            for source in ("compact", "fork"):
                out, _ = _hook("session_start.py", {"session_id": "s3", "cwd": CWD, "source": source})
                self.assertIsNone(out, f"source={source} must not re-inject")

    def test_unreachable_cluster_is_fast(self):
        import time
        with sandbox(), env(YANTRIKDB_SERVER_URL="http://10.255.255.1:7438", YANTRIKDB_TOKEN="x",
                            YANTRIKDB_HOOKS_ADOPT_MCP_ENV="0", YANTRIKDB_HOOKS_HTTP_TIMEOUT="1",
                            YANTRIKDB_HOOKS_DEBUG="1"):
            t0 = time.monotonic()
            out, err = _hook("user_prompt_submit.py",
                             {"session_id": "s", "cwd": CWD, "prompt": "a prompt that is long enough to recall"})
            took = time.monotonic() - t0
            if "engine unavailable" not in err and "unreachable" not in err and out is None and took < 0.5:
                self.skipTest("no interpreter with yantrikdb_mcp available")
            self.assertIsNone(out)
            self.assertLess(took, 5.0, f"hook must not stall on a dead cluster, took {took:.1f}s")


if __name__ == "__main__":
    unittest.main()
