"""
禁言功能模块 - 解析禁言指令和用户禁言意图
"""
import re


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
    # 兼容 [CQ:at,qq=123] 和 [CQ:at,qq=123,name=xxx] 等带额外参数的格式
    at_matches = re.findall(r'\[CQ:at,qq=(\d+)[^\]]*\]', message)
    if not at_matches:
        return None

    if '解禁' in message or '解除禁言' in message:
        return ('unban', int(at_matches[-1]), 0)

    if '禁言' in message or '关小黑屋' in message or '闭嘴' in message:
        duration = 600  # 默认 10 分钟
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
        return ('ban', int(at_matches[-1]), duration)

    return None
