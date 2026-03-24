"""
技能路由模块 - LLM 自动识别意图，路由到对应技能
关键词优先（零成本），匹配不到再调模型分类
"""
import json
import re

from nonebot.log import logger

from .api import call_llm_api
from .config import get_provider_by_id

# 技能定义
SKILLS = {
    "chat": "普通聊天/闲聊/问答/讨论",
    "draw": "画图/生成图片/画画",
    "ban": "禁言/解禁/关小黑屋",
    "search": "搜索/查询实时信息/新闻/天气/最新动态",
    "voice": "语音合成/朗读/念出来",
    "video": "生成视频/文生视频",
    "music": "唱歌/作曲/生成音乐/写歌",
}

# 意图分类提示词（尽量短，省 token）
ROUTER_PROMPT = """判断用户消息意图，只返回一个词：chat/draw/ban/search/voice/video/music
规则：
- 明确要画图 => draw
- 明确要禁言/解禁 => ban
- 明确要搜索实时信息 => search
- 明确要朗读/语音 => voice
- 明确要生成视频 => video
- 明确要生成音乐/唱歌 => music
- 其余所有情况（包括不确定）一律返回 chat
只返回一个英文词，不要解释。"""

# 画图 prompt 提取提示词
DRAW_EXTRACT_PROMPT = """从用户消息中提取画图描述，翻译/优化为适合AI绘画的英文prompt。
只返回英文prompt本身，不要有任何额外解释。
如果用户没有给出具体描述，根据聊天上下文和记忆中的信息合理推测。

你可能会收到长期记忆（包含人物外貌、性格等）和最近聊天记录作为上下文参考。
如果用户提到某个人，请结合记忆中该人物的外貌特征来生成prompt。

重要：发送消息的人叫"小织"，是一个20岁的中国大学女生，黑色长发，身材普通偏瘦，穿着休闲日常（T恤牛仔裤或校园风），长相清秀。
如果用户要求画"自己""自画像""你自己"等，就按照小织的外貌来生成prompt。"""

# 禁言信息提取提示词
BAN_EXTRACT_PROMPT = """从用户消息中提取禁言信息，返回格式：目标名称|时长分钟
- 如果是解禁，时长填0
- 如果没提时长，默认10分钟
- 只返回一行"目标名称|时长分钟"，不要有其他内容
示例：
- "把李四禁言1小时" → 李四|60
- "给小明解禁" → 小明|0
- "让张三闭嘴半天" → 张三|720"""

# 关键词匹配规则
_DRAW_KEYWORDS = re.compile(r"画[一个张幅只条]|画个|生成图|来[张个一].*图|画图|画画|生成.*图片|帮我画|给我画")
_BAN_KEYWORDS = re.compile(r"禁言|解禁|关小黑屋|闭嘴|封禁|解封")
_SEARCH_KEYWORDS = re.compile(r"搜[一下索]|查[一下询]|百度|谷歌|google|搜搜|帮我[搜查]|查[看找]一下")
_VOICE_KEYWORDS = re.compile(r"语音说|用语音|说一段|念一下|朗读|读一下|语音朗读|念出来")
_VIDEO_KEYWORDS = re.compile(r"生成视频|拍个视频|做个视频|录个视频|文生视频|来个视频|帮我[做拍].*视频")
_MUSIC_KEYWORDS = re.compile(r"唱[一首个]|作[一首个]歌|生成音乐|写首歌|来首歌|编个曲|作曲|写[一首个]歌")
_CHAT_KEYWORDS = re.compile(r"你好|哈喽|嗨|在吗|早上好|中午好|晚上好|晚安|哈哈|呵呵|嘿嘿|聊聊|无聊|hello|hi|hey|yo", re.IGNORECASE)
_INTENT_TOKEN_RE = re.compile(r"\b(chat|draw|ban|search|voice|video|music)\b", re.IGNORECASE)

# 清理 LLM 返回中的 markdown 代码块、JSON 包裹等
_CODE_BLOCK_RE = re.compile(r"^```[\w]*\n?(.*?)```$", re.DOTALL)


def _clean_llm_output(text: str) -> str:
    """清理 LLM 返回的文本：去除 markdown 代码块、JSON 包裹等"""
    text = text.strip()
    # 去除 markdown 代码块
    m = _CODE_BLOCK_RE.match(text)
    if m:
        text = m.group(1).strip()
    # 如果是 JSON 对象，尝试提取文本内容
    if text.startswith("{") and text.endswith("}"):
        try:
            obj = json.loads(text)
            # 常见字段名
            for key in ("prompt", "text", "content", "result", "answer", "reply"):
                if key in obj:
                    return str(obj[key]).strip()
        except (json.JSONDecodeError, TypeError):
            pass
    return text


def _extract_intent(raw_text: str) -> str | None:
    """从模型原始返回中尽量提取合法意图"""
    text = _clean_llm_output(raw_text).strip().lower()
    if not text:
        return None

    if text in SKILLS:
        return text

    m = _INTENT_TOKEN_RE.search(text)
    if m:
        return m.group(1).lower()

    cn_map = {
        "聊天": "chat",
        "画图": "draw",
        "禁言": "ban",
        "搜索": "search",
        "语音": "voice",
        "视频": "video",
        "音乐": "music",
    }
    for key, intent in cn_map.items():
        if key in text:
            return intent

    return None


def _keyword_classify(message: str) -> str | None:
    """关键词快速匹配，命中返回技能名，未命中返回 None"""
    if _DRAW_KEYWORDS.search(message):
        return "draw"
    if _BAN_KEYWORDS.search(message):
        return "ban"
    if _SEARCH_KEYWORDS.search(message):
        return "search"
    if _VOICE_KEYWORDS.search(message):
        return "voice"
    if _VIDEO_KEYWORDS.search(message):
        return "video"
    if _MUSIC_KEYWORDS.search(message):
        return "music"

    # 高频寒暄/闲聊优先直接判 chat，减少不必要路由调用
    if _CHAT_KEYWORDS.search(message):
        return "chat"

    short_msg = message.strip()
    if len(short_msg) <= 4 and re.fullmatch(r"[嗯哦啊哈欸唉哇呀~～!！?？.。]+", short_msg):
        return "chat"

    return None


async def _call_deepseek(messages: list, max_tokens: int = 50) -> str:
    """用 MiniMax-M2.5 做轻量调用（意图分类、关键词提取）"""
    provider = get_provider_by_id("deepseek")
    return await call_llm_api(
        messages,
        api_key=provider["api_key"],
        api_url=provider["api_url"],
        model=provider["model"],
        api_type=provider["api_type"],
        max_tokens=max_tokens,
    )


async def classify_intent(user_message: str, chat_context: str = "") -> str:
    """意图分类：仅关键词匹配，未命中一律 chat"""
    keyword_result = _keyword_classify(user_message)
    if keyword_result:
        logger.debug(f"关键词匹配意图: {keyword_result}")
        return keyword_result

    return "chat"

async def extract_draw_prompt(user_message: str, chat_context: str = "") -> str:
    """从用户消息中提取/优化画图 prompt（英文），支持聊天上下文"""
    try:
        content = user_message
        if chat_context:
            content = f"最近的聊天记录（供参考上下文）：\n{chat_context}\n\n用户当前消息：{user_message}"
        messages = [
            {"role": "system", "content": DRAW_EXTRACT_PROMPT},
            {"role": "user", "content": content},
        ]
        result = await _call_deepseek(messages, max_tokens=100)
        return _clean_llm_output(result)
    except Exception as e:
        logger.error(f"画图 prompt 提取失败: {e}")
        return user_message


SEARCH_EXTRACT_PROMPT ="""从用户消息中提取搜索关键词，返回简洁的搜索查询词。
只返回搜索关键词本身，不要有任何额外解释。
示例：
- "今天武汉天气怎么样" → 武汉今天天气
- "最近有什么新闻" → 最新新闻
- "搜一下 Python 3.13 新特性" → Python 3.13 新特性"""


async def extract_search_query(user_message: str) -> str:
    """从用户消息中提取简洁的搜索关键词"""
    try:
        messages = [
            {"role": "system", "content": SEARCH_EXTRACT_PROMPT},
            {"role": "user", "content": user_message},
        ]
        result = await _call_deepseek(messages, max_tokens=50)
        return _clean_llm_output(result)
    except Exception as e:
        logger.error(f"搜索关键词提取失败: {e}")
        return user_message


async def extract_ban_info(user_message: str) -> tuple[str, int] | None:
    """从用户消息中提取禁言目标和时长，返回 (目标名称, 时长分钟) 或 None"""
    try:
        messages = [
            {"role": "system", "content": BAN_EXTRACT_PROMPT},
            {"role": "user", "content": user_message},
        ]
        result = await _call_deepseek(messages, max_tokens=30)
        result = result.strip()
        if "|" in result:
            parts = result.split("|", 1)
            target = parts[0].strip()
            minutes = int(parts[1].strip())
            return (target, minutes)
    except Exception as e:
        logger.error(f"禁言信息提取失败: {e}")
    return None


# ============ 语音/视频/音乐提取 ============

VOICE_EXTRACT_PROMPT = """从用户消息中提取要朗读/语音合成的文本内容。
去掉"语音说""用语音""念一下"等指令词，只返回要朗读的文本本身。
如果用户没给出具体文本，返回一句合适的问候语。
只返回文本内容，不要有额外解释。"""

VIDEO_EXTRACT_PROMPT = """从用户消息中提取视频描述，翻译/优化为适合AI视频生成的英文prompt。
只返回英文prompt本身，不要有任何额外解释。
如果用户没有给出具体描述，根据上下文合理推测。"""

MUSIC_EXTRACT_PROMPT = """从用户消息中提取音乐信息，返回JSON格式：
{"style": "音乐风格描述（英文）", "lyrics": "歌词（中文，如有）"}
- style：从消息中提取的音乐风格/情绪/主题描述，翻译为英文
- lyrics：如果用户提供了歌词就填入，没有则为空字符串
只返回JSON，不要有其他内容。"""


async def extract_voice_text(user_message: str) -> str:
    """从用户消息中提取要朗读的文本"""
    try:
        messages = [
            {"role": "system", "content": VOICE_EXTRACT_PROMPT},
            {"role": "user", "content": user_message},
        ]
        result = await _call_deepseek(messages, max_tokens=200)
        return _clean_llm_output(result)
    except Exception as e:
        logger.error(f"语音文本提取失败: {e}")
        return user_message


async def extract_video_prompt(user_message: str) -> str:
    """从用户消息中提取/优化视频生成 prompt（英文）"""
    try:
        messages = [
            {"role": "system", "content": VIDEO_EXTRACT_PROMPT},
            {"role": "user", "content": user_message},
        ]
        result = await _call_deepseek(messages, max_tokens=100)
        return _clean_llm_output(result)
    except Exception as e:
        logger.error(f"视频 prompt 提取失败: {e}")
        return user_message


async def extract_music_info(user_message: str) -> tuple[str, str]:
    """从用户消息中提取音乐风格和歌词，返回 (style, lyrics)"""
    try:
        messages = [
            {"role": "system", "content": MUSIC_EXTRACT_PROMPT},
            {"role": "user", "content": user_message},
        ]
        result = await _call_deepseek(messages, max_tokens=200)
        result = _clean_llm_output(result)
        # 尝试解析 JSON
        if result.startswith("{"):
            obj = json.loads(result)
            return obj.get("style", "pop music"), obj.get("lyrics", "")
    except Exception as e:
        logger.error(f"音乐信息提取失败: {e}")
    return "pop music", ""
