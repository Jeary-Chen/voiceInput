import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from core.fault_policy import FAULT_POLICIES, BalloonMode
from core.faults import FaultKind, FaultSource, classify_fault


class FaultClassificationTests(unittest.TestCase):
    def test_free_tier_403_is_api_remote(self):
        msg = (
            "API 403: The free tier of the model has been exhausted. "
            "If you wish to continue access the model on a paid basis, "
            'please disable the "use free tier only" mode.'
        )
        event = classify_fault(FaultSource.ENGINE, msg)
        self.assertEqual(event.kind, FaultKind.API_REMOTE)
        self.assertIn("free tier", event.message)
        policy = FAULT_POLICIES[event.kind]
        self.assertFalse(policy.persist_credential_fault)
        self.assertTrue(policy.clear_credential_fault)
        self.assertFalse(policy.block_hotkey_when_ready)

    def test_401_invalid_key_is_credential(self):
        msg = "API 401: Invalid API Key provided"
        event = classify_fault(FaultSource.ENGINE, msg)
        self.assertEqual(event.kind, FaultKind.CREDENTIAL)
        policy = FAULT_POLICIES[event.kind]
        self.assertTrue(policy.persist_credential_fault)
        self.assertTrue(policy.block_hotkey_when_ready)

    def test_missing_credentials(self):
        msg = "Missing credentials. Please pass an api_key or set OPENAI_API_KEY."
        event = classify_fault(FaultSource.ENGINE, msg)
        self.assertEqual(event.kind, FaultKind.CREDENTIAL)

    def test_bare_api_403_is_credential(self):
        event = classify_fault(FaultSource.ENGINE, "API 403:")
        self.assertEqual(event.kind, FaultKind.CREDENTIAL)

    def test_mic_source_is_device(self):
        event = classify_fault(FaultSource.MIC, "麦克风似乎已断开连接")
        self.assertEqual(event.kind, FaultKind.DEVICE)
        policy = FAULT_POLICIES[event.kind]
        self.assertEqual(policy.balloon_mode, BalloonMode.MESSAGE)

    def test_speech_silent_suppresses_balloon(self):
        event = classify_fault(FaultSource.ENGINE, "未检测到语音")
        self.assertEqual(event.kind, FaultKind.SPEECH_SILENT)
        self.assertEqual(FAULT_POLICIES[event.kind].balloon_mode, BalloonMode.NONE)

    def test_credential_policy_blocks_via_tray_flag(self):
        policy = FAULT_POLICIES[FaultKind.CREDENTIAL]
        self.assertTrue(policy.persist_credential_fault)
        self.assertTrue(policy.block_hotkey_when_ready)

    def test_api_remote_clears_credential_flag(self):
        policy = FAULT_POLICIES[FaultKind.API_REMOTE]
        self.assertTrue(policy.clear_credential_fault)
        self.assertFalse(policy.block_hotkey_when_ready)


if __name__ == "__main__":
    unittest.main()
