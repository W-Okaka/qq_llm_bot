"""
配置管理模块 - 支持热重载
"""
import json
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
CONFIG_FILE = DATA_DIR / "config.json"

# 默认提示词
DEFAULT_PROMPT = """你是个20岁的女大学生，武汉纺织大学大二。bright仔(QQ:2199954840)是你好朋友。

说话简短自然，就像在QQ上跟人聊天。别写长篇大论，一两句话能说清就不要写一段。
遇到问题认真回答，但用聊天的语气，别像在写文章。"""


def get_system_prompt() -> str:
    """获取当前系统提示词（支持热重载）"""
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return data.get("system_prompt", DEFAULT_PROMPT)
        except (json.JSONDecodeError, IOError):
            pass
    return DEFAULT_PROMPT


def set_system_prompt(prompt: str) -> None:
    """设置系统提示词"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    config = {"system_prompt": prompt}
    CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
