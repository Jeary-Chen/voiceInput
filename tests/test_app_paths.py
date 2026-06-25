"""Install path helpers."""
import sys
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

    @patch.object(app_paths, "installed_exe_path", return_value=Path("C:/VoiceInput/VoiceInput.exe"))
    def test_autostart_command_quotes_exe(self, _exe):
        self.assertEqual(app_paths.autostart_command(), '"C:\\VoiceInput\\VoiceInput.exe"')

    @patch.object(app_paths.sys, "_MEIPASS", "C:/temp/meipass", create=True)
    def test_installed_exe_none_in_onefile_extract(self):
        self.assertIsNone(app_paths.installed_exe_path())


if __name__ == "__main__":
    unittest.main()
