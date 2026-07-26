"""Install path helpers."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core import app_paths  # noqa: E402


class AppPathsTests(unittest.TestCase):
    def test_install_root_points_at_project_tree(self):
        root = app_paths.install_root()
        self.assertTrue((root / "src" / "core" / "app_paths.py").is_file())
        self.assertEqual(app_paths.payload_root(), root)
        self.assertEqual(app_paths.product_root(), root)

    def test_product_root_walks_up_from_versioned_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp) / "VoiceInput"
            payload = product / "versions" / "1.6.0" / "src" / "core"
            payload.mkdir(parents=True)
            fake = payload / "app_paths.py"
            fake.write_text("# stub\n", encoding="utf-8")
            with patch.object(app_paths, "payload_root", return_value=product / "versions" / "1.6.0"):
                self.assertEqual(app_paths.product_root(), product)
                self.assertEqual(app_paths.install_root(), product)

    def test_read_version_and_current_helpers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "python").mkdir()
            (root / "src" / "_version.py").write_text(
                'VERSION = "1.6.0"\n', encoding="utf-8"
            )
            self.assertEqual(app_paths.read_version_py(root / "src" / "_version.py"), "1.6.0")
            self.assertTrue(app_paths.is_flat_layout(root))
            self.assertFalse(app_paths.is_versioned_layout(root))
            (root / "versions" / "1.6.0").mkdir(parents=True)
            (root / "current.txt").write_text("1.6.0", encoding="utf-8")
            self.assertEqual(app_paths.read_current_version(root), "1.6.0")
            self.assertTrue(app_paths.is_versioned_layout(root))
            self.assertEqual(app_paths.version_dir("1.6.2", root), root / "versions" / "1.6.2")
            self.assertEqual(
                app_paths.partial_version_dir("1.6.2", root),
                root / "versions" / "1.6.2.partial",
            )
            self.assertEqual(
                app_paths.launcher_new_path(root),
                root / app_paths.LAUNCHER_NEW_NAME,
            )

    @patch.object(app_paths, "installed_exe_path", return_value=Path("C:/VoiceInput/VoiceInput.exe"))
    def test_autostart_command_quotes_exe(self, _exe):
        self.assertEqual(app_paths.autostart_command(), '"C:\\VoiceInput\\VoiceInput.exe"')

    @patch.object(app_paths.sys, "_MEIPASS", "C:/temp/meipass", create=True)
    def test_installed_exe_none_in_onefile_extract(self):
        self.assertIsNone(app_paths.installed_exe_path())


if __name__ == "__main__":
    unittest.main()
