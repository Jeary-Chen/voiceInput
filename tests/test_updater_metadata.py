import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from core.updater import UpdateInfo, _resolve_update_proxies, _select_latest_release


class UpdateMetadataTests(unittest.TestCase):
    def test_update_info_carries_release_notes_metadata(self):
        info = UpdateInfo(
            version="1.2.4",
            download_url="https://example.com/VoiceInput-1.2.4-setup.exe",
            filename="VoiceInput-1.2.4-setup.exe",
            size=1024,
            title="VoiceInput v1.2.4",
            body="修复启动时未配置 API Key 崩溃",
            html_url="https://example.com/releases/v1.2.4",
            published_at="2026-05-12T11:00:00Z",
        )

        self.assertEqual(info.title, "VoiceInput v1.2.4")
        self.assertIn("API Key", info.body)
        self.assertEqual(info.html_url, "https://example.com/releases/v1.2.4")

    def test_update_proxy_uses_windows_system_proxy_before_app_no_proxy(self):
        with patch("core.updater._windows_system_proxies", return_value={"https": "http://127.0.0.1:7890"}):
            with patch.dict("os.environ", {"NO_PROXY": "*"}, clear=False):
                self.assertEqual(_resolve_update_proxies(), {"https": "http://127.0.0.1:7890"})

    def test_select_latest_release_uses_highest_version_tag(self):
        releases = [
            {
                "tag_name": "v1.2.4",
                "draft": False,
                "prerelease": False,
                "assets": [{"name": "VoiceInput-1.2.4-portable.zip", "browser_download_url": "https://example.com/124.zip"}],
            },
            {
                "tag_name": "v1.2.5",
                "draft": False,
                "prerelease": False,
                "assets": [{"name": "VoiceInput-1.2.5-portable.zip", "browser_download_url": "https://example.com/125.zip"}],
            },
        ]

        self.assertEqual(_select_latest_release(releases, "1.2.4")["tag_name"], "v1.2.5")


if __name__ == "__main__":
    unittest.main()
