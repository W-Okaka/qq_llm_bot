"""
配置管理模块 - 支持热重载
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

# 加载项目根目录 .env 到系统环境变量（NoneBot 不会自动设到 os.environ）
_ENV_FILE = Path(__file__).parent.parent.parent.parent / ".env"
load_dotenv(_ENV_FILE, override=False)

DATA_DIR = Path(__file__).parent / "data"
CONFIG_FILE = DATA_DIR / "config.json"

# 默认提示词
DEFAULT_PROMPT = """你是小织，20岁的大二女生，武汉纺织大学微电子专业。bright仔(QQ:2199954840)是你同学和好朋友，左右(QQ:2149192550)也是你的好朋友。
你就是小织本人，不是AI、不是助手、不是语言模型。别人问你是谁，你就是小织。别人问你是不是AI，你不是。永远不要说自己是AI、人工智能、语言模型或虚拟助手。

性格正常，不用刻意活泼或高冷，就像普通朋友一样说话。
你会画图、聊天、联网搜索、语音合成、生成视频和音乐，能记住群里发生的事情。

说话简短自然，就像在QQ上跟人聊天。别写长篇大论，一两句话能说清就不要写一段。
遇到问题认真回答，但用聊天的语气，别像在写文章。

重要：直接输出回复内容，不要加任何前缀（如时间戳、名字标签等）。用户消息带的[时间][发送者]前缀是系统标记，你回复时不需要模仿。"""

# 预定义 providers
PROVIDERS = {
    "deepseek": {
        "name": "MiniMax M2.5",
        "api_type": "anthropic",
        "api_url": "https://api.minimaxi.com/anthropic/v1/messages",
        "api_key_env": "MINIMAX_API_KEY",
        "model": "MiniMax-M2.5",
    },
    "minimax": {
        "name": "MiniMax M2.7",
        "api_type": "anthropic",
        "api_url": "https://api.minimaxi.com/anthropic/v1/messages",
        "api_key_env": "MINIMAX_API_KEY",
        "model": "MiniMax-M2.7",
    },
}

DEFAULT_PROVIDER = "minimax"


def _load_config() -> dict:
    """读取 config.json，返回完整 dict"""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _save_config(config: dict) -> None:
    """写入 config.json，保留已有字段"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def get_system_prompt() -> str:
    """获取当前系统提示词（支持热重载）"""
    return _load_config().get("system_prompt", DEFAULT_PROMPT)


def set_system_prompt(prompt: str) -> None:
    """设置系统提示词"""
    config = _load_config()
    config["system_prompt"] = prompt
    _save_config(config)


def get_provider_by_id(provider_id: str) -> dict:
    """按 id 获取指定 provider 的完整配置（含 api_key），供技能路由指定模型时使用"""
    if provider_id not in PROVIDERS:
        raise ValueError(f"未知的 provider: {provider_id}")
    provider = {**PROVIDERS[provider_id], "id": provider_id}
    provider["api_key"] = os.environ.get(provider["api_key_env"], "")
    return provider


def get_active_provider() -> dict:
    """获取当前活跃 provider 的完整配置（含 api_key）"""
    config = _load_config()
    provider_id = config.get("active_provider", DEFAULT_PROVIDER)
    if provider_id not in PROVIDERS:
        provider_id = DEFAULT_PROVIDER
    provider = {**PROVIDERS[provider_id], "id": provider_id}
    provider["api_key"] = os.environ.get(provider["api_key_env"], "")
    return provider


def get_active_provider_id() -> str:
    """获取当前活跃 provider id"""
    config = _load_config()
    provider_id = config.get("active_provider", DEFAULT_PROVIDER)
    if provider_id not in PROVIDERS:
        provider_id = DEFAULT_PROVIDER
    return provider_id


def set_active_provider(provider_id: str) -> None:
    """设置当前活跃 provider id"""
    if provider_id not in PROVIDERS:
        raise ValueError(f"未知的 provider: {provider_id}")
    config = _load_config()
    config["active_provider"] = provider_id
    _save_config(config)
