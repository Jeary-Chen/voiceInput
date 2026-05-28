import copy
import json
import os
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path


def _config_dir() -> Path:
    return Path(os.environ.get("USERPROFILE", os.path.expanduser("~"))) / ".voiceinput"


def _config_path() -> Path:
    return _config_dir() / "config.json"


LATEST_ASR_MODEL = "qwen3-asr-flash-2026-02-10"

POLISH_MODELS: list[tuple[str, str]] = [
    ("qwen3.6-flash", "Qwen3.6 Flash"),
    ("qwen3.6-plus",  "Qwen3.6 Plus"),
    ("qwen3-max",     "Qwen3 Max"),
]

# 配置升级规则：(目标版本, {字段: 新值})
# 按版本顺序排列，升级时依次应用。被覆盖的旧值会备份到 config.json 的
# upgraded_backup 字段中，用户可自行查看或恢复。
_CONFIG_UPGRADES: list[tuple[str, dict]] = [
    # ("1.4.0", {"asr_model": "qwen3-asr-flash-2026-xx-xx"}),
]


def _parse_ver(v: str) -> tuple[int, ...]:
    parts = []
    for p in (v or "0").split("."):
        try:
            parts.append(int(p))
        except ValueError:
            break
    return tuple(parts) or (0,)


@dataclass
class Config:
    hotkey: str = "lctrl+lshift+r"
    trigger_mode: str = "toggle"
    mode: str = "polish"
    custom_prompts: list = field(default_factory=list)
    active_prompt_id: str = ""
    prompts_initialized: bool = False
    language: str = "auto"

    api_key: str = ""
    api_base_url: str = "https://dashscope.aliyuncs.com/api/v1"
    asr_model: str = LATEST_ASR_MODEL
    polish_model: str = "qwen3.6-flash"

    mic_index: int | None = None
    mic_name: str = ""

    paste_result: bool = True
    restore_clipboard: bool = False
    simulate_keypresses: bool = False
    tray_click_to_record: bool = True

    play_sounds: bool = True
    save_history: bool = True
    save_audio: bool = False
    hide_mini_window_when_idle: bool = False
    show_result_text: bool = False
    autostart_enabled: bool = False

    smart_chunk_max_duration_sec: int = 600
    silence_trim: bool = True
    show_countdown: bool = True
    mini_bar_show_timer: bool = True

    mini_window_x: int | None = None

    config_version: str = ""
    upgraded_backup: dict = field(default_factory=dict)

    @property
    def active_prompt_text(self) -> str:
        if not self.active_prompt_id or not self.custom_prompts:
            return ""
        for p in self.custom_prompts:
            if p.get("id") == self.active_prompt_id:
                return p.get("content", "")
        return ""

    @classmethod
    def load(cls) -> "Config":
        path = _config_path()
        raw_data: dict = {}
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw_data = json.load(f)
                known = {fld.name for fld in cls.__dataclass_fields__.values()}
                filtered = {k: v for k, v in raw_data.items() if k in known}
                cfg = cls(**filtered)
            except Exception:
                cfg = cls()
        else:
            cfg = cls()

        old_text = raw_data.get("custom_prompt", "").strip()
        if old_text and not cfg.custom_prompts:
            pid = uuid.uuid4().hex[:8]
            cfg.custom_prompts = [{"id": pid, "name": "自定义提示词", "content": old_text}]
            cfg.active_prompt_id = pid
            cfg.prompts_initialized = True

        if cfg.custom_prompts:
            cfg.prompts_initialized = True
        elif not cfg.prompts_initialized:
            from core.prompt_templates import seed_default_prompt_templates

            seed_default_prompt_templates(cfg)
            cfg.prompts_initialized = True

        if not cfg.api_key:
            cfg.api_key = os.environ.get("DASHSCOPE_API_KEY", "")

        from _version import VERSION
        if cfg.config_version != VERSION:
            cur = _parse_ver(cfg.config_version)
            for ver, changes in _CONFIG_UPGRADES:
                if _parse_ver(ver) > cur:
                    backup = {}
                    for field_name, new_val in changes.items():
                        old_val = getattr(cfg, field_name, None)
                        if old_val != new_val:
                            backup[field_name] = copy.deepcopy(old_val)
                            setattr(cfg, field_name, copy.deepcopy(new_val))
                    if backup:
                        cfg.upgraded_backup[ver] = backup
            cfg.config_version = VERSION

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
