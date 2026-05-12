import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


from build import _normalize_tag_version, _resolve_app_version


class BuildVersionTests(unittest.TestCase):
    def test_normalize_tag_version_strips_v_prefix(self):
        self.assertEqual(_normalize_tag_version("v1.2.4"), "1.2.4")
        self.assertEqual(_normalize_tag_version("1.2.4"), "1.2.4")

    def test_resolve_app_version_prefers_github_ref_name(self):
        with patch.dict(os.environ, {"GITHUB_REF_NAME": "v1.2.5"}, clear=False):
            self.assertEqual(_resolve_app_version(), "1.2.5")

    def test_resolve_app_version_falls_back_to_git_tag(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("build._git_latest_tag", return_value="v1.2.4"):
                self.assertEqual(_resolve_app_version(), "1.2.4")


if __name__ == "__main__":
    unittest.main()
