import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class LogCrashHandlerTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._log_dir = Path(self._tmpdir.name)
        self._session_log = self._log_dir / "voiceinput_test.log"

    def tearDown(self):
        self._tmpdir.cleanup()

    def _import_log_module(self):
        with patch("core.log._cleanup_old_logs"), patch(
            "core.log.install_crash_handlers"
        ), patch("core.log.logger.remove"), patch("core.log.logger.add"):
            import core.log as log_module

        log_module._LOG_DIR = self._log_dir
        log_module._session_log = self._session_log
        log_module.session_log_path = self._session_log
        return log_module

    def test_exception_hook_writes_crash_marker_and_flushes(self):
        log_module = self._import_log_module()
        flush_calls = []
        log_module.flush_log = lambda: flush_calls.append("flush")

        with patch.object(log_module.logger, "opt") as opt:
            opt.return_value.critical = lambda msg: None
            try:
                raise RuntimeError("boom")
            except RuntimeError:
                log_module._exception_hook(*sys.exc_info())

        self.assertEqual(flush_calls, ["flush"])
        content = self._session_log.read_text(encoding="utf-8")
        self.assertIn("[UNHANDLED_EXCEPTION]", content)
        self.assertIn("RuntimeError: boom", content)
        self.assertIn(f"run={log_module.run_id}", content)

    def test_thread_exception_hook_records_thread_name(self):
        log_module = self._import_log_module()
        log_module.flush_log = lambda: None

        with patch.object(log_module.logger, "opt") as opt:
            opt.return_value.error = lambda msg: None
            try:
                raise ValueError("thread boom")
            except ValueError:
                exc_info = sys.exc_info()
            log_module._thread_exception_hook(
                SimpleNamespace(
                    exc_type=exc_info[0],
                    exc_value=exc_info[1],
                    exc_traceback=exc_info[2],
                    thread=SimpleNamespace(name="Worker-1"),
                )
            )

        content = self._session_log.read_text(encoding="utf-8")
        self.assertIn("[THREAD_EXCEPTION:Worker-1]", content)
        self.assertIn("ValueError: thread boom", content)

    def test_unraisable_hook_records_object_context(self):
        log_module = self._import_log_module()
        log_module.flush_log = lambda: None

        with patch.object(log_module.logger, "opt") as opt:
            opt.return_value.error = lambda msg: None
            try:
                raise OSError("cleanup failed")
            except OSError as exc:
                log_module._unraisable_hook(
                    SimpleNamespace(
                        exc_type=type(exc),
                        exc_value=exc,
                        exc_traceback=exc.__traceback__,
                        err_msg="in __del__",
                        object=object(),
                    )
                )

        content = self._session_log.read_text(encoding="utf-8")
        self.assertIn("[UNRAISABLE_EXCEPTION]", content)
        self.assertIn("cleanup failed", content)
        self.assertIn("in __del__", content)

    def test_qt_fatal_records_marker(self):
        log_module = self._import_log_module()
        captured = {}

        def fake_install(handler):
            captured["handler"] = handler

        from PyQt6.QtCore import QtMsgType

        with patch("PyQt6.QtCore.qInstallMessageHandler", side_effect=fake_install), patch.object(
            log_module.logger, "critical"
        ):
            log_module.install_qt_handler()
            captured["handler"](QtMsgType.QtFatalMsg, None, "fatal qt message")

        content = self._session_log.read_text(encoding="utf-8")
        self.assertIn("[QT_FATAL]", content)
        self.assertIn("fatal qt message", content)

    def test_normal_shutdown_marker_written_once(self):
        log_module = self._import_log_module()
        log_module._shutdown_logged = False
        log_module.flush_log = lambda: None

        with patch.object(log_module.logger, "info") as info:
            log_module._log_normal_shutdown()
            log_module._log_normal_shutdown()
            self.assertEqual(info.call_count, 1)
            message = info.call_args.args[0]
            self.assertIn("app.lifecycle.end - Process exiting normally", message)
            self.assertIn("exit_code=0", message)

    def test_format_event_uses_stable_event_and_key_value_fields(self):
        log_module = self._import_log_module()

        message = log_module.format_event(
            "audio.device.changed",
            "Audio device changed",
            generation=3,
            default_name="Built-in Mic",
            open_probe=True,
        )

        self.assertIn("audio.device.changed - Audio device changed", message)
        self.assertIn("generation=3", message)
        self.assertIn('default_name="Built-in Mic"', message)
        self.assertIn("open_probe=true", message)

    def test_format_event_redacts_sensitive_fields(self):
        log_module = self._import_log_module()

        message = log_module.format_event(
            "config.api_key.updated",
            "API key updated",
            api_key="sk-secret",
            hotkey="f1",
        )

        self.assertIn("api_key=[REDACTED]", message)
        self.assertIn("hotkey=f1", message)
        self.assertNotIn("sk-secret", message)


if __name__ == "__main__":
    unittest.main()
