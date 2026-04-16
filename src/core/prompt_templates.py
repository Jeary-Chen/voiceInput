"""Built-in polish prompt templates and seed helpers."""
import copy
import uuid


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def default_prompt_templates() -> list[dict]:
    """Factory list for first-run seed and 「恢复默认」."""
    return [
        {
            "id": _new_id(),
            "name": "翻译为英语",
            "content": "翻译为英语",
        },
        {
            "id": _new_id(),
            "name": "删除重复",
            "content": (
                "删除语句中的重复，去口语化"
            ),
        },
    ]


def seed_default_prompt_templates(cfg) -> None:
    """Populate empty custom_prompts with defaults and activate the first entry."""
    tpls = default_prompt_templates()
    cfg.custom_prompts = copy.deepcopy(tpls)
    cfg.active_prompt_id = tpls[0]["id"]
