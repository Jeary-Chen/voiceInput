import sys
import os
import signal

sys.path.insert(0, os.path.dirname(__file__))

# Ctrl+C 直接终止进程，不抛 KeyboardInterrupt 到随机线程
signal.signal(signal.SIGINT, signal.SIG_DFL)

# VoiceInput 只访问 dashscope.aliyuncs.com (阿里云国内)，清除代理避免冲突
for _pv in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(_pv, None)
os.environ["NO_PROXY"] = "*"

from PyQt6.QtWidgets import QApplication

from config import Config
from core.log import logger
from core.engine import VoiceEngine
from ui.mini_window import MiniRecordingWindow
from ui.tray import VoiceTray


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("VoiceInput")

    config = Config.load()

    if not config.api_key:
        from config import _config_path
        logger.warning("API key not configured")
        logger.info(f"Set DASHSCOPE_API_KEY env var or edit: {_config_path()}")

    engine = VoiceEngine(config)
    mini = MiniRecordingWindow(engine)
    tray = VoiceTray(engine, mini, config)

    hotkey_display = config.hotkey.replace("+", " + ").title()
    logger.success(f"VoiceInput started. Press {hotkey_display} to record.")

    mini.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
