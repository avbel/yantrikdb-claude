import os
import time
import unittest

from _helpers import env, load, sandbox


class StateTests(unittest.TestCase):
    def setUp(self):
        self.ydb = load("ydb")

    def test_sweep_removes_only_old_session_files(self):
        with sandbox(), env(YANTRIKDB_HOOKS_STATE_MAX_AGE_DAYS="7"):
            d = self.ydb.state_dir()
            old = d / "old-session.json"
            fresh = d / "fresh-session.json"
            marker = self.ydb.unreachable_marker_path()
            interp = d / "interpreter"
            for p in (old, fresh, marker, interp):
                p.write_text("{}")
            stale = time.time() - 30 * 86400
            os.utime(old, (stale, stale))
            os.utime(marker, (stale, stale))
            os.utime(interp, (stale, stale))
            self.ydb.sweep_state()
            self.assertFalse(old.exists())
            self.assertTrue(fresh.exists())
            self.assertTrue(marker.exists(), "the marker has its own TTL")
            self.assertTrue(interp.exists(), "the interpreter cache is never swept")

    def test_merge_rids_keeps_order_and_drops_oldest(self):
        merged = self.ydb.merge_rids(["a", "b", "c"], ["c", "d"], cap=4)
        self.assertEqual(merged, ["a", "b", "c", "d"])
        merged = self.ydb.merge_rids(["a", "b", "c"], ["d", "e"], cap=4)
        self.assertEqual(merged, ["b", "c", "d", "e"], "oldest entries are the ones trimmed")

    def test_state_roundtrip(self):
        with sandbox():
            self.ydb.write_state("sess/1", a=1)
            self.ydb.write_state("sess/1", b=2)
            self.assertEqual(self.ydb.read_state("sess/1"), {"a": 1, "b": 2})


if __name__ == "__main__":
    unittest.main()
