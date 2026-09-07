import importlib.util
import sys
import unittest
from pathlib import Path

from _helpers import HOOKS, env, load


def load_hook(name: str):
    """Import a top-level hook module (they live outside lib/)."""
    if str(HOOKS) not in sys.path:
        sys.path.insert(0, str(HOOKS))
    spec = importlib.util.spec_from_file_location(name, HOOKS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Response:
    def __init__(self, text: str):
        self.text = text


class _Conflict(Exception):
    def __init__(self):
        super().__init__("500 Server Error: Internal Server Error for url: /v1/sessions")
        self.response = _Response(
            '{"error":{"code":"generic","message":'
            '"session conflict: active session already exists for '
            'namespace=default, client_id="}}'
        )


class ClientIdTests(unittest.TestCase):
    def setUp(self):
        self.ydb = load("ydb")

    def test_derives_from_claude_session_id(self):
        with env(YANTRIKDB_HOOKS_CLIENT_ID=None):
            cid = self.ydb.client_id_for("1d69429d-F41F-4a35-b0f9-350e9d141ed5")
            self.assertEqual(cid, "claude-code-1d69429d-f41f-4a35-b0f9-350e9d141ed5")

    def test_distinct_sessions_get_distinct_ids(self):
        with env(YANTRIKDB_HOOKS_CLIENT_ID=None):
            self.assertNotEqual(self.ydb.client_id_for("aaa"), self.ydb.client_id_for("bbb"))

    def test_never_empty_without_a_session_id(self):
        with env(YANTRIKDB_HOOKS_CLIENT_ID=None):
            for missing in ("", None, "///"):
                self.assertEqual(self.ydb.client_id_for(missing), "claude-code")

    def test_explicit_override_wins(self):
        with env(YANTRIKDB_HOOKS_CLIENT_ID="laptop"):
            self.assertEqual(self.ydb.client_id_for("whatever"), "laptop")


class StartTrackingTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_hook("session_start")

    def test_sends_namespace_and_client_id(self):
        seen = []

        class DB:
            def session_start(self, **kw):
                seen.append(kw)
                return {"session_id": "rid-1"}

        rid = self.mod.start_tracking(DB(), "proj", "claude-code-abc")
        self.assertEqual(rid, "rid-1")
        self.assertEqual(seen, [{"namespace": "proj", "client_id": "claude-code-abc"}])

    def test_conflict_retries_once_under_a_salted_id(self):
        seen = []

        class DB:
            def session_start(self, **kw):
                seen.append(kw["client_id"])
                if len(seen) == 1:
                    raise _Conflict()
                return {"session_id": "rid-2"}

        rid = self.mod.start_tracking(DB(), "proj", "claude-code-abc")
        self.assertEqual(rid, "rid-2")
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0], "claude-code-abc")
        self.assertTrue(seen[1].startswith("claude-code-abc-"), seen[1])

    def test_other_failures_are_not_retried(self):
        seen = []

        class DB:
            def session_start(self, **kw):
                seen.append(kw)
                raise RuntimeError("connection refused")

        with self.assertRaises(RuntimeError):
            self.mod.start_tracking(DB(), "proj", "claude-code-abc")
        self.assertEqual(len(seen), 1, "a non-conflict failure must not burn a second slot")

    def test_client_id_is_dropped_on_an_engine_that_rejects_it(self):
        """flex() must degrade to the old call shape on an older embedded engine."""
        seen = []

        class DB:
            def session_start(self, **kw):
                seen.append(kw)
                if "client_id" in kw:
                    raise TypeError("session_start() got an unexpected keyword argument 'client_id'")
                return "rid-3"

        self.assertEqual(self.mod.start_tracking(DB(), "proj", "cid"), "rid-3")
        self.assertEqual(seen[-1], {"namespace": "proj"})


if __name__ == "__main__":
    unittest.main()
