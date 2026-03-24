"""
MiniMax 视频生成模块 - 调用 T2V-01 文生视频 API（异步任务）
"""
import asyncio

from nonebot.log import logger

from .api import _get_client

# 轮询配置
_POLL_INTERVAL = 5  # 秒
_POLL_TIMEOUT = 300  # 5 分钟


async def generate_video(prompt: str, api_key: str) -> str | None:
    """调用 MiniMax T2V-01 生成视频，返回视频下载 URL，失败返回 None

    流程：提交任务 → 轮询状态 → 获取下载链接
    """
    if not api_key:
        logger.error("未配置 MINIMAX_API_KEY，无法生成视频")
        return None

    client = _get_client()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # 1. 提交生成任务
    try:
        resp = await client.post(
            "https://api.minimaxi.com/v1/video_generation",
            headers=headers,
            json={
                "model": "T2V-01",
                "prompt": prompt,
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        task_id = data.get("task_id")
        if not task_id:
            logger.error(f"MiniMax 视频生成未返回 task_id: {data}")
            return None
        logger.info(f"视频生成任务已提交: task_id={task_id}")
    except Exception as e:
        logger.error(f"MiniMax 视频生成提交失败: {type(e).__name__}: {e}")
        return None

    # 2. 轮询任务状态
    elapsed = 0
    file_id = None
    while elapsed < _POLL_TIMEOUT:
        await asyncio.sleep(_POLL_INTERVAL)
        elapsed += _POLL_INTERVAL
        try:
            resp = await client.get(
                "https://api.minimaxi.com/v1/query/video_generation",
                params={"task_id": task_id},
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status")
            if status == "Success":
                file_id = data.get("file_id")
                break
            elif status in ("Failed", "failed"):
                logger.error(f"视频生成任务失败: {data}")
                return None
            # Processing / Queueing 继续等待
        except Exception as e:
            logger.warning(f"视频生成轮询异常: {e}")

    if not file_id:
        logger.error(f"视频生成超时 ({_POLL_TIMEOUT}s)")
        return None

    # 3. 获取下载链接
    try:
        resp = await client.get(
            "https://api.minimaxi.com/v1/files/retrieve",
            params={"file_id": file_id},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        download_url = data.get("file", {}).get("download_url")
        if not download_url:
            logger.error(f"MiniMax 文件检索未返回 download_url: {data}")
            return None
        return download_url
    except Exception as e:
        logger.error(f"MiniMax 文件检索失败: {type(e).__name__}: {e}")
        return None
