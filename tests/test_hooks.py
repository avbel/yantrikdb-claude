import importlib.util
import sys
import unittest

from _helpers import HOOKS, env


def load_hook(name: str):
    if str(HOOKS) not in sys.path:
        sys.path.insert(0, str(HOOKS))
    spec = importlib.util.spec_from_file_location(name, HOOKS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def hit(rid, text, sim=0.75, **kw):
    row = {"rid": rid, "text": text, "score": 0.5, "source": "user", "namespace": "default",
           "metadata": {}, "why_retrieved": [f"semantically similar ({sim})"]}
    row.update(kw)
    return row


DEFAULTS = dict(YANTRIKDB_HOOKS_TOP_K=None, YANTRIKDB_HOOKS_MIN_SIMILARITY=None, YANTRIKDB_HOOKS_CAPTURE=None,
                YANTRIKDB_HOOKS_RECALL_CAPTURED=None, YANTRIKDB_HOOKS_EXCLUDE_NAMESPACES=None,
                YANTRIKDB_HOOKS_MIN_SCORE=None, YANTRIKDB_HOOKS_DIGEST_MAINTENANCE=None,
                YANTRIKDB_HOOKS_DIGEST_DECISIONS=None)


class PromptRecallTests(unittest.TestCase):
    def test_noise_is_dropped_and_injection_capped(self):
        hook = load_hook("user_prompt_submit")
        hits = [
            hit("auto", "commit and push", source="session_auto_capture"),
            hit("merge", "yes. push | review with fable", metadata={"consolidated_from": ["a", "b"]}),
            hit("hermes", "Sorry, something went wrong there.", namespace="hermes:hermes:default"),
            hit("graph", "Docker Swarm service DNS names contain an underscore.", sim=None,
                why_retrieved=["keyword_match"]),
            hit("weak", "Ghost CMS logger crashes on array env vars.", sim=0.41),
            hit("seen", "Postgres 16 is the indexer database."),
            hit("a", "auction-bot code style: no single-use helpers, no comments."),
            hit("b", "auction-bot scenario hierarchy lives in auction_bot/scenario.py."),
            hit("c", "auction-bot rules were saved to the project skill on 2026-09-15."),
            hit("d", "auction-bot LiveAuctioneers identity comes from /auth/spauser."),
        ]
        with env(**{**DEFAULTS, "YANTRIKDB_HOOKS_EXCLUDE_NAMESPACES": "hermes:"}):
            lines, fresh = hook.select_lines(hits, ["seen"])
        self.assertEqual(fresh, ["a", "b", "c"])
        self.assertEqual(len(lines), 3)
        self.assertTrue(all(line.startswith("- auction-bot") for line in lines))

    def test_other_agent_namespaces_are_included_by_default(self):
        hook = load_hook("user_prompt_submit")
        with env(**DEFAULTS):
            _, fresh = hook.select_lines([hit("hermes", "NAS runs parent-control as a systemd unit.",
                                              namespace="hermes:hermes:default")], [])
        self.assertEqual(fresh, ["hermes"])

    def test_captured_memories_come_back_when_capture_is_on(self):
        hook = load_hook("user_prompt_submit")
        with env(**{**DEFAULTS, "YANTRIKDB_HOOKS_CAPTURE": "1"}):
            _, fresh = hook.select_lines([hit("auto", "We use Postgres 16.", source="session_auto_capture")], [])
        self.assertEqual(fresh, ["auto"])


class DigestTests(unittest.TestCase):
    DIGEST = {
        "top_decisions": [
            {"rid": "old", "snippet": "BOTS MIGRATION PAUSED", "superseded_by": "new", "namespace": "default"},
            {"rid": "chat", "snippet": "hey. unlock the laptop", "namespace": "hermes:hermes:default"},
            *[{"rid": f"d{i}", "snippet": f"decision {i}", "namespace": "default"} for i in range(8)],
        ],
        "open_conflicts": [{"conflict_type": "preference", "summary": "a vs b"}],
        "pending_triggers": [{"reason": "128 open conflicts need attention"}],
        "knowledge_gaps": [{"query": "just logged in in gcloud cli"}],
    }

    def test_default_digest_keeps_only_live_decisions(self):
        hook = load_hook("session_start")
        with env(**{**DEFAULTS, "YANTRIKDB_HOOKS_EXCLUDE_NAMESPACES": "hermes:"}):
            text = hook.render(self.DIGEST)
        self.assertIn("decision 0", text)
        self.assertIn("decision 4", text)
        self.assertNotIn("decision 5", text, "capped at 5 decisions")
        for noise in ("BOTS MIGRATION PAUSED", "unlock the laptop", "a vs b", "open conflicts need attention",
                      "gcloud cli"):
            self.assertNotIn(noise, text)

    def test_maintenance_sections_are_opt_in(self):
        hook = load_hook("session_start")
        with env(**{**DEFAULTS, "YANTRIKDB_HOOKS_DIGEST_MAINTENANCE": "1"}):
            text = hook.render(self.DIGEST)
        self.assertIn("open conflicts need attention", text)
        self.assertIn("gcloud cli", text)

    def test_recent_fallback_skips_noise(self):
        hook = load_hook("session_start")
        rows = [{"text": "commit and push", "source": "session_auto_capture"},
                {"text": "Deploy target is indexer-prod-1.", "source": "user"}]
        with env(**DEFAULTS):
            text = hook.render_recent(rows, "most recent records", "default")
        self.assertIn("indexer-prod-1", text)
        self.assertNotIn("commit and push", text)


if __name__ == "__main__":
    unittest.main()
