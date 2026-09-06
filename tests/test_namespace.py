import subprocess
import unittest
from pathlib import Path

from _helpers import env, load, sandbox


class NamespaceTests(unittest.TestCase):
    def setUp(self):
        self.ydb = load("ydb")

    def test_explicit_namespace_passthrough(self):
        with env(YANTRIKDB_HOOKS_NAMESPACE="team-x"):
            self.assertEqual(self.ydb.namespace_for("/any/where"), "team-x")

    def test_default_when_unset(self):
        with env(YANTRIKDB_HOOKS_NAMESPACE=None):
            self.assertEqual(self.ydb.namespace_for("/any/where"), "default")

    def test_auto_uses_git_remote_repo_name(self):
        with sandbox() as root, env(YANTRIKDB_HOOKS_NAMESPACE="auto"):
            proj = root / "some-dir"
            proj.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
            subprocess.run(["git", "remote", "add", "origin",
                            "git@github.com:avbel/My_Repo.git"], cwd=proj, check=True)
            self.assertEqual(self.ydb.namespace_for(str(proj)), "my_repo")

    def test_auto_without_git_uses_basename_plus_path_hash(self):
        with sandbox() as root, env(YANTRIKDB_HOOKS_NAMESPACE="auto"):
            a = root / "api"
            b = root / "other" / "api"
            a.mkdir()
            b.mkdir(parents=True)
            na, nb = self.ydb.namespace_for(str(a)), self.ydb.namespace_for(str(b))
            self.assertTrue(na.startswith("api-"), na)
            self.assertTrue(nb.startswith("api-"), nb)
            self.assertNotEqual(na, nb, "same basename in different places must not collide")
            self.assertEqual(na, self.ydb.namespace_for(str(a)), "must be deterministic")

    def test_auto_is_stable_when_git_has_no_remote(self):
        with sandbox() as root, env(YANTRIKDB_HOOKS_NAMESPACE="auto"):
            proj = root / "solo"
            proj.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
            ns = self.ydb.namespace_for(str(proj))
            self.assertTrue(ns.startswith("solo-"), ns)


if __name__ == "__main__":
    unittest.main()
