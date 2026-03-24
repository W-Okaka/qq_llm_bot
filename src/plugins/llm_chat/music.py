"""
MiniMax 音乐生成模块 - 调用 music-2.5+ API
"""
from nonebot.log import logger

from .api import _get_client


async def generate_music(prompt: str, api_key: str, lyrics: str = "") -> bytes | None:
    """调用 MiniMax music-2.5+ 生成音乐，返回音频 bytes，失败返回 None

    Args:
        prompt: 音乐风格描述
        api_key: MiniMax API Key
        lyrics: 歌词（可选，为空则纯音乐）
    """
    if not api_key:
        logger.error("未配置 MINIMAX_API_KEY，无法生成音乐")
        return None

    client = _get_client()
    payload = {
        "model": "music-2.5+",
        "prompt": prompt,
        "audio_setting": {
            "sample_rate": 32000,
            "bitrate": 128000,
            "format": "mp3",
        },
    }
    if lyrics:
        payload["lyrics"] = lyrics
    else:
        payload["is_instrumental"] = True

    try:
        response = await client.post(
            "https://api.minimaxi.com/v1/music_generation",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=120.0,
        )
        response.raise_for_status()
        data = response.json()
        logger.debug(f"MiniMax 音乐生成响应: {str(data)[:500]}")

        # 检查 base_resp 错误
        base_resp = data.get("base_resp", {})
        if base_resp.get("status_code", 0) != 0:
            logger.error(f"MiniMax 音乐生成业务错误: {base_resp}")
            return None

        # data.audio 是 hex 编码的音频数据
        audio_hex = (data.get("data") or {}).get("audio")
        if not audio_hex:
            logger.error(f"MiniMax 音乐生成响应缺少 audio 字段: {str(data)[:500]}")
            return None

        return bytes.fromhex(audio_hex)
    except Exception as e:
        logger.error(f"MiniMax 音乐生成失败: {type(e).__name__}: {e}")
        return None
