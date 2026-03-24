"""
历史记录管理模块 - 会话历史的存储、加载、构建和定时保存
"""
import asyncio
import json
import re
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Dict, Deque, List, Set

from nonebot.log import logger
from nonebot.adapters.onebot.v11 import Bot

from .config import get_system_prompt
from .memory import get_memory_for_prompt

# 纯 CQ 码消息的正则（整条消息只有 CQ 码，没有有意义的文字）
_PURE_CQ_RE = re.compile(r"^(\[CQ:[^\]]+\]\s*)+$")
# 无意义的 CQ 类型（转发消息内容是 object Object，图片/语音等对 LLM 无意义）
_JUNK_CQ_TYPES = {"forward", "record", "video", "face", "poke"}


def _is_junk_message(content: str) -> bool:
    """判断消息内容是否对 LLM 无意义（应跳过不存入历史）"""
    # 提取 ]: 之后的实际消息体（去掉时间戳和发送者前缀）
    msg_body = content
    prefix_end = content.find("]: ")
    if prefix_end != -1:
        msg_body = content[prefix_end + 3:]

    msg_body = msg_body.strip()
    if not msg_body:
        return True

    # 整条消息只有 CQ 码
    if _PURE_CQ_RE.match(msg_body):
        # 检查是否全是无意义的 CQ 类型
        cq_types = re.findall(r"\[CQ:(\w+)", msg_body)
        if all(t in _JUNK_CQ_TYPES for t in cq_types):
            return True
        # forward 消息含 object Object 是垃圾
        if "forward" in cq_types and "object Object" in msg_body:
            return True

    return False


def _simplify_cq_message(content: str) -> str:
    """将 CQ 码简化为人类可读的描述"""
    # [CQ:image,...] → [图片]
    content = re.sub(r"\[CQ:image[^\]]*\]", "[图片]", content)
    # [CQ:record,...] → [语音]
    content = re.sub(r"\[CQ:record[^\]]*\]", "[语音]", content)
    # [CQ:video,...] → [视频]
    content = re.sub(r"\[CQ:video[^\]]*\]", "[视频]", content)
    # [CQ:face,...] → [表情]
    content = re.sub(r"\[CQ:face[^\]]*\]", "", content)
    # [CQ:forward,...] → [转发消息]
    content = re.sub(r"\[CQ:forward[^\]]*\]", "[转发消息]", content)
    # [CQ:reply,...] → 去掉（引用标记对 LLM 无意义）
    content = re.sub(r"\[CQ:reply[^\]]*\]", "", content)
    # [CQ:at,qq=xxx] 保留（有意义）
    return content.strip()

# 上下文记忆：限制最大 100 条消息
MAX_HISTORY = 100
session_histories: Dict[str, Deque[Dict[str, str]]] = {}

# 持久化存储路径
DATA_DIR = Path(__file__).parent / "data"
HISTORY_FILE = DATA_DIR / "chat_history.json"
MSGID_FILE = DATA_DIR / "processed_msg_ids.json"

# 已处理的消息 ID（用于去重）
processed_msg_ids: Set[int] = set()

# ============ 批量保存 ============
_save_dirty = False


def mark_dirty():
    global _save_dirty
    _save_dirty = True


async def periodic_save_task():
    """每 30 秒检查并保存"""
    global _save_dirty
    while True:
        await asyncio.sleep(30)
        if _save_dirty:
            _save_dirty = False
            await asyncio.to_thread(do_save)
            logger.debug("定时保存历史记录完成")


def do_save():
    """实际的磁盘写入（同步，在线程中运行）- 原子写入"""
    DATA_DIR.mkdir(exist_ok=True)
    data = {k: list(v) for k, v in session_histories.items()}
    # 原子写入：先写临时文件再 rename
    tmp = HISTORY_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(HISTORY_FILE)
    # 只保留最近 100 个消息 ID，避免文件过大
    recent_ids = sorted(processed_msg_ids)[-100:]
    MSGID_FILE.write_text(json.dumps(recent_ids), encoding="utf-8")


# ============ 加载/获取 ============

def load_histories():
    """从 JSON 文件加载对话历史"""
    global processed_msg_ids
    logger.info(f"正在检查历史记录文件: {HISTORY_FILE}")
    if HISTORY_FILE.exists():
        logger.info(f"历史记录文件存在，大小: {HISTORY_FILE.stat().st_size} bytes")
        try:
            data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            for session_id, messages in data.items():
                session_histories[session_id] = deque(messages, maxlen=MAX_HISTORY)
            logger.info(f"成功加载 {len(data)} 个会话的历史记录")
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"加载历史记录失败: {e}")
    else:
        logger.info("历史记录文件不存在，将从空历史开始")
    if MSGID_FILE.exists():
        try:
            processed_msg_ids = set(json.loads(MSGID_FILE.read_text(encoding="utf-8")))
            logger.info(f"成功加载 {len(processed_msg_ids)} 个已处理的消息ID")
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"加载消息ID失败: {e}")


def get_session_history(session_id: str) -> Deque[Dict[str, str]]:
    """获取会话的对话历史（私聊按 user_id，群聊按 group_id）"""
    if session_id not in session_histories:
        session_histories[session_id] = deque(maxlen=MAX_HISTORY)
    return session_histories[session_id]


def build_messages(session_id: str) -> List[Dict[str, str]]:
    """构建发送给 API 的消息列表（支持热重载提示词 + 长期记忆）"""
    memory_text = get_memory_for_prompt(session_id)
    base_prompt = get_system_prompt()
    system_content = base_prompt + memory_text
    messages = [{"role": "system", "content": system_content}]
    history = get_session_history(session_id)

    # 先过滤掉垃圾消息和无意义的 assistant 回复
    filtered = []
    for msg in history:
        if msg["role"] == "assistant":
            # 跳过 [已回复] 这类占位回复
            if msg["content"].strip() in ("[已回复]", ""):
                continue
        elif msg["role"] == "user":
            # 跳过纯 CQ 码垃圾消息
            if _is_junk_message(msg["content"]):
                continue
            # 简化 CQ 码（图片→[图片] 等）
            msg = {**msg, "content": _simplify_cq_message(msg["content"])}
        filtered.append(msg)

    # 只保留最近 2 条 assistant 消息，更早的直接跳过
    assistant_indices = [i for i, m in enumerate(filtered) if m["role"] == "assistant"]
    skip_indices = set(assistant_indices[:-2]) if len(assistant_indices) > 2 else set()

    for i, msg in enumerate(filtered):
        if i not in skip_indices:
            messages.append(msg)

    return messages


# ============ 离线历史拉取 ============

async def fetch_offline_history(bot: Bot):
    """拉取离线期间的群聊历史"""
    group_ids = [
        int(sid.replace("group_", ""))
        for sid in session_histories.keys()
        if sid.startswith("group_")
    ]

    for group_id in group_ids:
        try:
            result = await bot.get_group_msg_history(group_id=group_id)
            messages = result.get("messages", [])

            session_id = f"group_{group_id}"
            history = get_session_history(session_id)

            # 收集已有历史的内容指纹用于去重（防止 msg_id 过期后重复拉取）
            existing_contents = {msg["content"] for msg in history}

            messages.sort(key=lambda m: m.get("time", 0))

            for msg in messages:
                msg_id = msg.get("message_id")
                if msg_id and msg_id in processed_msg_ids:
                    continue

                raw_message = msg.get("raw_message", "") or msg.get("message", "")
                if isinstance(raw_message, list):
                    raw_message = "".join(
                        seg.get("data", {}).get("text", "")
                        for seg in raw_message
                        if seg.get("type") == "text"
                    )

                if not raw_message.strip():
                    continue

                sender = msg.get("sender", {})
                sender_name = sender.get("card") or sender.get("nickname") or str(sender.get("user_id", "未知"))
                user_id = sender.get("user_id", "未知")

                msg_time = msg.get("time", 0)
                if msg_time:
                    timestamp = datetime.fromtimestamp(msg_time).strftime("%m-%d %H:%M")
                else:
                    timestamp = "未知时间"
                content = f"[{timestamp}][{sender_name}(QQ:{user_id})]: {raw_message.strip()}"

                # 过滤垃圾消息（纯转发、纯表情等）
                if _is_junk_message(content):
                    if msg_id:
                        processed_msg_ids.add(msg_id)
                    continue

                # 简化 CQ 码
                content = _simplify_cq_message(content)

                # 基于内容去重
                if content in existing_contents:
                    if msg_id:
                        processed_msg_ids.add(msg_id)
                    continue

                history.append({"role": "user", "content": content})
                existing_contents.add(content)

                if msg_id:
                    processed_msg_ids.add(msg_id)

        except Exception as e:
            logger.warning(f"拉取群 {group_id} 离线历史失败: {e}")

    mark_dirty()
