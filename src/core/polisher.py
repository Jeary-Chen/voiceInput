"""Text polisher — refines raw ASR output via OpenAI-compatible API."""
from openai import OpenAI

from core.log import logger

_TAG = "[Polisher]"

_SYSTEM_PROMPT = (
    "将给你的语音识别的原始文本修正为书面语。"
    "不增删内容，保持原有语句顺序，只修正错别字、补标点、去口语化。只输出润色结果，不要输出任何其他内容。"
)


def _to_compatible_url(base_url: str) -> str:
    base_url = base_url.rstrip("/")
    if "/compatible-mode" not in base_url:
        base_url = base_url.rsplit("/api/", 1)[0] + "/compatible-mode/v1"
    return base_url


class TextPolisher:
    def __init__(self, api_key: str, model: str = "qwen3.5-flash",
                 base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"):
        self._model = model
        self._base_url = _to_compatible_url(base_url)
        self._client = OpenAI(api_key=api_key, base_url=self._base_url)
        logger.info(f"{_TAG} Initialized (model={model}, url={self._base_url})")

    def update_api_key(self, api_key: str):
        self._client = OpenAI(api_key=api_key, base_url=self._base_url)
        logger.info(f"{_TAG} API key updated")

    def set_model(self, model: str):
        old = self._model
        self._model = model
        logger.info(f"{_TAG} Model changed: {old} → {model}")

    def polish(self, raw_text: str) -> str:
        if not raw_text.strip():
            return raw_text
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": raw_text},
                ],
                extra_body={"enable_thinking": False},
                timeout=15,
            )
            content = resp.choices[0].message.content
            result = content.strip() if isinstance(content, str) else str(content).strip()
            logger.info(f"{_TAG} Result: {result[:80]}{'…' if len(result) > 80 else ''}")
            return result or raw_text
        except Exception as e:
            logger.error(f"{_TAG} Failed: {e}")
            return raw_text
