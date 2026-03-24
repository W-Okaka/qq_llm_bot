"""
MiniMax 生图模块 - 调用 Image-01 API 生成图片
"""
import base64

from nonebot.log import logger

from .api import _get_client


async def generate_image(prompt: str, api_key: str, aspect_ratio: str = "1:1") -> bytes | None:
    """调用 MiniMax Image-01 生成图片，返回图片 bytes，失败返回 None"""
    if not api_key:
        logger.error("未配置 MINIMAX_API_KEY，无法生图")
        return None

    client = _get_client()
    try:
        response = await client.post(
            "https://api.minimaxi.com/v1/image_generation",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "image-01",
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
                "response_format": "base64",
                "n": 1,
            },
            timeout=120.0,
        )
        response.raise_for_status()
        data = response.json()
        # image_base64 是列表，取第一张
        image_list = data["data"]["image_base64"]
        if isinstance(image_list, list):
            b64_str = image_list[0]
        else:
            b64_str = image_list
        return base64.b64decode(b64_str)
    except Exception as e:
        logger.error(f"MiniMax 生图失败: {e}")
        return None
