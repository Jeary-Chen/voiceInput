import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.fault_notifications import spec_for_fault
from core.fault_policy import FAULT_POLICIES, BalloonMode
from core.faults import FaultKind
from core.notification_spec import NotificationSeverity


class FaultNotificationSpecTests(unittest.TestCase):
    def test_config_busy_is_info_short(self):
        spec = spec_for_fault(FaultKind.CONFIG_BUSY, FAULT_POLICIES[FaultKind.CONFIG_BUSY])
        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertEqual(spec.severity, NotificationSeverity.INFO)
        self.assertEqual(spec.duration_ms, 1500)
        self.assertIn("正在更新配置", spec.body)

    def test_credential_generic_key_default_body(self):
        policy = FAULT_POLICIES[FaultKind.CREDENTIAL]
        spec = spec_for_fault(FaultKind.CREDENTIAL, policy)
        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertEqual(spec.severity, NotificationSeverity.ERROR)
        self.assertIn("API Key", spec.body)

    def test_prefix_message_from_event(self):
        policy = FAULT_POLICIES[FaultKind.CAPTURE]
        spec = spec_for_fault(
            FaultKind.CAPTURE, policy, event_message="未录到音频",
        )
        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertTrue(spec.body.startswith("处理失败："))

    def test_none_when_suppressed(self):
        policy = FAULT_POLICIES[FaultKind.SPEECH_SILENT]
        self.assertEqual(policy.balloon_mode, BalloonMode.NONE)
        self.assertIsNone(spec_for_fault(FaultKind.SPEECH_SILENT, policy))


if __name__ == "__main__":
    unittest.main()
