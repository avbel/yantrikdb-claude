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

    def test_missing_file_is_empty(self):
        self.assertEqual(self.transcript.lines("/nonexistent/path.jsonl"), [])
        self.assertEqual(self.transcript.lines(None), [])


if __name__ == "__main__":
    unittest.main()
