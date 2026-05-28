"""Built-in polish prompt templates."""


def default_prompt_templates() -> list[dict]:
    """Factory list for first-run seed and 「恢复默认」.

    内置模板使用稳定 ID：首次 seed 与后续「恢复默认模板」产生相同身份，
    避免脏状态检查（_prompt_data_differs_from_disk 对比 ID 列表）误报
    未保存。用户自建条目仍用 uuid.uuid4().hex[:8]，与 __tpl_ 前缀不冲突。
    """
    return [
        {
            "id": "__tpl_translate_en",
            "name": "翻译为英语",
            "content": "翻译为英语",
        },
    ]
