import unittest

from _helpers import env, load

FULL_ROW = {"rid": "r1", "text": "status: 403 cloudflare challenge", "score": 0.5, "source": "session_auto_capture",
            "namespace": "default", "metadata": {"kind": "session_auto_capture"},
            "why_retrieved": ["semantically similar (0.82)"]}


class HttpBackend:
    """Stands in for yantrikdb_mcp's client, which drops provenance fields."""

    def recall(self, **_kw):
        return [{"rid": "r1", "text": FULL_ROW["text"], "score": 0.5,
                 "why_retrieved": FULL_ROW["why_retrieved"]}]


class Embedded:
    def recall(self, *, query, top_k, namespace=None, expand_entities=True):
        return [dict(FULL_ROW, query=query, top_k=top_k)]


class RecallRowsTests(unittest.TestCase):
    def setUp(self):
        self.ydb = load("ydb")

    def test_http_rows_keep_provenance_for_the_relevance_gate(self):
        sent = {}

        def fake_post(path, body):
            sent.update(path=path, body=body)
            return {"results": [FULL_ROW]}

        original = self.ydb._http_post
        self.ydb._http_post = fake_post
        try:
            rows = self.ydb.recall_rows(HttpBackend(), query="cloudflare 403", top_k=10, namespace=None)
        finally:
            self.ydb._http_post = original
        self.assertEqual(sent["path"], "/v1/recall")
        self.assertEqual(sent["body"]["top_k"], 10)
        self.assertNotIn("namespace", sent["body"])
        self.assertEqual(rows[0]["source"], "session_auto_capture")
        self.assertEqual(rows[0]["metadata"], {"kind": "session_auto_capture"})

    def test_embedded_rows_come_from_the_engine(self):
        rows = self.ydb.recall_rows(Embedded(), query="q", top_k=4, namespace="proj", min_score_ratio=0.55)
        self.assertEqual(rows[0]["top_k"], 4, "unsupported kwargs are dropped, the rest forwarded")
        self.assertEqual(rows[0]["source"], "session_auto_capture")

    def test_http_post_uses_env_coordinates(self):
        calls = []

        class Response:
            ok = True

            def json(self):
                return {"results": []}

            def raise_for_status(self):
                pass

        class Requests:
            @staticmethod
            def post(url, json=None, headers=None, timeout=None):
                calls.append((url, headers, timeout))
                return Response()

        with env(YANTRIKDB_SERVER_URL="http://a:7438/,http://b:7438", YANTRIKDB_TOKEN="tok",
                 YANTRIKDB_HOOKS_HTTP_TIMEOUT="4"):
            self.ydb._http_post("/v1/recall", {"query": "q"}, requests_module=Requests)
        self.assertEqual(calls, [("http://a:7438/v1/recall", {"Authorization": "Bearer tok"}, 4)])


if __name__ == "__main__":
    unittest.main()
