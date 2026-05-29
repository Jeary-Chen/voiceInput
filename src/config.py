import copy
import json
import os
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from core.log import logger
from core.prompt_templates import default_prompt_templates


def _config_dir() -> Path:
    return Path(os.environ.get("USERPROFILE", os.path.expanduser("~"))) / ".voiceinput"


def _config_path() -> Path:
    return _config_dir() / "config.json"


def _config_backup_path() -> Path:
    return _config_dir() / "config.json.bak"


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


class LoadStatus(str, Enum):
    """Result of reading config.json from disk."""

    OK = "ok"
    MISSING = "missing"
    CORRUPT = "corrupt"


@dataclass(frozen=True)
class ReloadResult:
    """Result of reloading disk into an existing Config instance."""

    changed: frozenset[str]
    status: LoadStatus
    migration_fields: frozenset[str] = frozenset()


@dataclass(frozen=True)
class LoadOutcome:
    """In-memory config plus metadata from a single read pass."""

    cfg: "Config"
    raw_data: dict
    status: LoadStatus
    migration_fields: frozenset[str]


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
_SAVE_HOOK_ATTR = "_save_hook"


def _valid_hotkey_keys() -> set[str]:
    keys = set("abcdefghijklmnopqrstuvwxyz")
    keys |= {str(i) for i in range(10)}
    keys |= {f"f{i}" for i in range(1, 25)}
    keys |= {
        "lctrl", "rctrl", "lshift", "rshift", "lalt", "ralt",
        "space", "enter", "tab", "escape", "backspace", "delete",
        "insert", "home", "end", "pageup", "pagedown",
        "up", "down", "left", "right",
        "capslock", "numlock", "scrolllock", "printscreen", "pause",
        ";", "=", ",", "-", ".", "/", "`", "[", "\\", "]", "'",
    }
    return keys


def _default(name: str):
    """Config 字段默认值。用于完整性校验、迁移、修复（非版本升级）。"""
    return copy.deepcopy(getattr(Config(), name))


def _defaults_for(*names: str) -> dict:
    """批量取默认值，仅供 _CONFIG_UPGRADES 版本升级时读取目标值。"""
    return {name: _default(name) for name in names}


def _read_json_object(path: Path) -> dict | None:
    """Return parsed dict, or None if the file is missing."""
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    if not isinstance(loaded, dict):
        raise ValueError("config root must be a JSON object")
    return loaded


def _backup_config_file(path: Path) -> None:
    if not path.exists():
        return
    backup = _config_backup_path()
    try:
        backup.write_bytes(path.read_bytes())
    except OSError as exc:
        logger.warning(f"[Config] Failed to backup {path}: {exc}")


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(path)


def _merge_missing_defaults(cfg: "Config", raw_data: dict) -> frozenset[str]:
    """补全新版本引入、但用户 config 里尚不存在的配置项。"""
    changed: set[str] = set()
    for name in cfg.__dataclass_fields__:
        if name in _META_FIELDS:
            continue
        if name not in raw_data:
            setattr(cfg, name, _default(name))
            changed.add(name)
    return frozenset(changed)


def _normalize_loaded_config(
    cfg: "Config",
    raw_data: dict,
    *,
    fill_env_api_key: bool,
) -> frozenset[str]:
    """Validate / migrate cfg in place. Returns field names that changed in memory."""
    changed = set(_merge_missing_defaults(cfg, raw_data))

    old_text = raw_data.get("custom_prompt", "").strip()
    if old_text and not cfg.custom_prompts:
        pid = uuid.uuid4().hex[:8]
        cfg.custom_prompts = [{"id": pid, "name": "自定义提示词", "content": old_text}]
        cfg.active_prompt_id = pid
        cfg.prompts_initialized = True
        changed.update({"custom_prompts", "active_prompt_id", "prompts_initialized"})

    if cfg.custom_prompts:
        if not cfg.prompts_initialized:
            cfg.prompts_initialized = True
            changed.add("prompts_initialized")
    elif not cfg.prompts_initialized:
        cfg.custom_prompts = _default("custom_prompts")
        cfg.prompts_initialized = True
        changed.update({"custom_prompts", "prompts_initialized"})

    if fill_env_api_key and not cfg.api_key:
        env_key = os.environ.get("DASHSCOPE_API_KEY", "")
        if env_key:
            cfg.api_key = env_key
            changed.add("api_key")

    from _version import VERSION
    if _is_dev_version(VERSION):
        if _is_dev_version(cfg.config_version):
            cfg.config_version = ""
            changed.add("config_version")
    elif cfg.config_version != VERSION:
        before = {
            name: copy.deepcopy(getattr(cfg, name))
            for name in cfg.__dataclass_fields__
        }
        if _apply_config_upgrades(cfg, cfg.config_version, VERSION):
            for name in cfg.__dataclass_fields__:
                if getattr(cfg, name) != before[name]:
                    changed.add(name)

    parts = [p.strip().lower() for p in cfg.hotkey.split("+")]
    default_hotkey = _default("hotkey")
    if not parts or not all(p in _valid_hotkey_keys() for p in parts):
        if cfg.hotkey != default_hotkey:
            cfg.hotkey = default_hotkey
            changed.add("hotkey")

    return frozenset(changed)


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
        backup_key = from_version
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
    def read_outcome(
        cls,
        *,
        fill_env_api_key: bool = True,
    ) -> LoadOutcome:
        """Read disk → memory. Never writes."""
        path = _config_path()
        try:
            raw = _read_json_object(path)
        except Exception as exc:
            logger.error(
                f"[Config] Failed to read {path}: {exc}; "
                "using in-memory defaults (disk left unchanged)"
            )
            return LoadOutcome(cls(), {}, LoadStatus.CORRUPT, frozenset())

        if raw is None:
            cfg = cls()
            migration = _normalize_loaded_config(
                cfg, {}, fill_env_api_key=fill_env_api_key,
            )
            return LoadOutcome(cfg, {}, LoadStatus.MISSING, migration)

        known = {fld.name for fld in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in raw.items() if k in known}
        try:
            cfg = cls(**filtered)
        except TypeError as exc:
            logger.error(
                f"[Config] Invalid field types in {path}: {exc}; "
                "using in-memory defaults (disk left unchanged)"
            )
            return LoadOutcome(cls(), raw, LoadStatus.CORRUPT, frozenset())

        migration = _normalize_loaded_config(
            cfg, raw, fill_env_api_key=fill_env_api_key,
        )
        return LoadOutcome(cfg, raw, LoadStatus.OK, migration)

    @classmethod
    def persist_migration(cls, cfg: "Config", fields: frozenset[str]) -> frozenset[str]:
        """Write normalized defaults for *fields* to disk (backup first)."""
        if not fields:
            return frozenset()
        _backup_config_file(_config_path())
        cfg._persist_fields(fields)
        logger.info(
            f"[Config] Migration persisted {len(fields)} field(s): "
            f"{', '.join(sorted(fields))}"
        )
        return fields

    @classmethod
    def finish_load(cls, outcome: LoadOutcome) -> "Config":
        """Apply persistence for a successful read outcome. Never handles CORRUPT."""
        path = _config_path()

        if outcome.status is LoadStatus.CORRUPT:
            return outcome.cfg

        if outcome.status is LoadStatus.MISSING:
            outcome.cfg._persist_all()
            logger.info(f"[Config] Created default config at {path}")
            return outcome.cfg

        if outcome.migration_fields:
            cls.persist_migration(outcome.cfg, outcome.migration_fields)

        return outcome.cfg

    @classmethod
    def load(cls) -> "Config":
        """Startup load: read → migrate in memory → persist only what migration changed."""
        return cls.finish_load(cls.read_outcome(fill_env_api_key=True))

    @classmethod
    def reload_into(
        cls, target: "Config", *, fill_env_api_key: bool = False,
    ) -> ReloadResult:
        """Runtime reload into an existing Config instance.

        Never writes disk; returns ``migration_fields`` so ConfigSync can persist
        them with watcher suppression (same fields as startup migration).
        """
        outcome = cls.read_outcome(fill_env_api_key=fill_env_api_key)
        if outcome.status in (LoadStatus.CORRUPT, LoadStatus.MISSING):
            logger.error(
                f"[Config] Runtime reload failed ({outcome.status.value}): "
                f"{_config_path()}; memory unchanged"
            )
            return ReloadResult(frozenset(), outcome.status)

        fresh = outcome.cfg
        changed: set[str] = set()
        for name in cls.__dataclass_fields__:
            old_val = copy.deepcopy(getattr(target, name))
            new_val = copy.deepcopy(getattr(fresh, name))
            if old_val != new_val:
                setattr(target, name, new_val)
                changed.add(name)
        return ReloadResult(
            frozenset(changed),
            LoadStatus.OK,
            outcome.migration_fields,
        )

    def _as_dict(self) -> dict:
        return {
            f.name: copy.deepcopy(getattr(self, f.name))
            for f in self.__dataclass_fields__.values()
        }

    def save(self, *, touched: frozenset[str] | None = None):
        """Persist config. Delegates to save hook (set by ConfigSync) when attached."""
        hook = getattr(self, _SAVE_HOOK_ATTR, None)
        if hook is not None:
            hook(touched=touched)
            return
        if touched is not None:
            self._persist_fields(touched)
        else:
            self._persist_all()

    def _persist_fields(self, fields: frozenset[str]) -> None:
        """Patch only named fields on disk; preserve everything else."""
        if not fields:
            return
        path = _config_path()
        on_disk: dict = {}
        if path.exists():
            try:
                on_disk = _read_json_object(path) or {}
            except Exception:
                on_disk = {}
        for name in fields:
            if name in self.__dataclass_fields__:
                on_disk[name] = copy.deepcopy(getattr(self, name))
        _atomic_write_json(path, on_disk)

    def _persist_all(self) -> None:
        """Full save: all known fields from memory, keep unknown top-level keys."""
        path = _config_path()
        new_data = self._as_dict()
        if path.exists():
            try:
                on_disk = _read_json_object(path) or {}
            except Exception:
                on_disk = {}
            merged = dict(on_disk)
            changed = False
            for key, val in new_data.items():
                if merged.get(key) != val:
                    merged[key] = val
                    changed = True
            if not changed:
                return
            data = merged
        else:
            data = new_data
        _atomic_write_json(path, data)

    def _write_to_disk(self):
        """Alias for ConfigSync / legacy callers — explicit full save."""
        self._persist_all()

    @staticmethod
    def history_dir() -> Path:
        d = _config_dir() / "history"
        d.mkdir(parents=True, exist_ok=True)
        return d


def open_config_in_editor(*, cfg: Config | None = None) -> None:
    """Open config.json in the system default editor, creating it if missing."""
    path = _config_path()
    if not path.exists():
        if cfg is not None:
            cfg.save()
        else:
            Config()._persist_all()
    os.startfile(str(path))


def delete_config_file() -> None:
    """Remove config.json (next load recreates defaults)."""
    path = _config_path()
    if path.exists():
        path.unlink()
        logger.warning(f"[Config] Deleted config: {path}")
