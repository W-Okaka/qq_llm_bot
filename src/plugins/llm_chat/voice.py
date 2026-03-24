"""
MiniMax 语音合成模块 - 调用 speech-02-hd TTS API
"""
import os

from nonebot.log import logger

from .api import _get_client


_DEFAULT_VOICE_ID = "female-shaonv-jingpin"


def _voice_not_found(status_code: int, status_msg: str) -> bool:
    """判断是否为音色不存在类错误"""
    msg = (status_msg or "").lower()
    return (
        status_code in {2054, 2013}
        or "voice id not exist" in msg
        or "tts_voice" in msg and "not found" in msg
        or "voice" in msg and "not found" in msg
    )


async def _request_tts(text: str, api_key: str, voice_id: str) -> tuple[bytes | None, int, str]:
    """请求一次 TTS，返回 (音频, 状态码, 状态信息)"""
    client = _get_client()
    response = await client.post(
        "https://api.minimaxi.com/v1/t2a_v2",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": "speech-02-hd",
            "text": text,
            "stream": False,
            "voice_setting": {
                "voice_id": voice_id,
                "speed": 1.0,
                "vol": 1.0,
                "pitch": 0,
            },
            "audio_setting": {
                "format": "mp3",
                "sample_rate": 32000,
            },
        },
        timeout=30.0,
    )
    response.raise_for_status()
    data = response.json()

    base_resp = data.get("base_resp") or {}
    status_code = int(base_resp.get("status_code", 0) or 0)
    status_msg = str(base_resp.get("status_msg", "") or "")

    audio_hex = (data.get("data") or {}).get("audio")
    if audio_hex:
        return bytes.fromhex(audio_hex), status_code, status_msg

    if status_code != 0:
        logger.error(f"MiniMax TTS 失败: status_code={status_code}, status_msg={status_msg}, voice_id={voice_id}")
    else:
        logger.error(f"MiniMax TTS 响应缺少 audio 字段: {data}")
    return None, status_code, status_msg


async def generate_voice(text: str, api_key: str) -> bytes | None:
    """调用 MiniMax TTS 生成语音，返回音频 bytes，失败返回 None"""
    if not api_key:
        logger.error("未配置 MINIMAX_API_KEY，无法生成语音")
        return None

    # 优先使用环境变量中的复刻音色，没有则用官方默认音色
    voice_id = (os.environ.get("MINIMAX_VOICE_ID", "") or "").strip() or _DEFAULT_VOICE_ID

    try:
        audio_bytes, status_code, status_msg = await _request_tts(text, api_key, voice_id)
        if audio_bytes:
            return audio_bytes

        # 复刻音色失效时，自动降级到默认音色重试一次
        if voice_id != _DEFAULT_VOICE_ID and _voice_not_found(status_code, status_msg):
            logger.warning(f"MiniMax 音色 {voice_id} 不存在，降级使用默认音色 {_DEFAULT_VOICE_ID}")
            fallback_audio, _, _ = await _request_tts(text, api_key, _DEFAULT_VOICE_ID)
            return fallback_audio

        return None
    except Exception as e:
        logger.error(f"MiniMax TTS 失败: {type(e).__name__}: {e}")
        return None
