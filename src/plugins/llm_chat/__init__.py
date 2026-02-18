import json
import re
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Dict, Deque, List, Set

import httpx
from nonebot import on_message, get_driver
from nonebot.log import logger
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, PrivateMessageEvent, GroupMessageEvent

from .config import get_system_prompt
from .memory import (
    load_memory, save_memory, get_memory_for_prompt,
    build_summarize_prompt, MEMORY_TRIGGER, MEMORY_BATCH,
)

# 获取配置
driver = get_driver()
config = driver.config

# API 配置
LLM_API_KEY: str = getattr(config, "llm_api_key", "")
LLM_API_URL: str = getattr(config, "llm_api_url", "https://api.deepseek.com/chat/completions")
LLM_MODEL: str = getattr(config, "llm_model", "deepseek-chat")

# 调试：打印实际加载的配置
print(f"[LLM_CHAT] 配置加载完成:")
print(f"  LLM_API_URL = {LLM_API_URL}")
print(f"  LLM_MODEL = {LLM_MODEL}")
print(f"  LLM_API_KEY = {LLM_API_KEY[:10]}..." if LLM_API_KEY else "  LLM_API_KEY = (未设置)")

# 主人 QQ 号（只有主人的禁言命令才会执行）
MASTER_QQ = 2199954840

# 上下文记忆：限制最大 100 条消息
MAX_HISTORY = 100
session_histories: Dict[str, Deque[Dict[str, str]]] = {}

# 持久化存储路径
DATA_DIR = Path(__file__).parent / "data"
HISTORY_FILE = DATA_DIR / "chat_history.json"
MSGID_FILE = DATA_DIR / "processed_msg_ids.json"

# 已处理的消息 ID（用于去重）
processed_msg_ids: Set[int] = set()


def parse_ban_command(text: str):
    """解析禁言指令，返回 (action, user_id, duration) 或 None"""
    ban_match = re.search(r'\[BAN:(\d+):(\d+)\]', text)
    if ban_match:
        return ('ban', int(ban_match.group(1)), int(ban_match.group(2)))

    unban_match = re.search(r'\[UNBAN:(\d+)\]', text)
    if unban_match:
        return ('unban', int(unban_match.group(1)), 0)

    return None


def remove_ban_command(text: str) -> str:
    """从文本中移除禁言指令标记"""
    text = re.sub(r'\[BAN:\d+:\d+\]', '', text)
    text = re.sub(r'\[UNBAN:\d+\]', '', text)
    return text.strip()


def parse_user_ban_intent(message: str):
    """
    解析用户消息中的禁言意图
    返回 (action, target_qq, duration_seconds) 或 None
    """
    # 提取 @ 的 QQ 号（排除 @ 机器人自己）
    at_matches = re.findall(r'\[CQ:at,qq=(\d+)\]', message)
    if not at_matches:
        return None

    # 检测解禁关键词
    if '解禁' in message or '解除禁言' in message:
        return ('unban', int(at_matches[-1]), 0)

    # 检测禁言关键词
    if '禁言' in message:
        # 提取时长，默认10分钟
        duration = 600
        # 匹配 "X分钟"、"X小时"、"X天"、"X秒"
        time_match = re.search(r'(\d+)\s*(分钟|小时|天|秒)', message)
        if time_match:
            num = int(time_match.group(1))
            unit = time_match.group(2)
            if unit == '秒':
                duration = num
            elif unit == '分钟':
                duration = num * 60
            elif unit == '小时':
                duration = num * 3600
            elif unit == '天':
                duration = num * 86400
        # 使用最后一个 @ 的人（排除可能 @ 机器人的情况）
        return ('ban', int(at_matches[-1]), duration)

    return None


def save_histories():
    """保存所有对话历史到 JSON 文件"""
    DATA_DIR.mkdir(exist_ok=True)
    data = {k: list(v) for k, v in session_histories.items()}
    HISTORY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # 只保留最近 100 个消息 ID，避免文件过大
    recent_ids = sorted(processed_msg_ids)[-100:]
    MSGID_FILE.write_text(json.dumps(recent_ids), encoding="utf-8")


def load_histories():
    """从 JSON 文件加载对话历史"""
    global processed_msg_ids
    print(f"[LLM_CHAT] 正在检查历史记录文件: {HISTORY_FILE}")
    if HISTORY_FILE.exists():
        print(f"[LLM_CHAT] 历史记录文件存在，大小: {HISTORY_FILE.stat().st_size} bytes")
        try:
            data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            for session_id, messages in data.items():
                session_histories[session_id] = deque(messages, maxlen=MAX_HISTORY)
            print(f"[LLM_CHAT] 成功加载 {len(data)} 个会话的历史记录")
        except (json.JSONDecodeError, IOError) as e:
            print(f"[LLM_CHAT] 加载历史记录失败: {e}")
    else:
        print(f"[LLM_CHAT] 历史记录文件不存在，将从空历史开始")
    if MSGID_FILE.exists():
        try:
            processed_msg_ids = set(json.loads(MSGID_FILE.read_text(encoding="utf-8")))
            print(f"[LLM_CHAT] 成功加载 {len(processed_msg_ids)} 个已处理的消息ID")
        except (json.JSONDecodeError, IOError) as e:
            print(f"[LLM_CHAT] 加载消息ID失败: {e}")


@driver.on_startup
async def startup():
    """启动时加载历史记录"""
    load_histories()
    print(f"[LLM_CHAT] 启动完成，已加载 {len(session_histories)} 个会话的历史记录")
    for session_id, history in session_histories.items():
        print(f"  - {session_id}: {len(history)} 条消息")


@driver.on_shutdown
async def shutdown():
    """关闭时保存历史记录"""
    save_histories()
    print(f"[LLM_CHAT] 关闭时已保存 {len(session_histories)} 个会话的历史记录")


async def fetch_offline_history(bot: Bot):
    """拉取离线期间的群聊历史"""
    # 从已保存的历史中获取群号列表
    group_ids = [
        int(sid.replace("group_", ""))
        for sid in session_histories.keys()
        if sid.startswith("group_")
    ]

    for group_id in group_ids:
        try:
            # 获取最近的群聊历史
            result = await bot.get_group_msg_history(group_id=group_id)
            messages = result.get("messages", [])

            session_id = f"group_{group_id}"
            history = get_session_history(session_id)

            # 按时间排序（旧消息在前）
            messages.sort(key=lambda m: m.get("time", 0))

            for msg in messages:
                msg_id = msg.get("message_id")
                if msg_id and msg_id in processed_msg_ids:
                    continue  # 跳过已处理的消息

                # 提取纯文本内容
                raw_message = msg.get("raw_message", "") or msg.get("message", "")
                if isinstance(raw_message, list):
                    # 如果是消息段列表，提取文本
                    raw_message = "".join(
                        seg.get("data", {}).get("text", "")
                        for seg in raw_message
                        if seg.get("type") == "text"
                    )

                if not raw_message.strip():
                    continue

                # 获取发送者信息
                sender = msg.get("sender", {})
                sender_name = sender.get("card") or sender.get("nickname") or str(sender.get("user_id", "未知"))
                user_id = sender.get("user_id", "未知")

                msg_time = msg.get("time", 0)
                if msg_time:
                    timestamp = datetime.fromtimestamp(msg_time).strftime("%m-%d %H:%M")
                else:
                    timestamp = "未知时间"
                content = f"[{timestamp}][{sender_name}(QQ:{user_id})]: {raw_message.strip()}"
                history.append({"role": "user", "content": content})

                if msg_id:
                    processed_msg_ids.add(msg_id)

        except Exception as e:
            logger.warning(f"拉取群 {group_id} 离线历史失败: {e}")

    save_histories()


@driver.on_bot_connect
async def on_connect(bot: Bot):
    """机器人连接时拉取离线期间的群聊历史"""
    await fetch_offline_history(bot)


def get_session_history(session_id: str) -> Deque[Dict[str, str]]:
    """获取会话的对话历史（私聊按 user_id，群聊按 group_id）"""
    if session_id not in session_histories:
        session_histories[session_id] = deque(maxlen=MAX_HISTORY)
    return session_histories[session_id]


def build_messages(session_id: str) -> List[Dict[str, str]]:
    """构建发送给 API 的消息列表（支持热重载提示词 + 长期记忆）"""
    memory_text = get_memory_for_prompt()
    system_content = get_system_prompt() + memory_text
    messages = [{"role": "system", "content": system_content}]
    history = get_session_history(session_id)

    history_list = list(history)
    # 只保留最近 2 条 assistant 消息，过滤掉更早的，减少回复同质化
    assistant_indices = [i for i, m in enumerate(history_list) if m["role"] == "assistant"]
    skip_indices = set(assistant_indices[:-2]) if len(assistant_indices) > 2 else set()

    for i, msg in enumerate(history_list):
        if i not in skip_indices:
            messages.append(msg)

    return messages


async def call_llm_api(messages: List[Dict[str, str]], max_tokens: int = 400) -> str:
    """调用 LLM API"""
    if not LLM_API_KEY:
        raise ValueError("未配置 LLM_API_KEY，请在 .env 文件中设置")

    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": 0.8,
        "max_tokens": max_tokens
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(LLM_API_URL, headers=headers, json=payload)
        response.raise_for_status()
        # 调试：打印原始响应
        raw_text = response.text
        if not raw_text.strip():
            raise ValueError("API 返回空响应")
        try:
            data = response.json()
        except json.JSONDecodeError as e:
            # 显示原始响应帮助诊断
            raise ValueError(f"API 返回非 JSON 格式: {raw_text[:500]}") from e
        return data["choices"][0]["message"]["content"]


# 消息响应器：私聊或群聊@机器人触发
llm_chat = on_message(priority=10, block=False)


@llm_chat.handle()
async def handle_chat(bot: Bot, event: MessageEvent):
    # 获取原始消息（保留 CQ 码，如 [CQ:at,qq=123456]）
    raw_message = str(event.message).strip()

    # 如果没有任何内容，跳过
    if not raw_message:
        return

    is_private = isinstance(event, PrivateMessageEvent)
    is_group = isinstance(event, GroupMessageEvent)

    # 计算 session_id：私聊按 user_id，群聊按 group_id
    if is_group:
        session_id = f"group_{event.group_id}"
        # 群聊消息带上发送者昵称（优先用群名片，否则用昵称）
        sender_name = event.sender.card or event.sender.nickname or str(event.user_id)
        # 使用原始消息（保留 @ 信息），包含 QQ 号以便 LLM 识别主人
        timestamp = datetime.now().strftime("%m-%d %H:%M")
        content = f"[{timestamp}][{sender_name}(QQ:{event.user_id})]: {raw_message}"
    else:
        session_id = f"private_{event.user_id}"
        sender_name = event.sender.nickname or str(event.user_id)
        timestamp = datetime.now().strftime("%m-%d %H:%M")
        content = f"[{timestamp}][{sender_name}(QQ:{event.user_id})]: {raw_message}"

    history = get_session_history(session_id)

    # 判断是否应该回复：私聊 或 群聊@机器人
    should_reply = is_private or event.is_tome()

    # 群聊：所有消息都记录到历史（无论是否@机器人）
    # 私聊：只有触发回复时才记录（因为私聊必定触发回复）
    if is_group or should_reply:
        history.append({"role": "user", "content": content})
        # 记录消息 ID 用于去重
        if hasattr(event, "message_id") and event.message_id:
            processed_msg_ids.add(event.message_id)

    # 检查是否需要总结记忆（任何会话达到阈值都触发）
    if len(history) >= MEMORY_TRIGGER:
        batch = [history.popleft() for _ in range(MEMORY_BATCH)]
        existing = load_memory()
        summarize_msgs = build_summarize_prompt(batch, existing)
        try:
            new_memory = await call_llm_api(summarize_msgs, max_tokens=800)
            save_memory(new_memory)
            print(f"[LLM_CHAT] 记忆总结完成，已处理 {MEMORY_BATCH} 条旧消息")
        except Exception as e:
            print(f"[LLM_CHAT] 记忆总结失败: {e}，恢复消息")
            for msg in reversed(batch):
                history.appendleft(msg)

    # 不需要回复时直接返回
    if not should_reply:
        save_histories()  # 群聊消息也要保存
        return

    # 检查主人的禁言命令（在调用 LLM 之前直接执行）
    ban_executed = False
    if is_group and event.user_id == MASTER_QQ:
        ban_intent = parse_user_ban_intent(raw_message)
        if ban_intent:
            action, target_qq, duration = ban_intent
            try:
                await bot.set_group_ban(
                    group_id=event.group_id,
                    user_id=target_qq,
                    duration=duration
                )
                ban_executed = True
            except Exception as e:
                await llm_chat.send(f"禁言执行失败喵：{e}")
                return

    # 构建完整消息列表（含历史）并调用 API
    messages = build_messages(session_id)

    try:
        reply = await call_llm_api(messages)
    except httpx.TimeoutException:
        reply = "错误：API 请求超时，请稍后重试"
    except httpx.HTTPStatusError as e:
        reply = f"错误：API 返回异常状态码 {e.response.status_code}\n响应内容：{e.response.text[:200]}"
    except httpx.RequestError as e:
        reply = f"错误：网络请求失败 - {type(e).__name__}: {str(e)}"
    except KeyError as e:
        reply = f"错误：API 响应格式异常，缺少字段 {e}"
    except Exception as e:
        reply = f"错误：{type(e).__name__}: {str(e)}"

    # 从回复中移除可能的禁言指令标记（以防 LLM 输出）
    reply = remove_ban_command(reply)

    # 将回复存入历史
    history.append({"role": "assistant", "content": reply})
    save_histories()

    # 发送消息
    await llm_chat.send(reply)
