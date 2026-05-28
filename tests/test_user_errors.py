import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from core.user_errors import UserErrorDomain, classify_user_error


class UserErrorsCompatTests(unittest.TestCase):
    def test_free_tier_maps_to_api_remote(self):
        msg = "API 403: The free tier of the model has been exhausted."
        ctx = classify_user_error(msg)
        self.assertEqual(ctx.domain, UserErrorDomain.API_REMOTE)

    def test_401_invalid_key_maps_to_credentials(self):
        ctx = classify_user_error("API 401: Invalid API Key provided")
        self.assertEqual(ctx.domain, UserErrorDomain.API_CREDENTIALS)
        self.assertIn("Invalid API Key", ctx.message)


if __name__ == "__main__":
    unittest.main()
