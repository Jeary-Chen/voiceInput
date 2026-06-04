"""Declarative config version upgrade operations."""
from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from typing import Any

from core.log import logger


_CATALOG_FIELDS = {
    "polish_models": {
        "id_key": "id",
    },
}


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


def apply_config_upgrade_rules(
    cfg: object,
    *,
    from_version: str,
    to_version: str,
    rules: list[tuple[str, list[dict]]],
    is_known_field: Callable[[str], bool],
    get_default: Callable[[str], Any],
    legacy_rules: list[tuple[str, tuple[str, ...]]] | None = None,
    entry_sources: Mapping[str, Callable[[], list[dict]]] | None = None,
) -> frozenset[str]:
    """Apply versioned upgrade operations to ``cfg`` in memory."""
    if _is_dev_version(to_version):
        return frozenset()

    cur = (
        (0,)
        if _is_dev_version(from_version) or not from_version
        else _parse_ver(from_version)
    )
    changed: set[str] = set()
    backup: dict[str, Any] = {}

    def mark_backup(field: str) -> None:
        if field not in backup:
            backup[field] = copy.deepcopy(getattr(cfg, field))

    for _version, ops in _pending_rules(rules, legacy_rules, cur):
        for op in ops:
            field = str(op.get("field") or "")
            if not field or not is_known_field(field):
                logger.warning(f"[ConfigUpgrade] Skip op with unknown field: {op}")
                continue

            if op.get("op") == "set":
                if _apply_set(cfg, field, op, get_default, mark_backup):
                    changed.add(field)
                continue

            if str(op.get("op") or "").startswith("catalog_"):
                if _apply_catalog_op(cfg, field, op, entry_sources or {}, mark_backup):
                    changed.add(field)
                continue

            logger.warning(f"[ConfigUpgrade] Skip unknown op: {op}")

    if backup:
        current_backup = getattr(cfg, "upgraded_backup")
        if not isinstance(current_backup, dict):
            current_backup = {}
        backup_key = from_version
        prev = current_backup.get(backup_key, {})
        merged = copy.deepcopy(prev) if isinstance(prev, dict) else {}
        merged.update(backup)
        current_backup[backup_key] = merged
        setattr(cfg, "upgraded_backup", current_backup)
        changed.add("upgraded_backup")

    if getattr(cfg, "config_version") != to_version:
        setattr(cfg, "config_version", to_version)
        changed.add("config_version")

    return frozenset(changed)


def _pending_rules(
    rules: list[tuple[str, list[dict]]],
    legacy_rules: list[tuple[str, tuple[str, ...]]] | None,
    cur: tuple[int, ...],
) -> list[tuple[str, list[dict]]]:
    converted_legacy = [
        (
            version,
            [
                {"op": "set", "field": field_name, "value_from": "default"}
                for field_name in field_names
            ],
        )
        for version, field_names in (legacy_rules or [])
    ]
    combined = converted_legacy + list(rules)
    pending = [(version, ops) for version, ops in combined if _parse_ver(version) > cur]
    return sorted(pending, key=lambda item: _parse_ver(item[0]))


def _apply_set(
    cfg: object,
    field: str,
    op: dict,
    get_default: Callable[[str], Any],
    mark_backup: Callable[[str], None],
) -> bool:
    has_value = "value" in op
    has_value_from = "value_from" in op
    if has_value == has_value_from:
        logger.warning(f"[ConfigUpgrade] set requires exactly one value source: {op}")
        return False
    if has_value_from:
        if op.get("value_from") != "default":
            logger.warning(f"[ConfigUpgrade] Unknown value_from: {op}")
            return False
        new_val = get_default(field)
    else:
        new_val = op.get("value")

    old_val = getattr(cfg, field)
    new_val_copy = copy.deepcopy(new_val)
    if old_val == new_val_copy:
        return False
    mark_backup(field)
    setattr(cfg, field, new_val_copy)
    return True


def _apply_catalog_op(
    cfg: object,
    field: str,
    op: dict,
    entry_sources: Mapping[str, Callable[[], list[dict]]],
    mark_backup: Callable[[str], None],
) -> bool:
    spec = _CATALOG_FIELDS.get(field)
    if spec is None:
        logger.warning(f"[ConfigUpgrade] Catalog field not registered: {field}")
        return False
    current = getattr(cfg, field)
    if not isinstance(current, list):
        logger.warning(f"[ConfigUpgrade] Catalog field is not a list: {field}")
        return False

    op_name = op.get("op")
    if op_name == "catalog_remove":
        new_items = _catalog_remove(current, op)
    elif op_name == "catalog_update":
        new_items = _catalog_update(current, op, spec["id_key"])
    elif op_name == "catalog_add":
        new_items = _catalog_add(current, op, spec["id_key"], entry_sources)
    else:
        logger.warning(f"[ConfigUpgrade] Unknown catalog op: {op}")
        return False

    if new_items == current:
        return False
    mark_backup(field)
    setattr(cfg, field, new_items)
    return True


def _catalog_remove(items: list, op: dict) -> list:
    match = _match_dict(op)
    if match is None:
        return copy.deepcopy(items)
    return [copy.deepcopy(item) for item in items if not _matches(item, match)]


def _catalog_update(items: list, op: dict, id_key: str) -> list:
    match = _match_dict(op)
    patch = op.get("patch")
    if match is None or not isinstance(patch, dict) or not patch:
        logger.warning(f"[ConfigUpgrade] Invalid catalog_update op: {op}")
        return copy.deepcopy(items)
    keep_unknown = bool(op.get("keep_unknown_keys", True))
    updated: list = []
    for item in items:
        item_copy = copy.deepcopy(item)
        if not _matches(item, match):
            updated.append(item_copy)
            continue
        if keep_unknown and isinstance(item_copy, dict):
            new_item = item_copy
            new_item.update(copy.deepcopy(patch))
        else:
            new_item = copy.deepcopy(patch)
            if isinstance(item_copy, dict) and id_key in item_copy and id_key not in new_item:
                new_item[id_key] = copy.deepcopy(item_copy[id_key])
        updated.append(new_item)
    return updated


def _catalog_add(
    items: list,
    op: dict,
    id_key: str,
    entry_sources: Mapping[str, Callable[[], list[dict]]],
) -> list:
    entries = _catalog_entries(op, entry_sources)
    if_exists = op.get("if_exists", "skip")
    if if_exists not in {"skip", "update", "error"}:
        logger.warning(f"[ConfigUpgrade] Unknown if_exists value: {op}")
        return copy.deepcopy(items)

    result = copy.deepcopy(items)
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get(id_key):
            logger.warning(f"[ConfigUpgrade] Invalid catalog entry: {entry}")
            continue
        existing_indexes = [
            idx for idx, item in enumerate(result)
            if isinstance(item, dict) and item.get(id_key) == entry[id_key]
        ]
        if not existing_indexes:
            result.append(copy.deepcopy(entry))
            continue
        if if_exists == "skip":
            continue
        if if_exists == "error":
            logger.warning(f"[ConfigUpgrade] Catalog entry exists: {entry[id_key]}")
            continue
        for idx in existing_indexes:
            merged = copy.deepcopy(result[idx])
            merged.update(copy.deepcopy(entry))
            result[idx] = merged
    return result


def _catalog_entries(
    op: dict,
    entry_sources: Mapping[str, Callable[[], list[dict]]],
) -> list[dict]:
    if "entry" in op:
        return [copy.deepcopy(op["entry"])]
    if "entries" in op:
        entries = op["entries"]
        if isinstance(entries, list):
            return copy.deepcopy(entries)
        logger.warning(f"[ConfigUpgrade] entries must be a list: {op}")
        return []
    source_name = op.get("entries_from")
    if source_name:
        source = entry_sources.get(source_name)
        if source is None:
            logger.warning(f"[ConfigUpgrade] Unknown entries_from: {source_name}")
            return []
        return copy.deepcopy(source())
    logger.warning(f"[ConfigUpgrade] catalog_add requires entry/entries/entries_from: {op}")
    return []


def _match_dict(op: dict) -> dict | None:
    match = op.get("match")
    if not isinstance(match, dict) or not match:
        logger.warning(f"[ConfigUpgrade] match must be a non-empty dict: {op}")
        return None
    return match


def _matches(item: object, match: dict) -> bool:
    if not isinstance(item, dict):
        return False
    return all(_match_one(item, key, value) for key, value in match.items())


def _match_one(item: dict, key: str, value: object) -> bool:
    if key.endswith("__startswith"):
        field = key.removesuffix("__startswith")
        actual = item.get(field)
        return isinstance(actual, str) and isinstance(value, str) and actual.startswith(value)
    return item.get(key) == value
