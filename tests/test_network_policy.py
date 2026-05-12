import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from core.network import (  # noqa: E402
    configure_direct_business_traffic,
    create_update_ssl_context,
    direct_business_network,
    open_update_url,
    resolve_update_proxies,
)


class NetworkPolicyTests(unittest.TestCase):
    def test_direct_business_traffic_clears_proxies_and_bypasses_proxy_lookup(self):
        environ = {
            "HTTP_PROXY": "http://127.0.0.1:7890",
            "HTTPS_PROXY": "http://127.0.0.1:7890",
            "ALL_PROXY": "socks5://127.0.0.1:7891",
            "NO_PROXY": "localhost",
        }

        configure_direct_business_traffic(environ)

        self.assertNotIn("HTTP_PROXY", environ)
        self.assertNotIn("HTTPS_PROXY", environ)
        self.assertNotIn("ALL_PROXY", environ)
        self.assertEqual(environ["NO_PROXY"], "*")
        self.assertEqual(environ["no_proxy"], "*")

    def test_direct_business_network_restores_previous_environment(self):
        environ = {
            "HTTPS_PROXY": "http://127.0.0.1:7890",
            "NO_PROXY": "localhost",
        }

        with direct_business_network(environ):
            self.assertNotIn("HTTPS_PROXY", environ)
            self.assertEqual(environ["NO_PROXY"], "*")

        self.assertEqual(environ, {
            "HTTPS_PROXY": "http://127.0.0.1:7890",
            "NO_PROXY": "localhost",
        })

    def test_update_proxy_ignores_business_no_proxy(self):
        with patch("core.network.windows_system_update_proxies", return_value={}):
            with patch("urllib.request.getproxies", return_value={"https": "http://127.0.0.1:7890"}):
                with patch.dict("os.environ", {"NO_PROXY": "*"}, clear=False):
                    self.assertEqual(resolve_update_proxies(), {"https": "http://127.0.0.1:7890"})

    def test_update_ssl_context_adds_certifi_to_default_context(self):
        ssl_context = MagicMock()

        with patch("core.network.certifi") as certifi:
            certifi.where.return_value = "bundle.pem"
            with patch("core.network.Path.exists", return_value=True):
                with patch("ssl.create_default_context", return_value=ssl_context) as create_context:
                    result = create_update_ssl_context()

        self.assertIs(result, ssl_context)
        create_context.assert_called_once_with()
        ssl_context.load_verify_locations.assert_called_once_with(cafile="bundle.pem")

    def test_open_update_url_uses_proxy_handler_and_ssl_context(self):
        request = object()
        ssl_context = object()
        opener = MagicMock()

        with patch("core.network.resolve_update_proxies", return_value={"https": "http://127.0.0.1:7890"}):
            with patch("core.network.create_update_ssl_context", return_value=ssl_context):
                with patch("urllib.request.ProxyHandler") as proxy_handler:
                    with patch("urllib.request.HTTPSHandler") as https_handler:
                        with patch("urllib.request.build_opener", return_value=opener) as build_opener:
                            result = open_update_url(request, timeout=10)

        self.assertIs(result, opener.open.return_value)
        proxy_handler.assert_called_once_with({"https": "http://127.0.0.1:7890"})
        https_handler.assert_called_once_with(context=ssl_context)
        build_opener.assert_called_once_with(proxy_handler.return_value, https_handler.return_value)
        opener.open.assert_called_once_with(request, timeout=10)


if __name__ == "__main__":
    unittest.main()
