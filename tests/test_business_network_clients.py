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
        with patch("core.polisher.httpx.Client") as http_client_cls:
            with patch("core.polisher.OpenAI") as openai_cls:
                TextPolisher(api_key="test-key")

        http_client_cls.assert_called_once_with(trust_env=False)
        openai_cls.assert_called_once_with(
            api_key="test-key",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            http_client=http_client_cls.return_value,
        )

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

        with patch("core.asr.direct_business_network", return_value=direct_context()):
            with patch("core.asr.dashscope.MultiModalConversation.call", return_value=response):
                result = DashScopeASR(api_key="test-key").transcribe(b"\0\0" * 160)

        self.assertEqual(result, "hello")
        self.assertEqual(entered, [True])


if __name__ == "__main__":
    unittest.main()
