import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


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
    _LEGACY_STAGING_APPLIED_MARKER,
    _LEGACY_STAGING_DIR_NAME,
    _NO_UPDATE,
    _STAGE_VERSION_FILE,
    _build_install_script,
    _select_latest_release,
)


def _write_staged_update(root: Path, version: str, *, source_version: str | None = None) -> Path:
    """Create a prepared versions/{ver} payload with readiness marker."""
    version_path = root / "versions" / version
    src = version_path / "src"
    python = version_path / "python"
    src.mkdir(parents=True)
    python.mkdir(parents=True)
    (src / "_version.py").write_text(
        f'"""Build-time application version."""\n\nVERSION = "{source_version or version}"\n',
        encoding="utf-8",
    )
    (version_path / _STAGE_VERSION_FILE).write_text(version, encoding="utf-8")
    return version_path


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

    def test_install_script_is_flip_only_after_prepare(self):
        script = _build_install_script(
            app_dir=Path("C:/Program Files/VoiceInput"),
            exe_path=Path("C:/Program Files/VoiceInput/VoiceInput.exe"),
            log_path=Path("C:/Users/me/.voiceinput/logs/update_install.log"),
            old_pid=12345,
            target_version="1.4.11",
        )

        self.assertIn("[DEBUG] update_install.ps1", script)
        self.assertIn("$OldPid = 12345", script)
        self.assertIn('$TargetVersion = "1.4.11"', script)
        self.assertIn("mode=flip_pointer", script)
        self.assertIn("wait_process already_exited", script)
        self.assertIn("$OldProcess | Wait-Process", script)
        self.assertIn("wait_process_timeout", script)
        self.assertIn("wait_old_instance elapsed_ms=", script)
        self.assertIn("C:\\Program Files\\VoiceInput\\versions\\1.4.11", script)
        self.assertIn("Switch-ToPreparedVersion", script)
        self.assertIn("current_switched", script)
        self.assertIn("current.txt", script)
        self.assertIn("launcher_refreshed", script)
        self.assertIn("VoiceInput.exe.new", script)
        self.assertIn("switch_prepared elapsed_ms=", script)
        self.assertIn("Update-UninstallRegistration", script)
        self.assertIn("DisplayVersion", script)
        self.assertIn("VoiceInput version $TargetVersion", script)
        self.assertIn("uninstall_reg_updated", script)
        self.assertIn("update_uninstall_reg elapsed_ms=", script)
        self.assertIn("Remove-ObsoleteVersions", script)
        self.assertIn("old_version_removed", script)
        self.assertIn("flat_python_removed", script)
        self.assertIn("orphan_payload_removed", script)
        self.assertIn("legacy_temp_staging_removed", script)
        self.assertIn(_LEGACY_STAGING_DIR_NAME, script)
        self.assertIn(_LEGACY_STAGING_APPLIED_MARKER, script)
        self.assertNotIn("robocopy_python", script)
        self.assertNotIn("robocopy_src", script)
        self.assertNotIn("Install-NewVersion", script)
        self.assertNotIn("Hand-OffStaging", script)
        self.assertNotIn("managed_paths_removed", script)
        self.assertNotIn("Copy-AppTree", script)
        self.assertNotIn("/MIR", script)
        self.assertNotIn('"/E"', script)
        self.assertIn("verify_version installed=", script)
        self.assertIn("version_mismatch", script)
        self.assertIn("current_mismatch", script)
        self.assertIn("start_process_failed", script)
        self.assertIn("start_process pid=", script)
        self.assertIn("polls=$poll", script)
        self.assertIn("new_process_not_running", script)
        self.assertIn("prepared_preserved path=", script)
        self.assertIn("install_success version=", script)
        self.assertLess(
            script.index("Switch-ToPreparedVersion"),
            script.index("Start-Process $ExePath"),
        )
        self.assertLess(
            script.index("Start-Process $ExePath"),
            script.index("cleanup_old_versions elapsed_ms="),
        )
        self.assertNotIn("Start-Job", script)
        self.assertIn("total elapsed_ms=", script)
        self.assertNotIn("sleep_before_copy elapsed_ms=", script)
        self.assertNotIn("Start-Sleep -Seconds 1", script)

    def test_staged_store_validates_marker_against_source_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_staged_update(root, "1.4.17", source_version="1.4.16")
            store = StagedUpdateStore(product_root=root)

            self.assertIsNone(store.load())

    def test_load_applicable_requires_newer_than_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_staged_update(root, "1.5.6")
            store = StagedUpdateStore(product_root=root)

            self.assertIsNone(store.load_applicable(newer_than="1.5.6"))
            self.assertIsNone(store.load_applicable(newer_than="1.5.7"))
            staged = store.load_applicable(newer_than="1.5.5")
            self.assertIsNotNone(staged)
            self.assertEqual(staged.version, "1.5.6")

    def test_reconcile_sets_memory_without_deleting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = _write_staged_update(root, "1.5.6")
            checker = UpdateChecker()
            checker._staged_store = StagedUpdateStore(product_root=root)

            with patch("core.updater.VERSION", "1.5.6"):
                checker._reconcile_staging()

            self.assertIsNone(checker._staged)
            self.assertFalse(checker.is_ready_to_install)
            self.assertTrue(staging.exists())

            with patch("core.updater.VERSION", "1.5.5"):
                checker._reconcile_staging()
                self.assertTrue(checker.is_ready_to_install)
                self.assertEqual(checker.staged_version, "1.5.6")
            self.assertTrue(staging.exists())

    def test_sweep_removes_obsolete_and_legacy_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = _write_staged_update(root, "1.5.6")
            store = StagedUpdateStore(product_root=root)
            store.temp_dir = root
            trash = root / f"{_LEGACY_STAGING_DIR_NAME}{_LEGACY_STAGING_APPLIED_MARKER}1.5.6-1"
            trash.mkdir()
            (trash / "marker.txt").write_text("x", encoding="utf-8")
            legacy = root / _LEGACY_STAGING_DIR_NAME
            legacy.mkdir()

            store.sweep(newer_than="1.5.6")

            self.assertFalse(staging.exists())
            self.assertFalse(trash.exists())
            self.assertFalse(legacy.exists())

    def test_check_result_reuses_matching_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = _write_staged_update(root, "1.4.17")
            checker = UpdateChecker()
            checker._staged_store = StagedUpdateStore(product_root=root)
            calls = []
            checker._cb_stage_done = lambda prompt: calls.append(("ready", prompt))
            checker._cb_available = lambda info: calls.append(("available", info.version))

            with patch("core.updater.VERSION", "1.4.16"):
                checker._on_check_result(_update_info("1.4.17"))
                self.assertEqual(calls, [("ready", False)])
                self.assertTrue(checker.is_ready_to_install)
                self.assertEqual(checker.staged_version, "1.4.17")
            self.assertTrue(staging.exists())

    def test_check_result_discards_obsolete_staging_and_reports_latest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = _write_staged_update(root, "1.4.17")
            checker = UpdateChecker()
            checker._staged_store = StagedUpdateStore(product_root=root)
            calls = []
            checker._cb_stage_done = lambda prompt: calls.append(("ready", prompt))
            checker._cb_available = lambda info: calls.append(("available", info.version))

            with patch("core.updater.VERSION", "1.4.16"):
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
            checker._staged_store = StagedUpdateStore(product_root=root)
            calls = []
            checker._cb_stage_done = lambda prompt: calls.append(("ready", prompt))
            checker._cb_check_failed = lambda: calls.append("failed")

            with patch("core.updater.VERSION", "1.4.16"):
                checker._on_check_result(_CHECK_ERROR)
                self.assertEqual(calls, [("ready", False)])
                self.assertTrue(checker.is_ready_to_install)
                self.assertEqual(checker.staged_version, "1.4.17")

    def test_check_failure_ignores_staging_already_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = _write_staged_update(root, "1.5.6")
            checker = UpdateChecker()
            checker._staged_store = StagedUpdateStore(product_root=root)
            calls = []
            checker._cb_stage_done = lambda prompt: calls.append(("ready", prompt))
            checker._cb_check_failed = lambda: calls.append("failed")

            with patch("core.updater.VERSION", "1.5.6"):
                checker._on_check_result(_CHECK_ERROR)
                self.assertEqual(calls, ["failed"])
                self.assertFalse(checker.is_ready_to_install)
                self.assertTrue(staging.exists())
                checker._sweep_staging()
                self.assertFalse(staging.exists())

    def test_no_update_clears_stale_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = _write_staged_update(root, "1.4.17")
            checker = UpdateChecker()
            checker._staged_store = StagedUpdateStore(product_root=root)
            calls = []
            checker._cb_stage_done = lambda prompt: calls.append(("ready", prompt))
            checker._cb_no_update = lambda: calls.append("no-update")

            checker._on_check_result(_NO_UPDATE)

            self.assertEqual(calls, ["no-update"])
            self.assertFalse(checker.is_ready_to_install)
            self.assertFalse(staging.exists())

    def test_clear_swallows_rmtree_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_staged_update(root, "1.5.6")
            store = StagedUpdateStore(product_root=root)
            with patch(
                "core.updater.shutil.rmtree",
                side_effect=PermissionError(5, "denied"),
            ):
                store.clear()  # must not raise

    def test_sweep_survives_clear_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_staged_update(root, "1.5.6")
            store = StagedUpdateStore(product_root=root)
            with patch.object(store, "clear", side_effect=RuntimeError("boom")):
                store.sweep(newer_than="1.5.6")  # must not raise

    def test_install_ready_rejects_expired_dialog_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = _write_staged_update(root, "1.4.17")
            checker = UpdateChecker()
            checker._staged_store = StagedUpdateStore(product_root=root)
            checker._staged = StagedUpdate("1.4.17", staging, staging)

            with patch("core.updater.VERSION", "1.4.16"):
                self.assertFalse(checker.install_ready("1.4.18"))
            self.assertIn("1.4.18", checker.last_install_error or "")

    def test_install_ready_revalidates_staging_before_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = _write_staged_update(root, "1.4.17", source_version="1.4.16")
            checker = UpdateChecker()
            checker._staged_store = StagedUpdateStore(product_root=root)
            checker._staged = StagedUpdate("1.4.17", staging, staging)

            with patch("core.updater.VERSION", "1.4.16"):
                self.assertFalse(checker.install_ready("1.4.17"))
            self.assertFalse(staging.exists())

    def test_clear_keeps_active_current_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = _write_staged_update(root, "1.6.1")
            staged = _write_staged_update(root, "1.6.2")
            (root / "current.txt").write_text("1.6.1", encoding="utf-8")
            # Active version should not keep a stage marker in normal use.
            (current / _STAGE_VERSION_FILE).unlink()
            store = StagedUpdateStore(product_root=root)

            store.clear()

            self.assertTrue(current.exists())
            self.assertFalse(staged.exists())


if __name__ == "__main__":
    unittest.main()
