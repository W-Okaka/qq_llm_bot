"""
LLM API 调用模块 - 封装 HTTP 客户端管理和 API 调用
支持 OpenAI 和 Anthropic 两种 API 格式
"""
import json
from typing import List, Dict

import httpx
from nonebot.log import logger


# HTTP 客户端复用
_http_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=60.0)
    return _http_client


async def close_client():
    """关闭 HTTP 客户端"""
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()


def _extract_error_detail(response: httpx.Response) -> str:
    """提取 API 错误详情，优先返回结构化 message"""
    try:
        data = response.json()
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict) and err.get("message"):
                return str(err["message"])
            if data.get("message"):
                return str(data["message"])
    except Exception:
        pass
    return response.text[:500].strip() or "未知错误"


def _raise_if_http_error(response: httpx.Response) -> None:
    """统一处理 HTTP 错误，补充可读错误信息"""
    if response.status_code < 400:
        return
    detail = _extract_error_detail(response)
    detail_lower = detail.lower()
    if "insufficient balance" in detail_lower or "(1008)" in detail_lower or "1008" in detail_lower:
        raise ValueError("MiniMax 余额不足（1008），请充值后重试")
    raise ValueError(f"LLM API 请求失败（HTTP {response.status_code}）：{detail}")


async def _call_openai(
    messages: List[Dict[str, str]],
    *,
    api_key: str,
    api_url: str,
    model: str,
    max_tokens: int,
) -> str:
    """OpenAI 兼容格式调用"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.8,
        "max_tokens": max_tokens,
    }
    client = _get_client()
    response = await client.post(api_url, headers=headers, json=payload)
    _raise_if_http_error(response)
    raw_text = response.text
    if not raw_text.strip():
        raise ValueError("API 返回空响应")
    try:
        data = response.json()
    except json.JSONDecodeError as e:
        raise ValueError(f"API 返回非 JSON 格式: {raw_text[:500]}") from e
    return data["choices"][0]["message"]["content"]


async def _call_anthropic(
    messages: List[Dict[str, str]],
    *,
    api_key: str,
    api_url: str,
    model: str,
    max_tokens: int,
) -> str:
    """Anthropic 兼容格式调用"""
    # 从 messages 中提取 system prompt，并合并连续同角色消息
    system_text = ""
    chat_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system_text = msg["content"]
        elif chat_messages and chat_messages[-1]["role"] == msg["role"]:
            # Anthropic API 要求角色交替，合并连续同角色消息
            chat_messages[-1]["content"] += "\n" + msg["content"]
        else:
            chat_messages.append({"role": msg["role"], "content": msg["content"]})

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": chat_messages,
        "max_tokens": max_tokens,
    }
    if system_text:
        payload["system"] = system_text

    client = _get_client()
    response = await client.post(api_url, headers=headers, json=payload)
    _raise_if_http_error(response)
    raw_text = response.text
    if not raw_text.strip():
        raise ValueError("API 返回空响应")
    try:
        data = response.json()
    except json.JSONDecodeError as e:
        raise ValueError(f"API 返回非 JSON 格式: {raw_text[:500]}") from e

    # 从 content 数组中提取 text block（跳过 thinking block）
    content_blocks = data.get("content", [])
    for block in content_blocks:
        if block.get("type") == "text":
            return block["text"]
    # 记录实际响应以便排查
    stop_reason = data.get("stop_reason", "unknown")
    logger.error(f"Anthropic API 未找到 text block，stop_reason={stop_reason}，content={content_blocks!r}")
    raise ValueError(f"API 响应中未找到 text 内容（stop_reason={stop_reason}）")


async def call_llm_api(
    messages: List[Dict[str, str]],
    *,
    api_key: str,
    api_url: str,
    model: str,
    api_type: str = "openai",
    max_tokens: int = 400,
) -> str:
    """调用 LLM API，根据 api_type 分发到对应实现"""
    if not api_key:
        raise ValueError("未配置 API Key，请在 .env 文件中设置")

    if api_type == "anthropic":
        return await _call_anthropic(
            messages, api_key=api_key, api_url=api_url, model=model, max_tokens=max_tokens,
        )
    else:
        return await _call_openai(
            messages, api_key=api_key, api_url=api_url, model=model, max_tokens=max_tokens,
        )
