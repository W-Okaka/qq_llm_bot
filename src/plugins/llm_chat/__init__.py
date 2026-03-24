"""
LLM 对话插件 - 入口模块
负责：配置读取、生命周期管理、消息路由
"""
import asyncio
import base64
import itertools
import os
import random
import re
from datetime import datetime

import httpx
from nonebot import on_message, get_driver
from nonebot.log import logger
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, PrivateMessageEvent, GroupMessageEvent, MessageSegment

from .api import call_llm_api, close_client
from .ban import parse_user_ban_intent, remove_ban_command
from .config import get_active_provider, get_provider_by_id, set_active_provider, PROVIDERS
from .image import generate_image
from .voice import generate_voice
from .video import generate_video
from .music import generate_music
from .history import (
    session_histories, processed_msg_ids,
    load_histories, get_session_history, build_messages,
    fetch_offline_history, mark_dirty, do_save, periodic_save_task,
    _is_junk_message, _simplify_cq_message,
)
from .memory import (
    load_memory, save_memory, migrate_legacy_memory,
    build_summarize_prompt, MEMORY_TRIGGER, MEMORY_BATCH,
)
from .search import tavily_search
from .skill_router import (
    classify_intent, extract_draw_prompt, extract_ban_info, extract_search_query,
    extract_voice_text, extract_video_prompt, extract_music_info,
)
from . import admin_routes  # noqa: F401  Admin 面板路由注册

# 获取配置
driver = get_driver()

# 主人 QQ 号（只有主人的禁言命令才会执行）
MASTER_QQ = 2199954840


# ============ 便捷 API 调用封装 ============

async def _call_api(messages, max_tokens=400) -> str:
    """封装 API 调用，从 config 获取当前活跃 provider 配置"""
    provider = get_active_provider()
    return await call_llm_api(
        messages,
        api_key=provider["api_key"],
        api_url=provider["api_url"],
        model=provider["model"],
        api_type=provider["api_type"],
        max_tokens=max_tokens,
    )


async def _call_api_with_provider(provider_id: str, messages, max_tokens=400) -> str:
    """指定 provider 调用 LLM，不依赖全局 active_provider"""
    provider = get_provider_by_id(provider_id)
    return await call_llm_api(
        messages,
        api_key=provider["api_key"],
        api_url=provider["api_url"],
        model=provider["model"],
        api_type=provider["api_type"],
        max_tokens=max_tokens,
    )


# ============ 生命周期 ============

@driver.on_startup
async def startup():
    """启动时加载历史记录并开启定时保存"""
    load_histories()
    migrate_legacy_memory()
    provider = get_active_provider()
    logger.info(f"启动完成，已加载 {len(session_histories)} 个会话的历史记录")
    logger.info(f"当前模型: {provider['name']} ({provider['id']})")
    for session_id, history in session_histories.items():
        logger.info(f"  - {session_id}: {len(history)} 条消息")
    asyncio.create_task(periodic_save_task())


@driver.on_shutdown
async def shutdown():
    """关闭时保存历史记录并关闭 HTTP 客户端"""
    do_save()
    logger.info(f"关闭时已保存 {len(session_histories)} 个会话的历史记录")
    await close_client()


@driver.on_bot_connect
async def on_connect(bot: Bot):
    """机器人连接时拉取离线期间的群聊历史"""
    await fetch_offline_history(bot)


# ============ QQ 命令处理 ============

_SWITCH_RE = re.compile(r"^切换模型\s*(\S+)$")


def _handle_model_command(raw_message: str, user_id: int) -> str | None:
    """处理模型相关命令，返回回复文本；非命令返回 None"""
    if user_id != MASTER_QQ:
        return None

    if raw_message == "模型列表":
        provider = get_active_provider()
        lines = ["当前可用模型："]
        for pid, p in PROVIDERS.items():
            marker = " ← 当前" if pid == provider["id"] else ""
            lines.append(f"  {pid} - {p['name']}{marker}")
        lines.append("\n发送「切换模型 <id>」切换")
        return "\n".join(lines)

    m = _SWITCH_RE.match(raw_message)
    if m:
        target = m.group(1).strip().lower()
        if target not in PROVIDERS:
            return f"未知模型：{target}\n可用：{', '.join(PROVIDERS.keys())}"
        set_active_provider(target)
        return f"已切换到 {PROVIDERS[target]['name']}"

    return None


async def _find_user_qq(bot: Bot, group_id: int, target: str) -> int | None:
    """通过昵称/card 在群成员列表中查找 QQ 号"""
    if not target:
        return None
    # 如果 target 本身就是纯数字（QQ 号），直接返回
    if target.isdigit():
        return int(target)
    try:
        members = await bot.get_group_member_list(group_id=group_id)
    except Exception as e:
        logger.error(f"获取群成员列表失败: {e}")
        return None
    # 精确匹配 card 或 nickname
    for m in members:
        if m.get("card") == target or m.get("nickname") == target:
            return m["user_id"]
    # 模糊匹配（包含关系）
    target_lower = target.lower()
    for m in members:
        card = (m.get("card") or "").lower()
        nick = (m.get("nickname") or "").lower()
        if target_lower in card or target_lower in nick:
            return m["user_id"]
    return None


def _strip_trailing_period(text: str) -> str:
    """去掉末尾句号（中英文），保留其他标点"""
    return text.rstrip("。.")


# 匹配 LLM 模仿用户消息格式输出的前缀，如 "[03-13 14:43][小织]: "
_REPLY_PREFIX_RE = re.compile(r"^\[\d{2}-\d{2}\s+\d{2}:\d{2}\]\[[^\]]+\]:\s*")


async def _send_multi_messages(send_func, messages: list[str]):
    """逐条发送多条消息，间隔按长度计算（2~3秒）"""
    for i, msg in enumerate(messages):
        if not msg.strip():
            continue
        await send_func(_strip_trailing_period(msg))
        if i < len(messages) - 1:
            # 按消息长度线性插值：短消息 2s，长消息(≥30字) 3s
            delay = 2.0 + min(len(msg), 30) / 30.0
            await asyncio.sleep(delay)


# ============ 群聊技能处理 ============

async def _handle_group_skill(bot: Bot, event: GroupMessageEvent, raw_message: str, session_id: str, history):
    """群聊技能路由：classify_intent → 分发执行"""
    recent_context = "\n".join(msg["content"] for msg in list(history)[-8:])
    intent = await classify_intent(raw_message, recent_context)
    logger.info(f"群聊意图分类: {intent} | 消息: {raw_message[:50]}")

    if intent == "draw":
        await _skill_draw(bot, event, raw_message, session_id, history)
    elif intent == "ban":
        await _skill_ban(bot, event, raw_message, session_id, history)
    elif intent == "search":
        await _skill_search(bot, event, raw_message, session_id, history)
    elif intent == "voice":
        await _skill_voice(bot, event, raw_message, session_id, history)
    elif intent == "video":
        await llm_chat.send("不会做视频")
    elif intent == "music":
        await _skill_music(bot, event, raw_message, session_id, history)
    else:
        await _skill_chat(bot, event, session_id, history)



async def _skill_chat(bot: Bot, event: GroupMessageEvent, session_id: str, history):
    """chat 技能：用 MiniMax 生成自然回复"""
    messages = build_messages(session_id)

    try:
        reply = await _call_api_with_provider("minimax", messages, max_tokens=400)
    except Exception as e:
        logger.error(f"群聊 chat 技能调用失败: {type(e).__name__}: {e}")
        reply = "脑子转不动了，等下再找我吧~"

    reply = remove_ban_command(reply)
    # 清理 LLM 可能模仿的时间戳前缀
    reply = _REPLY_PREFIX_RE.sub("", reply)
    history.append({"role": "assistant", "content": reply})

    # 多段拆发：按标点拆分
    segments = [p.strip() for p in re.split(r"(?<=[。！？!?])", reply) if p.strip()]
    if len(segments) <= 1 and len(reply) <= 30:
        # 短回复：尝试语音发送，失败降级文字
        api_key = os.environ.get("MINIMAX_API_KEY", "")
        try:
            audio_bytes = await generate_voice(_strip_trailing_period(reply), api_key)
        except Exception:
            audio_bytes = None
        if audio_bytes:
            b64_str = base64.b64encode(audio_bytes).decode()
            await bot.send_group_msg(
                group_id=event.group_id,
                message=MessageSegment.record(f"base64://{b64_str}"),
            )
        else:
            await llm_chat.send(_strip_trailing_period(reply))
    elif len(segments) > 1:
        await _send_multi_messages(llm_chat.send, segments)
    else:
        await llm_chat.send(_strip_trailing_period(reply))


async def _skill_search(bot: Bot, event: GroupMessageEvent, raw_message: str, session_id: str, history):
    """search 技能：提取关键词 → Tavily 搜索 → MiniMax 生成回复"""
    # 提取搜索关键词
    query = await extract_search_query(raw_message)
    logger.info(f"搜索关键词: {query}")

    # 调 Tavily 搜索
    api_key = os.environ.get("TAVILY_API_KEY", "")
    results = await tavily_search(query, api_key)

    if not results:
        # 搜索失败，降级为普通聊天
        logger.warning("搜索无结果，降级为 chat")
        await _skill_chat(bot, event, session_id, history)
        return

    # 将搜索结果格式化为上下文
    search_context = "\n\n".join(
        f"【{r['title']}】\n{r['content']}\n来源: {r['url']}"
        for r in results
    )

    # 构建消息：搜索结果 + 用户原始问题
    messages = build_messages(session_id)
    messages.append({
        "role": "user",
        "content": f"以下是关于用户问题的搜索结果，请根据搜索结果用自然语言回答用户的问题。"
                   f"不需要列出来源链接，直接回答即可。\n\n"
                   f"搜索结果：\n{search_context}\n\n"
                   f"用户问题：{raw_message}",
    })

    try:
        reply = await _call_api_with_provider("minimax", messages, max_tokens=600)
    except Exception as e:
        logger.error(f"搜索回复生成失败: {type(e).__name__}: {e}")
        reply = "搜到了一些信息但没整理好，稍后再试吧~"

    reply = remove_ban_command(reply)
    reply = _REPLY_PREFIX_RE.sub("", reply)
    history.append({"role": "assistant", "content": reply})

    # 多段拆发
    segments = [p.strip() for p in re.split(r"(?<=[。！？!?])", reply) if p.strip()]
    if len(segments) > 1:
        await _send_multi_messages(llm_chat.send, segments)
    else:
        await llm_chat.send(_strip_trailing_period(reply))


async def _skill_draw(bot: Bot, event: GroupMessageEvent, raw_message: str, session_id: str, history):
    """draw 技能：提取 prompt → 后台生图 → 发送，不阻塞聊天"""
    # 先回复一句，然后后台生图
    await llm_chat.send("在画了在画了，等我一下~")

    # 构建上下文：最近聊天 + 长期记忆（人物外貌等）
    recent = list(history)[-10:]
    chat_context = "\n".join(msg["content"] for msg in recent)
    memory_text = load_memory(session_id)
    if memory_text.strip():
        chat_context = f"[长期记忆]\n{memory_text}\n\n[最近聊天]\n{chat_context}"

    # 提取/优化画图 prompt（很快，几百ms）
    draw_prompt = await extract_draw_prompt(raw_message, chat_context)
    logger.info(f"画图 prompt: {draw_prompt}")

    # 后台执行生图，不阻塞后续消息处理
    async def _do_generate():
        api_key = os.environ.get("MINIMAX_API_KEY", "")
        try:
            img_bytes = await generate_image(draw_prompt, api_key)
        except Exception as e:
            logger.error(f"生图异常: {e}")
            img_bytes = None

        if img_bytes:
            b64_str = base64.b64encode(img_bytes).decode()
            await bot.send_group_msg(
                group_id=event.group_id,
                message=MessageSegment.text("画好啦~") + MessageSegment.image(f"base64://{b64_str}"),
            )
            history.append({"role": "assistant", "content": f"[画了一张图：{draw_prompt}]"})
        else:
            await bot.send_group_msg(
                group_id=event.group_id,
                message="图片生成失败了，稍后再试吧~",
            )
            history.append({"role": "assistant", "content": "[画图失败]"})
        mark_dirty()

    asyncio.create_task(_do_generate())


async def _skill_ban(bot: Bot, event: GroupMessageEvent, raw_message: str, session_id: str, history):
    """ban 技能：权限检查 → 提取禁言信息 → 执行"""
    if event.user_id != MASTER_QQ:
        # 非主人触发禁言意图，当普通聊天处理
        await _skill_chat(bot, event, session_id, history)
        return

    # 先尝试现有的精确解析（@某人 禁言 X分钟）
    ban_intent = parse_user_ban_intent(raw_message)
    if ban_intent:
        action, target_qq, duration = ban_intent
        try:
            await bot.set_group_ban(
                group_id=event.group_id, user_id=target_qq, duration=duration
            )
            if action == 'unban' or duration == 0:
                await llm_chat.send(f"已解禁 {target_qq}")
            else:
                mins = duration // 60 or 1
                await llm_chat.send(f"已禁言 {target_qq} {mins}分钟")
            history.append({"role": "assistant", "content": "[执行了禁言]"})
        except Exception as e:
            await llm_chat.send(f"禁言执行失败：{e}")
        return

    # 用 DeepSeek 提取禁言信息（自然语言描述的情况）
    # 清理 CQ 码，只保留纯文本给 DeepSeek 解析
    clean_msg = re.sub(r'\[CQ:[^\]]+\]', '', raw_message).strip()
    ban_info = await extract_ban_info(clean_msg)
    if not ban_info:
        await llm_chat.send("没搞懂要禁谁，再说一次？")
        return

    target_name, duration_minutes = ban_info
    target_qq = await _find_user_qq(bot, event.group_id, target_name)
    if not target_qq:
        await llm_chat.send(f"群里没找到「{target_name}」这个人")
        return

    duration_seconds = duration_minutes * 60
    try:
        await bot.set_group_ban(
            group_id=event.group_id, user_id=target_qq, duration=duration_seconds
        )
        if duration_minutes > 0:
            await llm_chat.send(f"已把{target_name}关小黑屋{duration_minutes}分钟")
        else:
            await llm_chat.send(f"已给{target_name}解禁了")
        history.append({"role": "assistant", "content": "[执行了禁言]"})
    except Exception as e:
        await llm_chat.send(f"禁言执行失败：{e}")


async def _skill_voice(bot: Bot, event: GroupMessageEvent, raw_message: str, session_id: str, history):
    """voice 技能：提取文本 → TTS → 发送语音"""
    text = await extract_voice_text(raw_message)
    logger.info(f"语音文本: {text[:50]}")

    api_key = os.environ.get("MINIMAX_API_KEY", "")
    try:
        audio_bytes = await generate_voice(text, api_key)
    except Exception as e:
        logger.error(f"语音生成异常: {e}")
        audio_bytes = None

    if audio_bytes:
        b64_str = base64.b64encode(audio_bytes).decode()
        await bot.send_group_msg(
            group_id=event.group_id,
            message=MessageSegment.record(f"base64://{b64_str}"),
        )
        history.append({"role": "assistant", "content": f"[语音朗读：{text[:30]}]"})
    else:
        await llm_chat.send("语音生成失败了，稍后再试吧~")
        history.append({"role": "assistant", "content": "[语音生成失败]"})
    mark_dirty()


async def _skill_video(bot: Bot, event: GroupMessageEvent, raw_message: str, session_id: str, history):
    """video 技能：提取 prompt → 后台生成视频 → 发送"""
    await llm_chat.send("在生成了，视频需要一点时间，稍等~")

    video_prompt = await extract_video_prompt(raw_message)
    logger.info(f"视频 prompt: {video_prompt}")

    async def _do_generate():
        api_key = os.environ.get("MINIMAX_API_KEY", "")
        try:
            video_url = await generate_video(video_prompt, api_key)
        except Exception as e:
            logger.error(f"视频生成异常: {e}")
            video_url = None

        if video_url:
            await bot.send_group_msg(
                group_id=event.group_id,
                message=MessageSegment.video(video_url),
            )
            history.append({"role": "assistant", "content": f"[生成了视频：{video_prompt}]"})
        else:
            await bot.send_group_msg(
                group_id=event.group_id,
                message="视频生成失败了，稍后再试吧~",
            )
            history.append({"role": "assistant", "content": "[视频生成失败]"})
        mark_dirty()

    asyncio.create_task(_do_generate())


async def _skill_music(bot: Bot, event: GroupMessageEvent, raw_message: str, session_id: str, history):
    """music 技能：提取信息 → 生成音乐 → 发送"""
    await llm_chat.send("在创作了，等我一下~")

    style, lyrics = await extract_music_info(raw_message)
    logger.info(f"音乐风格: {style}, 歌词: {lyrics[:30] if lyrics else '(纯音乐)'}")

    async def _do_generate():
        api_key = os.environ.get("MINIMAX_API_KEY", "")
        try:
            audio_bytes = await generate_music(style, api_key, lyrics=lyrics)
        except Exception as e:
            logger.error(f"音乐生成异常: {e}")
            audio_bytes = None

        if audio_bytes:
            b64_str = base64.b64encode(audio_bytes).decode()
            await bot.send_group_msg(
                group_id=event.group_id,
                message=MessageSegment.record(f"base64://{b64_str}"),
            )
            history.append({"role": "assistant", "content": f"[创作了音乐：{style}]"})
        else:
            await bot.send_group_msg(
                group_id=event.group_id,
                message="音乐生成失败了，稍后再试吧~",
            )
            history.append({"role": "assistant", "content": "[音乐生成失败]"})
        mark_dirty()

    asyncio.create_task(_do_generate())


# ============ 消息处理 ============

llm_chat = on_message(priority=10, block=False)


@llm_chat.handle()
async def handle_chat(bot: Bot, event: MessageEvent):
    raw_message = str(event.message).strip()
    if not raw_message:
        return

    is_private = isinstance(event, PrivateMessageEvent)
    is_group = isinstance(event, GroupMessageEvent)

    # 某些 OneBot 实现下 event.is_tome() 可能不稳定，补一层 CQ @ 兜底
    raw_cq_message = str(event.message)
    bot_self_id = str(getattr(bot, "self_id", ""))
    is_tome_flag = event.is_tome() if is_group else False
    is_tome_fallback = is_group and bool(bot_self_id) and f"[CQ:at,qq={bot_self_id}]" in raw_cq_message

    if is_group:
        session_id = f"group_{event.group_id}"
        sender_name = event.sender.card or event.sender.nickname or str(event.user_id)
    else:
        session_id = f"private_{event.user_id}"
        sender_name = event.sender.nickname or str(event.user_id)

    timestamp = datetime.now().strftime("%m-%d %H:%M")
    content = f"[{timestamp}][{sender_name}(QQ:{event.user_id})]: {raw_message}"

    history = get_session_history(session_id)

    should_reply = is_private or is_tome_flag or is_tome_fallback
    logger.info(
        f"收到消息: {'群聊' if is_group else '私聊'} | user={event.user_id}"
        f" | should_reply={should_reply}"
        f" | is_tome={is_tome_flag if is_group else '-'}"
        f" | fallback_at={is_tome_fallback if is_group else '-'}"
        f" | text={raw_message[:80]}"
    )

    if is_group or should_reply:
        # 过滤垃圾消息（纯转发、纯表情等），不存入历史
        if not _is_junk_message(content):
            # 简化 CQ 码（图片→[图片] 等）
            content = _simplify_cq_message(content)
            history.append({"role": "user", "content": content})
        if hasattr(event, "message_id") and event.message_id:
            processed_msg_ids.add(event.message_id)

    # 检查是否需要总结记忆（安全序：先总结成功再移除消息）
    if len(history) >= MEMORY_TRIGGER:
        batch = list(itertools.islice(history, MEMORY_BATCH))
        existing = load_memory(session_id)
        summarize_msgs = build_summarize_prompt(batch, existing)
        try:
            new_memory = await _call_api(summarize_msgs, max_tokens=800)
            save_memory(session_id, new_memory)
            pop_count = min(MEMORY_BATCH, len(history))
            for _ in range(pop_count):
                history.popleft()
            logger.info(f"记忆总结完成，已处理 {pop_count} 条旧消息")
        except Exception as e:
            logger.error(f"记忆总结失败: {e}")

    if not should_reply:
        logger.info("本条消息未触发回复（群聊未@机器人）")
        mark_dirty()
        return

    # 检查模型命令（主人专用）
    cmd_reply = _handle_model_command(raw_message, event.user_id)
    if cmd_reply is not None:
        await llm_chat.send(cmd_reply)
        mark_dirty()
        return

    # 群聊：技能路由模式
    if is_group:
        await _handle_group_skill(bot, event, raw_message, session_id, history)
        mark_dirty()
        return

    # 私聊：直接对话
    messages = build_messages(session_id)

    try:
        reply = await _call_api(messages, max_tokens=400)
    except httpx.TimeoutException:
        logger.error("私聊 API 请求超时")
        reply = "网络有点慢，等下再试试吧~"
    except httpx.HTTPStatusError as e:
        logger.error(f"私聊 API 状态码异常: {e.response.status_code} | {e.response.text[:200]}")
        reply = "服务暂时不可用，稍后再试~"
    except httpx.RequestError as e:
        logger.error(f"私聊网络请求失败: {type(e).__name__}: {e}")
        reply = "网络出了点问题，稍后再试~"
    except Exception as e:
        logger.error(f"私聊 API 调用异常: {type(e).__name__}: {e}")
        reply = "出了点小问题，稍后再试~"

    reply = remove_ban_command(reply)
    reply = _REPLY_PREFIX_RE.sub("", reply)

    history.append({"role": "assistant", "content": reply})
    mark_dirty()

    await llm_chat.send(_strip_trailing_period(reply))
