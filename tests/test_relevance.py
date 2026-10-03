import unittest

from _helpers import env, load

# Real merges of curated facts measured 825-4757 chars; this one is ~900.
CURATED_MERGE = " | ".join([
    "Dokploy deploys composeType 'stack' with docker stack deploy --prune, and Swarm does nothing when the "
    "service spec is byte-identical, so a one-shot migrate service must change its spec to run again. The "
    "redeploy reports success while the old completed task is simply kept, which looks like a silent no-op.",
    "Bump the image digest or a MIGRATE_RUN_ID env var to force the re-run; verified on 2026-09-04 against "
    "the ripstation inventory migrate service, which otherwise silently kept the previous completed task and "
    "never applied the new schema migration until the run id was changed and the stack deployed again.",
    "The same applies to every stack deployed by Dokploy on the byzantion swarm, including the bots project, "
    "the auth project and the defi indexer cursors; check docker service ps on the manager to confirm a new "
    "task actually started before assuming that a migration or a configuration change has been applied.",
])


def hit(**kw):
    row = {"rid": kw.pop("rid", "r1"), "text": "Postgres 16 is the indexer database.", "score": 0.5,
           "source": "user", "namespace": "default", "metadata": {},
           "why_retrieved": ["semantically similar (0.72)", "recent"]}
    row.update(kw)
    return row


class SimilarityTests(unittest.TestCase):
    def setUp(self):
        self.rel = load("relevance")

    def test_parses_similarity_from_why_retrieved(self):
        self.assertAlmostEqual(self.rel.semantic_similarity(["recent", "semantically similar (0.77)"]), 0.77)
        self.assertAlmostEqual(self.rel.semantic_similarity("semantically similar (0.5)"), 0.5)

    def test_missing_semantic_lane_is_none(self):
        self.assertIsNone(self.rel.semantic_similarity(["keyword_match", "graph-connected via FIX"]))
        self.assertIsNone(self.rel.semantic_similarity(None))


class NoiseTests(unittest.TestCase):
    def setUp(self):
        self.rel = load("relevance")

    def test_auto_captured_prompt_is_noise_unless_captures_are_wanted(self):
        row = hit(source="session_auto_capture", metadata={"kind": "session_auto_capture"})
        self.assertEqual(self.rel.noise_reason(row, include_captured=False), "auto-capture")
        self.assertIsNone(self.rel.noise_reason(row, include_captured=True))

    def test_consolidated_prompts_are_noise_even_when_relabeled_user(self):
        row = hit(text="commit and push. | review with fable, fix with opus. commit and push.",
                  metadata={"consolidated_from": ["a", "b"]})
        self.assertEqual(self.rel.noise_reason(row, include_captured=False), "prompt-merge")
        self.assertIsNone(self.rel.noise_reason(row, include_captured=True))

    def test_consolidated_curated_facts_are_kept(self):
        row = hit(text=CURATED_MERGE, metadata={"consolidated_from": ["a", "b", "c"]})
        self.assertIsNone(self.rel.noise_reason(row, include_captured=False))

    def test_excluded_namespace_prefixes(self):
        row = hit(namespace="hermes:hermes:default")
        self.assertEqual(self.rel.noise_reason(row, excluded=("hermes:",)), "namespace")
        self.assertIsNone(self.rel.noise_reason(hit(namespace="auction-bot"), excluded=("hermes:",)))
        self.assertIsNone(self.rel.noise_reason(row, excluded=()))

    def test_metadata_as_json_text_is_understood(self):
        row = hit(text="yes. push", metadata='{"consolidated_from": ["a", "b"]}')
        self.assertEqual(self.rel.noise_reason(row, include_captured=False), "prompt-merge")


class FilterTests(unittest.TestCase):
    def setUp(self):
        self.rel = load("relevance")

    def test_similarity_floor_requires_a_semantic_match(self):
        rows = [hit(rid="weak", why_retrieved=["semantically similar (0.41)"]),
                hit(rid="graph-only", why_retrieved=["keyword_match", "graph-connected via API"]),
                hit(rid="strong", why_retrieved=["semantically similar (0.81)"])]
        kept = self.rel.filter_hits(rows, min_similarity=0.6)
        self.assertEqual([r["rid"] for r in kept], ["strong"])

    def test_zero_floor_disables_the_similarity_gate(self):
        rows = [hit(rid="graph-only", why_retrieved=["keyword_match"])]
        self.assertEqual(len(self.rel.filter_hits(rows, min_similarity=0)), 1)

    def test_limit_and_order_are_preserved(self):
        rows = [hit(rid=f"r{i}") for i in range(6)]
        kept = self.rel.filter_hits(rows, min_similarity=0.6, limit=3)
        self.assertEqual([r["rid"] for r in kept], ["r0", "r1", "r2"])

    def test_objects_with_attributes_are_supported(self):
        class Row:
            def __init__(self, **kw):
                self.__dict__.update(kw)
        kept = self.rel.filter_hits([Row(**hit())], min_similarity=0.6)
        self.assertEqual(len(kept), 1)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.rel = load("relevance")

    def test_defaults(self):
        with env(YANTRIKDB_HOOKS_EXCLUDE_NAMESPACES=None, YANTRIKDB_HOOKS_MIN_SIMILARITY=None,
                 YANTRIKDB_HOOKS_RECALL_CAPTURED=None, YANTRIKDB_HOOKS_CAPTURE=None):
            cfg = self.rel.config()
        self.assertEqual(cfg["excluded"], (), "no namespace is excluded unless configured")
        self.assertAlmostEqual(cfg["min_similarity"], 0.5)
        self.assertFalse(cfg["include_captured"])

    def test_recall_of_captures_follows_capture_flag(self):
        with env(YANTRIKDB_HOOKS_CAPTURE="1", YANTRIKDB_HOOKS_RECALL_CAPTURED=None):
            self.assertTrue(self.rel.config()["include_captured"])
        with env(YANTRIKDB_HOOKS_CAPTURE="1", YANTRIKDB_HOOKS_RECALL_CAPTURED="0"):
            self.assertFalse(self.rel.config()["include_captured"])

    def test_exclusion_list_is_parsed(self):
        with env(YANTRIKDB_HOOKS_EXCLUDE_NAMESPACES="hermes:, other-agent:"):
            self.assertEqual(self.rel.config()["excluded"], ("hermes:", "other-agent:"))


if __name__ == "__main__":
    unittest.main()
