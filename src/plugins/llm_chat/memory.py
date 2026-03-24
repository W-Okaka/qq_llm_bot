"""
人物记忆管理模块 - 通过 LLM 总结聊天记录，持久化关键人物信息
支持按 session 隔离记忆（每个群/私聊独立记忆文件）
"""
from pathlib import Path
from typing import Dict, List

from nonebot.log import logger

DATA_DIR = Path(__file__).parent / "data"
MEMORY_DIR = DATA_DIR / "memory"
# 旧的全局记忆文件（用于迁移）
_LEGACY_MEMORY_FILE = DATA_DIR / "memory.md"

# 当会话历史 >= MEMORY_TRIGGER 条时触发总结
MEMORY_TRIGGER = 80
# 每次从最老的消息中取 MEMORY_BATCH 条进行总结
MEMORY_BATCH = 50


def _memory_path(session_id: str) -> Path:
    """每个 session 一个记忆文件"""
    safe_name = session_id.replace("/", "_")
    return MEMORY_DIR / f"{safe_name}.md"


def migrate_legacy_memory():
    """迁移旧的全局 memory.md 到按 session 隔离的记忆文件"""
    if not _LEGACY_MEMORY_FILE.exists():
        return

    legacy_content = _LEGACY_MEMORY_FILE.read_text(encoding="utf-8")
    if not legacy_content.strip():
        _LEGACY_MEMORY_FILE.rename(_LEGACY_MEMORY_FILE.with_suffix(".md.bak"))
        return

    MEMORY_DIR.mkdir(parents=True, exist_ok=True)

    from .history import session_histories
    migrated = 0
    for sid in session_histories:
        if sid.startswith("group_"):
            target = _memory_path(sid)
            if not target.exists():
                target.write_text(legacy_content, encoding="utf-8")
                migrated += 1

    _LEGACY_MEMORY_FILE.rename(_LEGACY_MEMORY_FILE.with_suffix(".md.bak"))
    logger.info(f"已迁移旧记忆到 {migrated} 个群聊 session，旧文件已备份为 memory.md.bak")


def load_memory(session_id: str) -> str:
    """读取指定 session 的记忆内容"""
    path = _memory_path(session_id)
    if path.exists():
        try:
            return path.read_text(encoding="utf-8")
        except IOError:
            pass
    return ""


def save_memory(session_id: str, content: str):
    """写入指定 session 的记忆"""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    _memory_path(session_id).write_text(content, encoding="utf-8")


def get_memory_for_prompt(session_id: str) -> str:
    """返回用于拼接到 system prompt 的记忆文本"""
    memory = load_memory(session_id)
    if not memory.strip():
        return ""
    return f"\n\n--- 以下是你对这个群/对话中群友们的长期记忆 ---\n{memory}"


def build_summarize_prompt(messages_batch: List[Dict[str, str]], existing_memory: str) -> List[Dict[str, str]]:
    """构建发给 LLM 的总结请求消息"""
    chat_text = "\n".join(msg["content"] for msg in messages_batch)

    existing_section = ""
    if existing_memory.strip():
        existing_section = f"\n\n【已有记忆】（请在此基础上合并更新）：\n{existing_memory}"

    system_msg = {
        "role": "system",
        "content": (
            "你是一个记忆整理助手。你的任务是从聊天记录中提取两类信息：\n"
            "**人物档案**和**事件记录**。\n\n"
            "要求：\n"
            "1. 人物档案部分：\n"
            "   - 格式为 ## 昵称 (QQ:号码)\n"
            "   - 每人提取 2-4 条要点：性格、兴趣爱好、特征习惯、与他人的关系\n"
            "   - 忽略日常寒暄，只保留能反映人物特点的信息\n"
            "2. 事件记录部分：\n"
            "   - 格式为 - [日期] 事件简述（一句话）\n"
            "   - 只记录有意义的事件（如做了什么决定、发生了什么有趣的事、讨论了什么重要话题）\n"
            "   - 忽略日常闲聊和无实质内容的对话\n"
            "   - 按时间顺序排列，保留最近 20 条左右\n"
            "3. 如果有已有记忆，在其基础上合并更新，去除重复，保留最新认知\n"
            "4. 直接输出 markdown 格式，不要解释说明\n"
            "5. 如果聊天记录中没有有价值的信息，原样返回已有记忆\n\n"
            "输出格式：\n"
            "# 人物档案\n"
            "## 昵称 (QQ:号码)\n"
            "- 要点1\n"
            "- 要点2\n\n"
            "# 事件记录\n"
            "- [MM-DD] 事件简述\n"
            "- [MM-DD] 事件简述\n"
        )
    }

    user_msg = {
        "role": "user",
        "content": f"【聊天记录】：\n{chat_text}{existing_section}"
    }

    return [system_msg, user_msg]
