"""ASR backend — batch transcription via DashScope (qwen3-asr-flash series)."""
import base64
import io
import wave

from core.log import logger
from core.network import direct_business_network

_TAG = "[ASR]"


class DashScopeASR:
    """Batch ASR — records everything, then transcribes in one shot.

    The dashscope SDK import is deferred to transcribe(): it costs hundreds of
    milliseconds and would otherwise run before the tray icon appears.  main()
    warms it in a background thread right after the UI is up.
    """

    def __init__(self, api_key: str, model: str = "qwen3-asr-flash-2026-02-10",
                 base_url: str = "https://dashscope.aliyuncs.com/api/v1"):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        logger.info(f"{_TAG} Initialized (model={model})")

    def update_settings(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        if api_key is not None:
            self.api_key = api_key
        if model is not None:
            self.model = model
        if base_url is not None:
            self.base_url = base_url
        logger.info(f"{_TAG} Settings updated (model={self.model})")

    def transcribe(self, pcm_data: bytes,
                   sample_rate: int = 16000, channels: int = 1) -> str:
        import dashscope
        dashscope.base_http_api_url = self.base_url

        wav_buf = io.BytesIO()
        with wave.open(wav_buf, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_data)

        wav_bytes = wav_buf.getvalue()
        b64 = base64.b64encode(wav_bytes).decode()
        data_uri = f"data:audio/wav;base64,{b64}"

        logger.info(f"{_TAG} Request: PCM {len(pcm_data)} B → WAV {len(wav_bytes)} B "
                    f"→ base64 {len(b64)} B")

        try:
            with direct_business_network():
                resp = dashscope.MultiModalConversation.call(
                    api_key=self.api_key,
                    model=self.model,
                    messages=[{
                        "role": "user",
                        "content": [{"audio": data_uri}],
                    }],
                    result_format="message",
                    asr_options={"enable_itn": False},
                )
        except Exception as e:
            logger.error(f"{_TAG} API call exception: {e}")
            raise

        if resp.status_code != 200:
            logger.error(f"{_TAG} API error {resp.status_code}: {resp.message}")
            raise RuntimeError(f"API {resp.status_code}: {resp.message}")

        logger.debug(f"{_TAG} Response: status={resp.status_code}, "
                     f"request_id={getattr(resp, 'request_id', 'N/A')}")

        try:
            content = resp.output.choices[0].message.content
            if isinstance(content, list):
                if not content:
                    logger.warning(f"{_TAG} Returned content=[] (no speech detected?)")
                    return ""
                text = content[0].get("text", "")
            else:
                text = str(content) if content else ""

            if not text:
                logger.warning(f"{_TAG} Empty text. Raw: {content}")
            else:
                logger.info(f"{_TAG} Result: {text[:100]}{'…' if len(text) > 100 else ''}")

            return text
        except Exception as e:
            logger.error(f"{_TAG} Response parse error: {e}")
            logger.error(f"{_TAG} Raw output: {resp.output}")
            return ""
