import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from core.asr import DashScopeASR  # noqa: E402
from core.polisher import TextPolisher  # noqa: E402


class BusinessNetworkClientTests(unittest.TestCase):
    def test_polisher_openai_client_ignores_environment_proxies(self):
        # openai/httpx are imported lazily inside _ensure_client, so patch the
        # real modules rather than core.polisher attributes.
        with patch("httpx.Client") as http_client_cls:
            with patch("openai.OpenAI") as openai_cls:
                polisher = TextPolisher(api_key="test-key")
                polisher._ensure_client()

        http_client_cls.assert_called_once_with(trust_env=False)
        openai_cls.assert_called_once_with(
            api_key="test-key",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            http_client=http_client_cls.return_value,
        )

    def test_polisher_builds_client_on_first_polish(self):
        with patch("httpx.Client"):
            with patch("openai.OpenAI") as openai_cls:
                polisher = TextPolisher(api_key="test-key")
                openai_cls.assert_not_called()

                client = openai_cls.return_value
                message = SimpleNamespace(content="```text\n润色结果\n```")
                client.chat.completions.create.return_value = SimpleNamespace(
                    choices=[SimpleNamespace(message=message)]
                )
                ok, text = polisher.polish("原文")

        self.assertTrue(ok)
        self.assertEqual(text, "润色结果")
        openai_cls.assert_called_once()

    def test_asr_call_runs_inside_direct_business_network_context(self):
        entered = []

        @contextmanager
        def direct_context():
            entered.append(True)
            yield

        response = SimpleNamespace(
            status_code=200,
            output=SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=[{"text": "hello"}])
                    )
                ]
            ),
        )

        # dashscope is imported lazily inside transcribe, so patch the real module.
        with patch("core.asr.direct_business_network", return_value=direct_context()):
            with patch("dashscope.MultiModalConversation.call", return_value=response):
                result = DashScopeASR(api_key="test-key").transcribe(b"\0\0" * 160)

        self.assertEqual(result, "hello")
        self.assertEqual(entered, [True])


if __name__ == "__main__":
    unittest.main()
