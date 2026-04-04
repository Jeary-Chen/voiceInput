"""Built-in polish prompt templates and seed helpers."""
import copy
import uuid

from core.polisher import DEFAULT_INSTRUCTIONS


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def default_prompt_templates() -> list[dict]:
    """Factory list for first-run seed and 「恢复默认」."""
    return [
        {
            "id": _new_id(),
            "name": "优秀模板",
            "content": DEFAULT_INSTRUCTIONS.strip(),
        }
    ]


def seed_default_prompt_templates(cfg) -> None:
    """Populate empty custom_prompts with defaults and activate the first entry."""
    tpls = default_prompt_templates()
    cfg.custom_prompts = copy.deepcopy(tpls)
    cfg.active_prompt_id = tpls[0]["id"]
