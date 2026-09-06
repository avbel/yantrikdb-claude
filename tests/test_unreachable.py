import json
import time
import unittest

from _helpers import env, load, sandbox

try:
    import yantrikdb_mcp  # noqa: F401
    HAVE_PKG = True
except Exception:
    HAVE_PKG = False


class UnreachableClusterTests(unittest.TestCase):
    def setUp(self):
        self.ydb = load("ydb")

    def test_marker_roundtrip(self):
        with sandbox(), env(YANTRIKDB_HOOKS_UNREACHABLE_TTL="60"):
            url = "http://10.255.255.1:7438"
            self.assertFalse(self.ydb.cluster_marked_down(url))
            self.ydb.mark_cluster_down(url)
            self.assertTrue(self.ydb.cluster_marked_down(url))
            self.assertFalse(self.ydb.cluster_marked_down("http://other:1"), "marker is per URL")

    def test_marker_expires(self):
        with sandbox(), env(YANTRIKDB_HOOKS_UNREACHABLE_TTL="1"):
            url = "http://10.255.255.1:7438"
            self.ydb.mark_cluster_down(url)
            self.assertTrue(self.ydb.cluster_marked_down(url))
            time.sleep(1.2)
            self.assertFalse(self.ydb.cluster_marked_down(url))

    def test_marker_survives_garbage(self):
        with sandbox():
            self.ydb.unreachable_marker_path().write_text("{broken")
            self.assertFalse(self.ydb.cluster_marked_down("http://x:1"))

    @unittest.skipUnless(HAVE_PKG, "yantrikdb_mcp not importable under this interpreter")
    def test_open_db_fails_fast_and_marks_down(self):
        with sandbox(), env(YANTRIKDB_SERVER_URL="http://10.255.255.1:7438", YANTRIKDB_TOKEN="x",
                            YANTRIKDB_HOOKS_HTTP_TIMEOUT="1", YANTRIKDB_HOOKS_ADOPT_MCP_ENV="0"):
            t0 = time.monotonic()
            self.assertIsNone(self.ydb.open_db())
            first = time.monotonic() - t0
            self.assertLess(first, 4.0, f"probe must be bounded, took {first:.1f}s")
            self.assertTrue(self.ydb.cluster_marked_down("http://10.255.255.1:7438"))
            t0 = time.monotonic()
            self.assertIsNone(self.ydb.open_db())
            self.assertLess(time.monotonic() - t0, 0.5, "second open must short-circuit on the marker")


if __name__ == "__main__":
    unittest.main()
