import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path


def _config_dir() -> Path:
    return Path(os.environ.get("USERPROFILE", os.path.expanduser("~"))) / ".voiceinput"


def _config_path() -> Path:
    return _config_dir() / "config.json"


@dataclass
class Config:
    hotkey: str = "lctrl+lshift+r"
    trigger_mode: str = "toggle"
    mode: str = "transcribe"
    custom_prompt: str = ""
    language: str = "auto"

    api_key: str = ""
    api_base_url: str = "https://dashscope.aliyuncs.com/api/v1"
    asr_model: str = "qwen3-asr-flash"

    mic_index: int | None = None

    paste_result: bool = True
    restore_clipboard: bool = False
    simulate_keypresses: bool = False
    tray_click_to_record: bool = True

    play_sounds: bool = True
    save_history: bool = True
    save_audio: bool = False

    mini_window_x: int | None = None

    @classmethod
    def load(cls) -> "Config":
        path = _config_path()
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                known = {fld.name for fld in cls.__dataclass_fields__.values()}
                filtered = {k: v for k, v in data.items() if k in known}
                cfg = cls(**filtered)
            except Exception:
                cfg = cls()
        else:
            cfg = cls()

        if not cfg.api_key:
            cfg.api_key = os.environ.get("DASHSCOPE_API_KEY", "")

        _VALID_KEYS = set("abcdefghijklmnopqrstuvwxyz")
        _VALID_KEYS |= {str(i) for i in range(10)}
        _VALID_KEYS |= {f"f{i}" for i in range(1, 25)}
        _VALID_KEYS |= {
            "lctrl", "rctrl", "lshift", "rshift", "lalt", "ralt",
            "space", "enter", "tab", "escape", "backspace", "delete",
            "insert", "home", "end", "pageup", "pagedown",
            "up", "down", "left", "right",
            "capslock", "numlock", "scrolllock", "printscreen", "pause",
            ";", "=", ",", "-", ".", "/", "`", "[", "\\", "]", "'",
            "mouse_left", "mouse_right", "mouse_middle",
        }
        parts = [p.strip().lower() for p in cfg.hotkey.split("+")]
        if not parts or not all(p in _VALID_KEYS for p in parts):
            cfg.hotkey = cls.hotkey

        cfg.save()
        return cfg

    def save(self):
        path = _config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)

    @staticmethod
    def history_dir() -> Path:
        d = _config_dir() / "history"
        d.mkdir(parents=True, exist_ok=True)
        return d
