import unittest
from pathlib import Path

from _helpers import load, sandbox, write_transcript


class TranscriptTests(unittest.TestCase):
    def setUp(self):
        self.transcript = load("transcript")

    def test_meta_turns_are_dropped_and_real_prompts_kept(self):
        with sandbox() as root:
            p = root / "t.jsonl"
            write_transcript(p)
            rows = self.transcript.lines(str(p))
        joined = "\n".join(rows)
        self.assertIn("Postgres 16", joined)
        self.assertIn("indexer-prod-1", joined)
        self.assertIn("pasted html", joined, "a prompt starting with a normal tag is not meta")
        self.assertNotIn("command-name", joined)
        self.assertNotIn("stdout of a tool", joined)
        self.assertNotIn("automated reminder", joined, "system-reminder blocks are stripped")
        self.assertNotIn("system-reminder", joined)

    def test_roles_all_includes_assistant(self):
        with sandbox() as root:
            p = root / "t.jsonl"
            write_transcript(p)
            rows = self.transcript.lines(str(p), roles="all")
        self.assertTrue(any("migrate the schema" in r for r in rows))

    def test_inline_reminder_is_stripped_not_whole_turn(self):
        text = self.transcript.clean_text(
            "Keep this fact.\n<system-reminder>\ndrop this\n</system-reminder>\nAnd this one.")
        self.assertIn("Keep this fact", text)
        self.assertIn("And this one", text)
        self.assertNotIn("drop this", text)

    def test_compaction_and_harness_boilerplate_are_not_user_facts(self):
        import json
        records = [
            {"type": "user", "isCompactSummary": True, "message": {"role": "user", "content":
                "This session is being continued from a previous conversation that ran out of context."}},
            {"type": "user", "message": {"role": "user", "content":
                "Resume directly — do not acknowledge the summary, do not recap what was happening."}},
            {"type": "user", "message": {"role": "user", "content":
                "[ASYNC DELEGATION BATCH COMPLETE — deleg_024b6896] A background fan-out unit has finished."}},
            {"type": "user", "message": {"role": "user", "content":
                "Delegated task: Map the existing bridge repository APIs necessary for the coordinator."}},
            {"type": "user", "message": {"role": "user", "content":
                "Use the staging VM for the load test.\n<pasted_content id=\"ab12\">\n2026-10-03 ERROR a long log dump\n</pasted_content id=\"ab12\">"}},
        ]
        with sandbox() as root:
            p = root / "t.jsonl"
            p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
            joined = "\n".join(self.transcript.lines(str(p)))
        self.assertIn("staging VM", joined)
        for noise in ("continued from a previous", "Resume directly", "ASYNC DELEGATION",
                      "Delegated task", "long log dump"):
            self.assertNotIn(noise, joined)

    def test_headless_sdk_sessions_are_detected(self):
        import json
        with sandbox() as root:
            sdk, cli = root / "sdk.jsonl", root / "cli.jsonl"
            sdk.write_text(json.dumps({"type": "user", "entrypoint": "sdk-py", "message":
                                       {"role": "user", "content": "Review this change for security."}}) + "\n")
            cli.write_text(json.dumps({"type": "user", "entrypoint": "cli", "message":
                                       {"role": "user", "content": "Deploy it."}}) + "\n")
            self.assertTrue(self.transcript.is_headless(str(sdk)))
            self.assertFalse(self.transcript.is_headless(str(cli)))
            self.assertFalse(self.transcript.is_headless(None))

    def test_missing_file_is_empty(self):
        self.assertEqual(self.transcript.lines("/nonexistent/path.jsonl"), [])
        self.assertEqual(self.transcript.lines(None), [])


if __name__ == "__main__":
    unittest.main()
