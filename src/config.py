import copy
import json
import os
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path

from core.prompt_templates import default_prompt_templates


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

# 版本升级专用：跨版本时按此列表强制覆盖已有字段（值从 Config 默认读取，此处只写字段名）。
# 完整性校验 / 迁移 / 修复不走此列表，见 _default() 与 Config.load()。
_CONFIG_UPGRADES: list[tuple[str, tuple[str, ...]]] = [
    # ("1.4.0", ("asr_model",)),
]


def _parse_ver(v: str) -> tuple[int, ...]:
    parts = []
    for p in (v or "0").split("."):
        try:
            parts.append(int(p))
        except ValueError:
            break
    return tuple(parts) or (0,)


def _is_dev_version(v: str) -> bool:
    return (v or "").strip().lower() == "dev"


_META_FIELDS = frozenset({"config_version", "upgraded_backup"})


def _default(name: str):
    """Config 字段默认值。用于完整性校验、迁移、修复（非版本升级）。"""
    return copy.deepcopy(getattr(Config(), name))


def _defaults_for(*names: str) -> dict:
    """批量取默认值，仅供 _CONFIG_UPGRADES 版本升级时读取目标值。"""
    return {name: _default(name) for name in names}


def _merge_missing_defaults(cfg: "Config", raw_data: dict) -> bool:
    """补全新版本引入、但用户 config 里尚不存在的配置项。返回是否有变更。"""
    changed = False
    for name in cfg.__dataclass_fields__:
        if name in _META_FIELDS:
            continue
        if name not in raw_data:
            setattr(cfg, name, _default(name))
            changed = True
    return changed


def _apply_config_upgrades(cfg: "Config", from_version: str, to_version: str) -> bool:
    """版本升级：按规则覆盖字段；被覆盖的旧值写入 upgraded_backup[from_version]。"""
    if _is_dev_version(to_version):
        return False
    cur = (
        (0,)
        if _is_dev_version(from_version) or not from_version
        else _parse_ver(from_version)
    )
    changed = False
    backup: dict = {}
    for ver, field_names in _CONFIG_UPGRADES:
        if _parse_ver(ver) <= cur:
            continue
        for field_name, new_val in _defaults_for(*field_names).items():
            if field_name not in cfg.__dataclass_fields__:
                continue
            old_val = getattr(cfg, field_name)
            new_val_copy = copy.deepcopy(new_val)
            if old_val != new_val_copy:
                backup[field_name] = copy.deepcopy(old_val)
                setattr(cfg, field_name, new_val_copy)
                changed = True
    if backup:
        backup_key = from_version  # 升级前已存储的 config_version
        prev = cfg.upgraded_backup.get(backup_key, {})
        merged = copy.deepcopy(prev) if isinstance(prev, dict) else {}
        merged.update(backup)
        cfg.upgraded_backup[backup_key] = merged
        changed = True
    if cfg.config_version != to_version:
        cfg.config_version = to_version
        changed = True
    return changed


@dataclass
class Config:
    hotkey: str = "lctrl+lshift+r"
    trigger_mode: str = "toggle"
    mode: str = "polish"
    custom_prompts: list = field(default_factory=default_prompt_templates)
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
        """加载配置，分两条路径（默认值均来自 Config dataclass）：

        1. 完整性校验 / 迁移 / 修复：缺字段、非法值、旧格式等 → _default() 补全或修正；
           不覆盖已有合法值。不读 _CONFIG_UPGRADES。

        2. 版本升级：仅当 config_version ≠ VERSION → _CONFIG_UPGRADES + _apply_config_upgrades
           强制覆盖声明字段并备份；dev 构建跳过。
        """
        path = _config_path()
        raw_data: dict = {}
        dirty = not path.exists()
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw_data = json.load(f)
                known = {fld.name for fld in cls.__dataclass_fields__.values()}
                filtered = {k: v for k, v in raw_data.items() if k in known}
                cfg = cls(**filtered)
            except Exception:
                cfg = cls()
                dirty = True
        else:
            cfg = cls()

        dirty |= _merge_missing_defaults(cfg, raw_data)

        # --- 完整性校验 / 迁移 / 修复：出问题用 _default()，不走 _CONFIG_UPGRADES ---
        old_text = raw_data.get("custom_prompt", "").strip()
        if old_text and not cfg.custom_prompts:
            pid = uuid.uuid4().hex[:8]
            cfg.custom_prompts = [{"id": pid, "name": "自定义提示词", "content": old_text}]
            cfg.active_prompt_id = pid
            cfg.prompts_initialized = True
            dirty = True

        if cfg.custom_prompts:
            if not cfg.prompts_initialized:
                cfg.prompts_initialized = True
                dirty = True
        elif not cfg.prompts_initialized:
            cfg.custom_prompts = _default("custom_prompts")
            cfg.prompts_initialized = True
            dirty = True

        if not cfg.api_key:
            env_key = os.environ.get("DASHSCOPE_API_KEY", "")
            if env_key:
                cfg.api_key = env_key
                dirty = True

        # --- 版本升级：仅 config_version 不一致时走 _CONFIG_UPGRADES ---
        from _version import VERSION
        if _is_dev_version(VERSION):
            if _is_dev_version(cfg.config_version):
                cfg.config_version = ""
                dirty = True
        elif cfg.config_version != VERSION:
            dirty |= _apply_config_upgrades(cfg, cfg.config_version, VERSION)

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
        default_hotkey = _default("hotkey")
        if not parts or not all(p in _VALID_KEYS for p in parts):
            if cfg.hotkey != default_hotkey:
                cfg.hotkey = default_hotkey
                dirty = True

        if dirty:
            cfg.save()
        return cfg

    def save(self):
        """写盘时与现有 JSON 按顶层字段对比，仅更新有差异的项，保留未知字段。"""
        path = _config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        new_data = asdict(self)
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    on_disk = json.load(f)
                if not isinstance(on_disk, dict):
                    on_disk = {}
            except Exception:
                on_disk = {}
            merged = dict(on_disk)
            changed = False
            for key, val in new_data.items():
                if on_disk.get(key) != val:
                    merged[key] = val
                    changed = True
            if not changed:
                return
            data = merged
        else:
            data = new_data
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @staticmethod
    def history_dir() -> Path:
        d = _config_dir() / "history"
        d.mkdir(parents=True, exist_ok=True)
        return d
