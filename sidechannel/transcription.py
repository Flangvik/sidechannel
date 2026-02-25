"""Voice message transcription via OpenAI Whisper API."""

from typing import Optional
import aiohttp
import structlog

logger = structlog.get_logger()

# Signal voice message MIME types → file extensions for Whisper
SUPPORTED_AUDIO_TYPES: dict[str, str] = {
    "audio/aac":    ".aac",
    "audio/mp4":    ".mp4",
    "audio/mpeg":   ".mp3",
    "audio/ogg":    ".ogg",
    "audio/webm":   ".webm",
    "audio/x-m4a":  ".m4a",
    "audio/m4a":    ".m4a",
}

WHISPER_URL = "https://api.openai.com/v1/audio/transcriptions"
WHISPER_MAX_BYTES = 25_000_000   # Whisper API hard limit


async def transcribe_voice(
    audio_data: bytes,
    content_type: str,
    api_key: str,
    session: aiohttp.ClientSession,
    model: str = "whisper-1",
) -> Optional[str]:
    """POST audio bytes to OpenAI Whisper and return transcript text."""
    if len(audio_data) > WHISPER_MAX_BYTES:
        logger.warning("voice_too_large_for_whisper", size=len(audio_data))
        return None

    ext = SUPPORTED_AUDIO_TYPES.get(content_type, ".mp4")
    form = aiohttp.FormData()
    form.add_field("model", model)
    form.add_field("file", audio_data, filename=f"voice{ext}", content_type=content_type)

    try:
        async with session.post(
            WHISPER_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            data=form,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status == 200:
                result = await resp.json()
                text = result.get("text", "").strip()
                logger.info("voice_transcribed", chars=len(text))
                return text or None
            body = await resp.text()
            logger.error("whisper_error", status=resp.status, body=body[:200])
            return None
    except aiohttp.ClientError as e:
        logger.error("whisper_request_failed", error=str(e))
        return None
