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

    def test_pyaudio_truncation_pair_only_matches_exact_31_prefix(self):
        from core.device_names import (
            PYAUDIO_DEVICE_NAME_MAX_LEN,
            is_pyaudio_name_truncation_pair,
            pyaudio_truncated_name,
            same_device_name,
        )

        full = "耳机 (HUAWEI FreeBuds SE 2 Hands-Free AG Audio)"
        trunc = pyaudio_truncated_name(full)
        self.assertEqual(len(trunc), PYAUDIO_DEVICE_NAME_MAX_LEN)
        self.assertTrue(is_pyaudio_name_truncation_pair(trunc, full))
        self.assertTrue(same_device_name(trunc, full))
        # Distinct endpoints that merely share a brand must not collapse.
        other = "耳机 (HUAWEI FreeBuds SE 2 Stereo)"
        self.assertFalse(is_pyaudio_name_truncation_pair(trunc, other))
        self.assertFalse(is_pyaudio_name_truncation_pair(full, other))


if __name__ == "__main__":
    unittest.main()
