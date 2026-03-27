"""ASR backend — batch transcription via DashScope (qwen3-asr-flash)."""
import base64
import io
import wave

import dashscope

from core.log import logger


class DashScopeASR:
    """Batch ASR — records everything, then transcribes in one shot."""

    def __init__(self, api_key: str, model: str = "qwen3-asr-flash",
                 base_url: str = "https://dashscope.aliyuncs.com/api/v1"):
        self.api_key = api_key
        self.model = model
        dashscope.base_http_api_url = base_url

    def transcribe(self, pcm_data: bytes,
                   sample_rate: int = 16000, channels: int = 1) -> str:
        wav_buf = io.BytesIO()
        with wave.open(wav_buf, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_data)

        wav_bytes = wav_buf.getvalue()
        b64 = base64.b64encode(wav_bytes).decode()
        data_uri = f"data:audio/wav;base64,{b64}"

        logger.info(f"ASR request: PCM {len(pcm_data)} bytes → WAV {len(wav_bytes)} bytes "
                    f"→ base64 {len(b64)} bytes")

        try:
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
            logger.error(f"ASR API call exception: {e}")
            raise

        if resp.status_code != 200:
            logger.error(f"ASR API error {resp.status_code}: {resp.message}")
            raise RuntimeError(f"API {resp.status_code}: {resp.message}")

        logger.info(f"ASR API response: status={resp.status_code}, "
                    f"request_id={getattr(resp, 'request_id', 'N/A')}")

        try:
            content = resp.output.choices[0].message.content
            if isinstance(content, list):
                if not content:
                    logger.warning(f"ASR returned content=[] (no speech detected?)")
                    return ""
                text = content[0].get("text", "")
            else:
                text = str(content) if content else ""

            if not text:
                logger.warning(f"ASR returned empty text. Raw content: {content}")
            else:
                logger.info(f"ASR result: {text[:100]}{'...' if len(text) > 100 else ''}")

            return text
        except Exception as e:
            logger.error(f"ASR response parse error: {e}")
            logger.error(f"Raw output: {resp.output}")
            return ""
