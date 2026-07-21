import copy
import json
import os
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from config_upgrade_ops import apply_config_upgrade_rules, _parse_ver as _parse_upgrade_ver
from core.log import logger
from core.prompt_templates import default_prompt_templates
from core.output_mode import DEFAULT_OUTPUT_MODE


def _config_dir() -> Path:
    return Path(os.environ.get("USERPROFILE", os.path.expanduser("~"))) / ".voiceinput"


def _config_path() -> Path:
    return _config_dir() / "config.json"


def _config_backup_path() -> Path:
    return _config_dir() / "config.json.bak"


LATEST_ASR_MODEL = "qwen3-asr-flash-2026-02-10"


def default_polish_models() -> list[dict]:
    """Tray 润色模型菜单的出厂列表；用户可在 config.json 的 polish_models 中覆盖。"""
    return [
        # Qwen
        {"id": "qwen3.5-plus-2026-04-20", "label": "Qwen3.5 Plus 2026-04-20"},
        {"id": "qwen3.6-flash", "label": "Qwen3.6 Flash"},
        {"id": "qwen3.6-flash-2026-04-16", "label": "Qwen3.6 Flash 2026-04-16"},
        {"id": "qwen3.6-plus", "label": "Qwen3.6 Plus"},
        {"id": "qwen3.6-plus-2026-04-02", "label": "Qwen3.6 Plus 2026-04-02"},
        {"id": "qwen3.6-max-preview", "label": "Qwen3.6 Max Preview"},
        {"id": "qwen3.6-27b", "label": "Qwen3.6 27B"},
        {"id": "qwen3.6-35b-a3b", "label": "Qwen3.6 35B A3B"},
        {"id": "qwen3.7-plus", "label": "Qwen3.7 Plus"},
        {"id": "qwen3.7-plus-2026-05-26", "label": "Qwen3.7 Plus 2026-05-26"},
        {"id": "qwen3.7-max", "label": "Qwen3.7 Max"},
        {"id": "qwen3.7-max-2026-05-17", "label": "Qwen3.7 Max 2026-05-17"},
        {"id": "qwen3.7-max-2026-05-20", "label": "Qwen3.7 Max 2026-05-20"},
        {"id": "qwen3.7-max-preview", "label": "Qwen3.7 Max Preview"},
        # GLM
        {"id": "glm-5.1", "label": "GLM 5.1"},
        # Kimi
        {"id": "kimi-k2.6", "label": "Kimi K2.6"},
        # DeepSeek
        {"id": "deepseek-v4-flash", "label": "DeepSeek V4 Flash"},
        {"id": "deepseek-v4-pro", "label": "DeepSeek V4 Pro"},
        # GUI
        {"id": "gui-plus-2026-02-26", "label": "GUI Plus 2026-02-26"},
    ]


def _official_polish_model_ids() -> list[str]:
    return [entry["id"] for entry in default_polish_models()]


def default_enabled_polish_models() -> list[str]:
    """默认只启用托盘菜单中的常用润色模型。"""
    return [
        "qwen3.6-flash",
        "qwen3.6-plus",
        "qwen3.7-max",
    ]


def _order_polish_models_catalog(items: list[tuple[str, str]]) -> list[dict]:
    """官方模型按出厂顺序排列，用户自定义项保留在末尾。"""
    by_id = {mid: {"id": mid, "label": label} for mid, label in items}
    official_ids = set(_official_polish_model_ids())
    ordered: list[dict] = []
    for model_id in _official_polish_model_ids():
        if model_id in by_id:
            ordered.append(by_id[model_id])
    for mid, label in items:
        if mid not in official_ids:
            ordered.append({"id": mid, "label": label})
    return ordered


def polish_model_menu_items(models: list | None) -> list[tuple[str, str]]:
    """将 config 中的 polish_models 规范为 (id, label) 列表，供托盘菜单使用。"""
    items: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in models or []:
        if isinstance(entry, (list, tuple)) and entry:
            model_id = str(entry[0]).strip()
            label = str(entry[1]).strip() if len(entry) > 1 else model_id
        elif isinstance(entry, dict):
            model_id = str(entry.get("id") or "").strip()
            label = str(
                entry.get("label") or entry.get("name") or model_id,
            ).strip()
        else:
            continue
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        items.append((model_id, label or model_id))
    return items


def enabled_polish_model_menu_items(
    models: list | None,
    enabled_model_ids: list | None,
) -> list[tuple[str, str]]:
    """返回当前启用的润色模型菜单项。"""
    items = polish_model_menu_items(models)
    enabled = _normalize_enabled_polish_model_ids(
        enabled_model_ids,
        items,
        allow_missing=True,
    )
    by_id = {mid: (mid, label) for mid, label in items}
    return [by_id[mid] for mid in enabled if mid in by_id]


def _normalize_enabled_polish_model_ids(
    enabled_model_ids: list | None,
    items: list[tuple[str, str]],
    *,
    allow_missing: bool = False,
) -> list[str]:
    valid_ids = {mid for mid, _ in items}
    enabled: list[str] = []
    seen: set[str] = set()
    if enabled_model_ids is None and allow_missing:
        return [mid for mid, _ in items]
    if not isinstance(enabled_model_ids, list):
        raise ValueError("enabled_polish_models must be a list")
    raw_ids = enabled_model_ids
    for raw_id in raw_ids:
        model_id = str(raw_id).strip()
        if not model_id or model_id not in valid_ids or model_id in seen:
            continue
        seen.add(model_id)
        enabled.append(model_id)
    if enabled:
        return enabled
    raise ValueError("enabled_polish_models has no valid model id")


def _normalize_polish_models(cfg: "Config") -> frozenset[str]:
    """校验 polish_models 与 polish_model 一致；非法项丢弃或回退出厂列表。"""
    changed: set[str] = set()
    items = polish_model_menu_items(cfg.polish_models)
    if not items:
        cfg.polish_models = _default("polish_models")
        changed.add("polish_models")
        items = polish_model_menu_items(cfg.polish_models)
    else:
        canonical = _order_polish_models_catalog(items)
        if canonical != cfg.polish_models:
            cfg.polish_models = canonical
            changed.add("polish_models")
    enabled_ids = _normalize_enabled_polish_model_ids(
        cfg.enabled_polish_models,
        items,
    )
    if cfg.enabled_polish_models != enabled_ids:
        cfg.enabled_polish_models = enabled_ids
        changed.add("enabled_polish_models")
    if cfg.polish_model not in set(enabled_ids):
        cfg.polish_model = enabled_ids[0]
        changed.add("polish_model")
    return frozenset(changed)

# 版本升级专用：跨版本时按声明式规则修改已有字段。
# 完整性校验 / 迁移 / 修复不走此列表，见 _default() 与 Config.load()。
_CONFIG_UPGRADE_RULES: list[tuple[str, list[dict]]] = [
    ("1.4.6", [
        {"op": "set", "field": "polish_models", "value_from": "default"},
        {"op": "set", "field": "enabled_polish_models", "value_from": "default"},
    ]),
]

# 旧版兼容：整字段覆盖会在启动时转换为 set op。新规则请写 _CONFIG_UPGRADE_RULES。
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


def _is_dev_version(v: str) -> bool:
    return (v or "").strip().lower() == "dev"


def _parse_ver(v: str) -> tuple[int, ...]:
    """Compatibility wrapper for internal callers; implementation lives with upgrade ops."""
    return _parse_upgrade_ver(v)


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
        json.dumps(_ordered_root_config(data), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(path)


def _ordered_root_config(data: dict) -> dict:
    """Order root config keys: known fields first, unknown fields, backup last."""
    ordered: dict = {}
    field_names = list(Config.__dataclass_fields__)
    backup_key = "upgraded_backup"
    for name in field_names:
        if name != backup_key and name in data:
            ordered[name] = data[name]
    known = set(field_names)
    for name in sorted(key for key in data if key not in known):
        ordered[name] = data[name]
    if backup_key in data:
        ordered[backup_key] = data[backup_key]
    return ordered


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
        changed.update(_apply_config_upgrades(cfg, cfg.config_version, VERSION))

    parts = [p.strip().lower() for p in cfg.hotkey.split("+")]
    default_hotkey = _default("hotkey")
    if not parts or not all(p in _valid_hotkey_keys() for p in parts):
        if cfg.hotkey != default_hotkey:
            cfg.hotkey = default_hotkey
            changed.add("hotkey")

    changed |= set(_normalize_polish_models(cfg))
    changed |= set(_migrate_output_mode(cfg, raw_data))

    return frozenset(changed)


def _migrate_output_mode(cfg: "Config", raw_data: dict) -> frozenset[str]:
    """Canonicalize output_mode; derive from legacy paste_result/restore_clipboard."""
    from core.output_mode import (
        LEGACY_OUTPUT_CONFIG_KEYS,
        normalize_output_mode,
        resolve_output_mode_from_raw,
    )

    changed: set[str] = set()
    if raw_data and (
        "output_mode" in raw_data
        or (LEGACY_OUTPUT_CONFIG_KEYS & raw_data.keys())
    ):
        mode = resolve_output_mode_from_raw(raw_data)
    else:
        mode = normalize_output_mode(getattr(cfg, "output_mode", None))

    if cfg.output_mode != mode:
        cfg.output_mode = mode
        changed.add("output_mode")
    elif (
        raw_data
        and "output_mode" not in raw_data
        and (LEGACY_OUTPUT_CONFIG_KEYS & raw_data.keys())
    ):
        # 模式值碰巧等于默认，仍需落盘 output_mode，便于剥离旧字段后可自描述。
        changed.add("output_mode")
    return frozenset(changed)


def _strip_legacy_config_keys() -> bool:
    """Remove retired keys from disk. Returns True if the file was rewritten."""
    from core.output_mode import LEGACY_OUTPUT_CONFIG_KEYS

    path = _config_path()
    if not path.exists():
        return False
    try:
        on_disk = _read_json_object(path) or {}
    except Exception:
        return False
    removed = [key for key in LEGACY_OUTPUT_CONFIG_KEYS if key in on_disk]
    if not removed:
        return False
    for key in removed:
        del on_disk[key]
    _backup_config_file(path)
    _atomic_write_json(path, on_disk)
    logger.info(
        f"[Config] Stripped legacy field(s): {', '.join(sorted(removed))}"
    )
    return True


def _apply_config_upgrades(
    cfg: "Config",
    from_version: str,
    to_version: str,
) -> frozenset[str]:
    """版本升级：按声明式规则修改字段；旧值写入 upgraded_backup[from_version]。"""
    return apply_config_upgrade_rules(
        cfg,
        from_version=from_version,
        to_version=to_version,
        rules=_CONFIG_UPGRADE_RULES,
        legacy_rules=_CONFIG_UPGRADES,
        is_known_field=lambda name: name in cfg.__dataclass_fields__,
        get_default=_default,
        entry_sources={"default_polish_models": default_polish_models},
    )


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
    polish_models: list = field(default_factory=default_polish_models)
    enabled_polish_models: list = field(default_factory=default_enabled_polish_models)
    polish_model: str = "qwen3.6-flash"

    mic_index: int | None = None
    mic_name: str = ""

    # copy | paste | paste_copy — see core.output_mode
    output_mode: str = DEFAULT_OUTPUT_MODE
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
            try:
                migration = _normalize_loaded_config(
                    cfg, {}, fill_env_api_key=fill_env_api_key,
                )
            except Exception as exc:
                logger.error(f"[Config] Failed to normalize defaults: {exc}")
                return LoadOutcome(cls(), {}, LoadStatus.CORRUPT, frozenset())
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

        try:
            migration = _normalize_loaded_config(
                cfg, raw, fill_env_api_key=fill_env_api_key,
            )
        except Exception as exc:
            logger.error(
                f"[Config] Invalid config values in {path}: {exc}; "
                "using in-memory defaults (disk left unchanged)"
            )
            return LoadOutcome(cls(), raw, LoadStatus.CORRUPT, frozenset())
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

        cls.cleanup_legacy_disk_keys()

        return outcome.cfg

    @classmethod
    def cleanup_legacy_disk_keys(cls) -> bool:
        """Strip retired config keys from disk (safe no-op if absent)."""
        return _strip_legacy_config_keys()

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
