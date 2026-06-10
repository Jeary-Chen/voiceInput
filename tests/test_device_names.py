import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class DeviceNameTests(unittest.TestCase):
    def test_same_device_name_ignores_windows_duplicate_prefix(self):
        from core.device_names import same_device_name

        self.assertTrue(
            same_device_name(
                "耳机 (HUAWEI FreeBuds SE 2)",
                "耳机 (2- HUAWEI FreeBuds SE 2)",
            )
        )

    def test_device_labels_overlap_accepts_substring_match(self):
        from core.device_names import device_labels_overlap

        self.assertTrue(
            device_labels_overlap(
                "Headphones",
                "Headphones (Realtek Audio)",
            )
        )


if __name__ == "__main__":
    unittest.main()
