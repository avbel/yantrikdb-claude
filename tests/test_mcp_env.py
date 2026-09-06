import os
import unittest
from pathlib import Path

from _helpers import env, load, sandbox, write_json

CLEAR = dict(YANTRIKDB_SERVER_URL=None, YANTRIKDB_TOKEN=None, YANTRIKDB_DB_PATH=None,
             YANTRIKDB_EMBEDDER=None, YANTRIKDB_HOOKS_ADOPT_MCP_ENV=None,
             YANTRIKDB_HOOKS_MCP_SERVER=None)


class AdoptMcpEnvTests(unittest.TestCase):
    def setUp(self):
        self.ydb = load("ydb")

    def test_reads_global_claude_json(self):
        with sandbox() as root, env(**CLEAR):
            write_json(Path(os.environ["HOME"]) / ".claude.json", {"mcpServers": {"yantrikdb": {
                "command": "yantrikdb-mcp",
                "env": {"YANTRIKDB_SERVER_URL": "http://10.0.0.5:7438", "YANTRIKDB_TOKEN": "ydb_x",
                        "UNRELATED": "no"}}}})
            self.ydb.adopt_mcp_env(str(root))
            self.assertEqual(os.environ.get("YANTRIKDB_SERVER_URL"), "http://10.0.0.5:7438")
            self.assertEqual(os.environ.get("YANTRIKDB_TOKEN"), "ydb_x")
            self.assertIsNone(os.environ.get("UNRELATED"))

    def test_explicit_process_env_wins(self):
        with sandbox() as root, env(**CLEAR), env(YANTRIKDB_SERVER_URL="http://explicit:1"):
            write_json(Path(os.environ["HOME"]) / ".claude.json", {"mcpServers": {"yantrikdb": {
                "env": {"YANTRIKDB_SERVER_URL": "http://from-mcp:1", "YANTRIKDB_TOKEN": "t"}}}})
            self.ydb.adopt_mcp_env(str(root))
            self.assertEqual(os.environ["YANTRIKDB_SERVER_URL"], "http://explicit:1")
            self.assertEqual(os.environ.get("YANTRIKDB_TOKEN"), "t", "missing keys still adopted")

    def test_project_mcp_json_beats_global(self):
        with sandbox() as root, env(**CLEAR):
            write_json(Path(os.environ["HOME"]) / ".claude.json", {"mcpServers": {"yantrikdb": {
                "env": {"YANTRIKDB_SERVER_URL": "http://global:1"}}}})
            write_json(root / ".mcp.json", {"mcpServers": {"yantrikdb": {
                "env": {"YANTRIKDB_DB_PATH": str(root / "proj.db")}}}})
            self.ydb.adopt_mcp_env(str(root))
            self.assertEqual(os.environ.get("YANTRIKDB_DB_PATH"), str(root / "proj.db"))
            self.assertIsNone(os.environ.get("YANTRIKDB_SERVER_URL"), "first match wins outright")

    def test_per_project_entry_in_claude_json(self):
        with sandbox() as root, env(**CLEAR):
            write_json(Path(os.environ["HOME"]) / ".claude.json", {
                "mcpServers": {"yantrikdb": {"env": {"YANTRIKDB_SERVER_URL": "http://global:1"}}},
                "projects": {str(root): {"mcpServers": {"yantrikdb": {
                    "env": {"YANTRIKDB_SERVER_URL": "http://project:1"}}}}}})
            self.ydb.adopt_mcp_env(str(root))
            self.assertEqual(os.environ.get("YANTRIKDB_SERVER_URL"), "http://project:1")

    def test_custom_server_name_and_var_expansion(self):
        with sandbox() as root, env(**CLEAR), env(YANTRIKDB_HOOKS_MCP_SERVER="memory", MY_TOK="abc"):
            write_json(Path(os.environ["HOME"]) / ".claude.json", {"mcpServers": {"memory": {
                "env": {"YANTRIKDB_TOKEN": "${MY_TOK}"}}}})
            self.ydb.adopt_mcp_env(str(root))
            self.assertEqual(os.environ.get("YANTRIKDB_TOKEN"), "abc")

    def test_opt_out(self):
        with sandbox() as root, env(**CLEAR), env(YANTRIKDB_HOOKS_ADOPT_MCP_ENV="0"):
            write_json(Path(os.environ["HOME"]) / ".claude.json", {"mcpServers": {"yantrikdb": {
                "env": {"YANTRIKDB_SERVER_URL": "http://global:1"}}}})
            self.ydb.adopt_mcp_env(str(root))
            self.assertIsNone(os.environ.get("YANTRIKDB_SERVER_URL"))

    def test_garbage_files_are_ignored(self):
        with sandbox() as root, env(**CLEAR):
            (Path(os.environ["HOME"]) / ".claude.json").write_text("{not json")
            self.ydb.adopt_mcp_env(str(root))  # must not raise
            self.assertIsNone(os.environ.get("YANTRIKDB_SERVER_URL"))


if __name__ == "__main__":
    unittest.main()
