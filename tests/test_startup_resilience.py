import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from core.polisher import TextPolisher
from core.user_errors import UserErrorDomain, classify_user_error


class StartupResilienceTests(unittest.TestCase):
    def test_polisher_allows_missing_api_key_at_startup(self):
        polisher = TextPolisher(api_key="")

        ok, text = polisher.polish("原文")

        self.assertIsNone(polisher._client)
        self.assertFalse(ok)
        self.assertEqual(text, "原文")

    def test_polisher_allows_whitespace_api_key_at_startup(self):
        polisher = TextPolisher(api_key="   ")

        ok, text = polisher.polish("原文")

        self.assertIsNone(polisher._client)
        self.assertFalse(ok)
        self.assertEqual(text, "原文")

    def test_missing_credentials_is_classified_as_api_credentials(self):
        ctx = classify_user_error(
            "Missing credentials. Please pass an api_key or set OPENAI_API_KEY."
        )

        self.assertEqual(ctx.domain, UserErrorDomain.API_CREDENTIALS)


if __name__ == "__main__":
    unittest.main()
