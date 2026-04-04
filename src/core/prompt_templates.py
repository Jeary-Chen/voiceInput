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
            "name": "整理事项",
            "content": (
                "修正错别字、补标点、去口语化。"
                "并为不同的话题划分段落，用两个空行去分割。"
                "删除重复的句子。"
                "满足前面要求的前提下，尽可能使用原文表达。"
            ),
        },
    ]


def seed_default_prompt_templates(cfg) -> None:
    """Populate empty custom_prompts with defaults and activate the first entry."""
    tpls = default_prompt_templates()
    cfg.custom_prompts = copy.deepcopy(tpls)
    cfg.active_prompt_id = tpls[0]["id"]
