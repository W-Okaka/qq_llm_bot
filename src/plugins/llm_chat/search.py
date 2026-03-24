"""
联网搜索模块 - 使用 Tavily Search API
复用 api.py 的 httpx 客户端
"""
from nonebot.log import logger

from .api import _get_client


async def tavily_search(query: str, api_key: str, max_results: int = 3) -> list[dict] | None:
    """调用 Tavily Search API，返回搜索结果列表，失败返回 None

    返回格式: [{"title": ..., "url": ..., "content": ...}, ...]
    """
    if not api_key:
        logger.warning("未配置 TAVILY_API_KEY，跳过搜索")
        return None

    client = _get_client()
    try:
        resp = await client.post(
            "https://api.tavily.com/search",
            json={
                "query": query,
                "api_key": api_key,
                "max_results": max_results,
                "search_depth": "basic",
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        return [
            {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", "")}
            for r in results
        ]
    except Exception as e:
        logger.error(f"Tavily 搜索失败: {type(e).__name__}: {e}")
        return None
