import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from core.updater import UpdateInfo, _build_install_script, _select_latest_release


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

    def test_install_script_logs_each_timed_phase(self):
        script = _build_install_script(
            source=Path("C:/tmp/VoiceInput"),
            app_dir=Path("C:/Program Files/VoiceInput"),
            exe_path=Path("C:/Program Files/VoiceInput/VoiceInput.exe"),
            staged=Path("C:/tmp/VoiceInput_update_staging"),
            log_path=Path("C:/Users/me/.voiceinput/logs/update_install.log"),
        )

        self.assertIn("[DEBUG] update_install.ps1", script)
        self.assertIn("sleep_before_copy elapsed_ms=", script)
        self.assertIn("robocopy_copy exit_code=", script)
        self.assertIn("start_process elapsed_ms=", script)
        self.assertIn("cleanup_staging elapsed_ms=", script)
        self.assertIn("total elapsed_ms=", script)


if __name__ == "__main__":
    unittest.main()
