import unittest

from _helpers import load


class RedactTests(unittest.TestCase):
    def setUp(self):
        self.redact = load("redact")

    def test_known_token_shapes_are_masked(self):
        cases = [
            "token is ydb_0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef ok",
            "key sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMN",
            "gh token ghp_abcdefghijklmnopqrstuvwxyz0123456789",
            "aws AKIAIOSFODNN7EXAMPLE here",
            "slack xoxb-FAKETESTTOKEN-notarealsecretvalue",
            "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnopqrstuvwxyz",
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----",
            "postgres://user:s3cretpassword@db.example.com:5432/app",
        ]
        for text in cases:
            out = self.redact.redact(text)
            self.assertIn("[redacted", out, text)
            for secret in ("0123456789abcdef0123", "abcdefghijklmnopqrstuvwxyz0123456789", "IOSFODNN7",
                           "s3cretpassword", "MIIEow", "eyJzdWIi"):
                self.assertNotIn(secret, out, text)

    def test_oauth_codes_and_tokens_are_masked(self):
        cases = {
            "http://localhost:1/?state=BKZmldtWuKQARBVuymiy&iss=https://accounts.google.com&code=4/0AXlqoi6mfZ8khB6jehDKnWG&scope=email":
                ("BKZmldtWuKQARBVuymiy", "0AXlqoi6mfZ8khB6jehDKnWG"),
            "paste the code 4/0AVGzR1Bc3dEfGhIjKlMnOpQrStUvWxYz here": ("0AVGzR1Bc3dEfGhIjKlMnOpQrStUvWxYz",),
            "access_token=ya29.a0AfH6SMBx3yZ9QWERTYuiopasdfgh refresh_token: 1//0gLmNoPqRsTuVwXyZ12345":
                ("ya29.a0AfH6SMBx3yZ9QWERTYuiopasdfgh", "1//0gLmNoPqRsTuVwXyZ12345"),
            "client_secret=GOCSPX-abcdefghijklmnop": ("GOCSPX-abcdefghijklmnop",),
        }
        for text, secrets in cases.items():
            out = self.redact.redact(text)
            for secret in secrets:
                self.assertNotIn(secret, out, text)
        self.assertIn("accounts.google.com", self.redact.redact(next(iter(cases))))

    def test_assignments_are_masked_but_key_names_kept(self):
        out = self.redact.redact("set API_KEY=abcdefgh12345678 and password: hunter2hunter2")
        self.assertIn("API_KEY", out)
        self.assertIn("password", out)
        self.assertNotIn("abcdefgh12345678", out)
        self.assertNotIn("hunter2hunter2", out)

    def test_ordinary_text_and_git_shas_survive(self):
        text = ("We decided to use Postgres 16. Commit 3f786850e387550fdab836ed7e6dc881de23001b "
                "fixed it; the deploy target is indexer-prod-1 in europe-west4.")
        self.assertEqual(self.redact.redact(text), text)

    def test_disabled_by_env(self):
        from _helpers import env
        with env(YANTRIKDB_HOOKS_REDACT="0"):
            self.assertEqual(self.redact.redact("ghp_abcdefghijklmnopqrstuvwxyz0123456789"),
                             "ghp_abcdefghijklmnopqrstuvwxyz0123456789")

    def test_empty_and_none(self):
        self.assertEqual(self.redact.redact(""), "")
        self.assertEqual(self.redact.redact(None), "")


if __name__ == "__main__":
    unittest.main()
