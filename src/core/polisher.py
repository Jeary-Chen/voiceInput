"""Text polisher — refines raw ASR output via OpenAI-compatible API."""
import re

from openai import OpenAI

from core.log import logger

_TAG = "[Polisher]"

DEFAULT_INSTRUCTIONS = "优化表达，不增删内容，保持原有的语句顺序。"

_TASK_PREAMBLE="将给你的语音识别原始文本按照要求润色。"

_OUTPUT_FORMAT = (
    "【输出格式】：用户会用 ```text 代码块包裹需要润色的内容。"
    "你也必须用 ```text 代码块包裹润色结果输出。"
    "如果代码块内容为空，则什么都不输出。"
    "任何时候不得违反【输出格式】要求。"
)


def _build_system_prompt(custom_instructions: str) -> str:
    custom = (custom_instructions or "").strip()
    if custom:
        return _TASK_PREAMBLE + "要求：" + custom + _OUTPUT_FORMAT
    return _TASK_PREAMBLE + "要求：" + DEFAULT_INSTRUCTIONS + _OUTPUT_FORMAT


def _extract_from_codeblock(text: str) -> str:
    """从 markdown 代码块中提取内容，兼容各种残缺格式。"""
    match = re.search(r"```(?:\w*)\n(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    cleaned = text.strip()
    cleaned = re.sub(r"^`{1,3}\s*\w*\n?", "", cleaned)
    cleaned = re.sub(r"\n?`{1,3}\s*$", "", cleaned)
    return cleaned.strip()


def _to_compatible_url(base_url: str) -> str:
    base_url = base_url.rstrip("/")
    if "/compatible-mode" not in base_url:
        base_url = base_url.rsplit("/api/", 1)[0] + "/compatible-mode/v1"
    return base_url


class TextPolisher:
    def __init__(self, api_key: str, model: str = "qwen3.6-flash",
                 base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"):
        self._model = model
        self._base_url = _to_compatible_url(base_url)
        self._client: OpenAI | None = None
        self.update_api_key(api_key)
        logger.info(f"{_TAG} Initialized (model={model}, url={self._base_url})")

    def update_api_key(self, api_key: str):
        api_key = (api_key or "").strip()
        if not api_key:
            self._client = None
            logger.warning(f"{_TAG} API key not configured; client disabled")
            return
        try:
            self._client = OpenAI(api_key=api_key, base_url=self._base_url)
            logger.info(f"{_TAG} API key updated")
        except Exception as e:
            self._client = None
            logger.error(f"{_TAG} API client init failed: {e}")

    def set_model(self, model: str):
        old = self._model
        self._model = model
        logger.info(f"{_TAG} Model changed: {old} → {model}")

    def polish(self, raw_text: str, extra_instructions: str = "") -> tuple[bool, str]:
        """Returns (api_ok, text). api_ok is False only when the request raised."""
        if not raw_text.strip():
            return True, raw_text
        if self._client is None:
            logger.warning(f"{_TAG} Skipped: API key not configured")
            return False, raw_text
        try:
            system_content = _build_system_prompt(extra_instructions)
            user_content = f"```text\n{raw_text}\n```"
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": user_content},
                ],
                extra_body={"enable_thinking": False},
                timeout=15,
            )
            content = resp.choices[0].message.content
            raw_result = content.strip() if isinstance(content, str) else str(content).strip()
            result = _extract_from_codeblock(raw_result)
            logger.info(f"{_TAG} Result: {result[:80]}{'…' if len(result) > 80 else ''}")
            return True, (result or raw_text)
        except Exception as e:
            logger.error(f"{_TAG} Failed: {e}")
            return False, raw_text
