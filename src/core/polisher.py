"""Text polisher — refines raw ASR output via DashScope LLM."""
import dashscope
from dashscope import Generation

from core.log import logger

_SYSTEM_PROMPT = (
    "你是文本润色助手。将给你的语音识别的原始文本修正为书面语。"
    "不增删内容，保持原有语句顺序，只修正错别字、补标点、去口语化。只输出润色结果，不要输出任何其他内容。"
)


class TextPolisher:
    def __init__(self, api_key: str, model: str = "qwen-plus"):
        self._api_key = api_key
        self._model = model

    def polish(self, raw_text: str) -> str:
        if not raw_text.strip():
            return raw_text
        try:
            resp = Generation.call(
                api_key=self._api_key,
                model=self._model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": raw_text},
                ],
                result_format="message",
                timeout=15,
            )
            if resp.status_code != 200:
                logger.error(f"Polish API error {resp.status_code}: {resp.message}")
                return raw_text

            content = resp.output.choices[0].message.content
            result = content.strip() if isinstance(content, str) else str(content).strip()
            logger.info(f"Polish result: {result[:80]}{'...' if len(result) > 80 else ''}")
            return result or raw_text
        except Exception as e:
            logger.error(f"Polish failed: {e}")
            return raw_text
