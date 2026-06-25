import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from core.updater import (
    StagedUpdate,
    StagedUpdateStore,
    UpdateChecker,
    UpdateInfo,
    _CHECK_ERROR,
    _NO_UPDATE,
    _build_install_script,
    _select_latest_release,
)


def _write_staged_update(root: Path, version: str, *, source_version: str | None = None) -> Path:
    staging = root / "VoiceInput_update_staging"
    source = staging / "VoiceInput"
    src = source / "src"
    src.mkdir(parents=True)
    (src / "_version.py").write_text(
        f'"""Build-time application version."""\n\nVERSION = "{source_version or version}"\n',
        encoding="utf-8",
    )
    (staging / ".update_version").write_text(version, encoding="utf-8")
    return staging


def _update_info(version: str) -> UpdateInfo:
    return UpdateInfo(
        version=version,
        download_url=f"https://example.com/VoiceInput-{version}-portable.zip",
        filename=f"VoiceInput-{version}-portable.zip",
        size=1024,
        title=f"VoiceInput v{version}",
        body="",
        html_url=f"https://example.com/releases/v{version}",
        published_at="2026-05-12T11:00:00Z",
    )


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

    def test_install_script_waits_validates_and_preserves_staging_on_failure(self):
        script = _build_install_script(
            source=Path("C:/tmp/VoiceInput"),
            app_dir=Path("C:/Program Files/VoiceInput"),
            exe_path=Path("C:/Program Files/VoiceInput/VoiceInput.exe"),
            staged=Path("C:/tmp/VoiceInput_update_staging"),
            log_path=Path("C:/Users/me/.voiceinput/logs/update_install.log"),
            old_pid=12345,
            target_version="1.4.11",
        )

        self.assertIn("[DEBUG] update_install.ps1", script)
        self.assertIn("$OldPid = 12345", script)
        self.assertIn('$TargetVersion = "1.4.11"', script)
        self.assertIn("wait_process already_exited", script)
        self.assertIn("$OldProcess | Wait-Process", script)
        self.assertIn("wait_process_timeout", script)
        self.assertIn("wait_old_instance elapsed_ms=", script)
        self.assertIn("C:\\Program Files\\VoiceInput\\python", script)
        self.assertIn("C:\\Program Files\\VoiceInput\\src", script)
        self.assertIn("managed_path_still_exists", script)
        self.assertIn("managed_paths_removed", script)
        self.assertIn("cleanup_managed_paths elapsed_ms=", script)
        self.assertIn("robocopy_copy exit_code=", script)
        self.assertIn("robocopy_failed exit_code=", script)
        self.assertIn("verify_version installed=", script)
        self.assertIn("version_mismatch", script)
        self.assertIn("start_process_failed", script)
        self.assertIn("start_process pid=", script)
        self.assertIn("polls=$poll", script)
        self.assertIn("new_process_not_running", script)
        self.assertIn("staging_preserved path=", script)
        self.assertIn("install_success version=", script)
        self.assertIn("cleanup_staging elapsed_ms=", script)
        self.assertNotIn("Start-Job", script)
        self.assertNotIn("VoiceInputWin32", script)
        self.assertIn("total elapsed_ms=", script)
        self.assertNotIn("sleep_before_copy elapsed_ms=", script)
        self.assertNotIn("Start-Sleep -Seconds 1", script)

    def test_staged_store_validates_marker_against_source_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_staged_update(root, "1.4.17", source_version="1.4.16")
            store = StagedUpdateStore(temp_dir=root)

            self.assertIsNone(store.load())

    def test_check_result_reuses_matching_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = _write_staged_update(root, "1.4.17")
            checker = UpdateChecker()
            checker._staged_store = StagedUpdateStore(temp_dir=root)
            calls = []
            checker._cb_stage_done = lambda: calls.append("ready")
            checker._cb_available = lambda info: calls.append(("available", info.version))

            checker._on_check_result(_update_info("1.4.17"))

            self.assertEqual(calls, ["ready"])
            self.assertTrue(checker.is_ready_to_install)
            self.assertEqual(checker.staged_version, "1.4.17")
            self.assertTrue(staging.exists())

    def test_check_result_discards_obsolete_staging_and_reports_latest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = _write_staged_update(root, "1.4.17")
            checker = UpdateChecker()
            checker._staged_store = StagedUpdateStore(temp_dir=root)
            calls = []
            checker._cb_stage_done = lambda: calls.append("ready")
            checker._cb_available = lambda info: calls.append(("available", info.version))

            checker._on_check_result(_update_info("1.4.18"))

            self.assertEqual(calls, [("available", "1.4.18")])
            self.assertFalse(checker.is_ready_to_install)
            self.assertEqual(checker.staged_version, "")
            self.assertFalse(staging.exists())

    def test_check_failure_restores_valid_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_staged_update(root, "1.4.17")
            checker = UpdateChecker()
            checker._staged_store = StagedUpdateStore(temp_dir=root)
            calls = []
            checker._cb_stage_done = lambda: calls.append("ready")
            checker._cb_check_failed = lambda: calls.append("failed")

            checker._on_check_result(_CHECK_ERROR)

            self.assertEqual(calls, ["ready"])
            self.assertTrue(checker.is_ready_to_install)
            self.assertEqual(checker.staged_version, "1.4.17")

    def test_no_update_clears_stale_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = _write_staged_update(root, "1.4.17")
            checker = UpdateChecker()
            checker._staged_store = StagedUpdateStore(temp_dir=root)
            calls = []
            checker._cb_stage_done = lambda: calls.append("ready")
            checker._cb_no_update = lambda: calls.append("no-update")

            checker._on_check_result(_NO_UPDATE)

            self.assertEqual(calls, ["no-update"])
            self.assertFalse(checker.is_ready_to_install)
            self.assertFalse(staging.exists())

    def test_install_ready_rejects_expired_dialog_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = _write_staged_update(root, "1.4.17")
            source = staging / "VoiceInput"
            checker = UpdateChecker()
            checker._staged = StagedUpdate("1.4.17", staging, source)

            self.assertFalse(checker.install_ready("1.4.18"))
            self.assertIn("1.4.18", checker.last_install_error or "")

    def test_install_ready_revalidates_staging_before_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = _write_staged_update(root, "1.4.17", source_version="1.4.16")
            source = staging / "VoiceInput"
            checker = UpdateChecker()
            checker._staged_store = StagedUpdateStore(temp_dir=root)
            checker._staged = StagedUpdate("1.4.17", staging, source)

            self.assertFalse(checker.install_ready("1.4.17"))
            self.assertFalse(staging.exists())


if __name__ == "__main__":
    unittest.main()
