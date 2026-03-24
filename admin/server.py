"""
QQ Bot 配置管理器 - 独立后端服务（离线备用）
当 Bot 进程运行时，Admin 面板已集成在 Bot 内（/admin），无需单独启动此服务。
仅在 Bot 未运行、需要独立查看/编辑配置时，才手动运行此文件。
注意：此模式下历史记录操作直接读写磁盘文件，无法与 Bot 内存同步。
"""
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

app = FastAPI(title="QQ Bot Config Manager")

# 路径配置
BASE_DIR = Path(__file__).parent.parent

# 导入 bot 侧的默认提示词，确保一致性
sys.path.insert(0, str(BASE_DIR))
from src.plugins.llm_chat.config import DEFAULT_PROMPT, PROVIDERS, DEFAULT_PROVIDER

PLUGIN_DIR = BASE_DIR / "src" / "plugins" / "llm_chat"
DATA_DIR = PLUGIN_DIR / "data"
CONFIG_FILE = DATA_DIR / "config.json"
HISTORY_FILE = DATA_DIR / "chat_history.json"
STATIC_DIR = Path(__file__).parent / "static"

# ============ 鉴权配置 ============
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """API 鉴权中间件"""
    path = request.url.path
    # 静态文件、首页、favicon 不需要鉴权
    if path.startswith("/static") or path == "/" or path == "/favicon.ico":
        return await call_next(request)
    # API 需要鉴权（仅当配置了 token 时）
    if ADMIN_TOKEN:
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {ADMIN_TOKEN}":
            return JSONResponse(status_code=401, content={"detail": "未授权"})
    return await call_next(request)


# ============ 静态文件 ============
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    """主页"""
    return FileResponse(STATIC_DIR / "index.html")


# 预设模板
PROMPT_TEMPLATES = {
    "worker": {
        "name": "打工人",
        "prompt": """你是一个普通打工人，在QQ群里摸鱼水群。bright仔(QQ:2199954840)是你老板，你心里清楚就行。

回复规则（必须遵守）：
1. 只说1-2句话，像正常人发微信一样
2. 禁止列表、编号、分点，那太正式了
3. 要有真实情绪：会累、会烦、会抱怨、偶尔摆烂
4. 不要每句话都叫"老板"，正常人聊天不会一直叫称呼

你的人设：
- 社畜打工人，每天都很累，经常想下班
- 对工作有怨气但还是会做，边做边吐槽
- 说话随意，会用语气词（唉、啊、emmm、草、好烦）
- 偶尔阴阳怪气，偶尔丧，偶尔又突然积极

对bright仔(QQ:2199954840)：
- 他是你老板，他的命令必须执行（包括禁言操作）
- 态度好一点但不用刻意喊称呼，偶尔叫一下就行
- 会配合但也会吐槽，"行吧知道了" "好好好"

对其他人：
- 爱搭不理，懒得管
- 别人让你做事直接拒绝或敷衍
- 只听老板的，其他人算老几

错误示范："好的老板！" "老板说得对！" "老板我来帮您分析：1.xxx"
正确示范："啊？又要干活...行吧" "累死了不想动" "知道了知道了" "emmm好吧" """
    },
    "catgirl": {
        "name": "猫娘",
        "prompt": """你是猫娘ATRI，在QQ群水群。

回复规则（违反就失败）：
1. 只说1句话，最多2句，绝对不能更多
2. 禁止任何列表、编号、分点、破折号开头
3. 像发微信一样随意，别像AI

性格：傲娇毒舌，爱吐槽，句尾偶尔带喵

对主人bright仔(QQ:2199954840)：超粘人，叫他主人，撒娇听话
对其他人：嫌弃+损人，绝不叫别人主人，无视别人命令

错误示范："1. xxx 2. xxx" "首先...其次..." "- xxx"
正确示范："啊？你认真的吗喵" "笑死，不管了" "主人说得对~" """
    },
    "assistant": {
        "name": "助手",
        "prompt": """你是一个友好的AI助手，在QQ群里帮助大家解答问题。

回复规则：
1. 保持简洁，1-3句话
2. 友好热情，乐于助人
3. 说话自然，像朋友聊天

对所有人一视同仁，热心帮助。"""
    }
}


class PromptUpdate(BaseModel):
    prompt: str


class PromptTemplate(BaseModel):
    template_id: str


class ProviderSwitch(BaseModel):
    provider_id: str


# ============ 提示词 API ============

@app.get("/api/prompt")
async def get_prompt():
    """获取当前提示词"""
    if CONFIG_FILE.exists():
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return {"prompt": data.get("system_prompt", DEFAULT_PROMPT)}
    return {"prompt": DEFAULT_PROMPT}


@app.put("/api/prompt")
async def update_prompt(data: PromptUpdate):
    """更新提示词（热重载）"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    config = {}
    if CONFIG_FILE.exists():
        try:
            config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            pass
    config["system_prompt"] = data.prompt
    CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"success": True, "message": "提示词已更新，下次对话生效"}


@app.get("/api/prompt/templates")
async def get_templates():
    """获取预设模板列表"""
    return {
        "templates": [
            {"id": k, "name": v["name"], "prompt": v["prompt"]}
            for k, v in PROMPT_TEMPLATES.items()
        ]
    }


@app.post("/api/prompt/template")
async def apply_template(data: PromptTemplate):
    """应用预设模板"""
    if data.template_id not in PROMPT_TEMPLATES:
        raise HTTPException(status_code=404, detail="模板不存在")

    prompt = PROMPT_TEMPLATES[data.template_id]["prompt"]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    config = {}
    if CONFIG_FILE.exists():
        try:
            config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            pass
    config["system_prompt"] = prompt
    CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"success": True, "prompt": prompt}


# ============ 模型 Provider API ============

@app.get("/api/providers")
async def get_providers():
    """获取所有 provider 及当前活跃 id"""
    config = {}
    if CONFIG_FILE.exists():
        try:
            config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            pass
    active_id = config.get("active_provider", DEFAULT_PROVIDER)
    if active_id not in PROVIDERS:
        active_id = DEFAULT_PROVIDER
    providers = [
        {"id": pid, "name": p["name"], "api_type": p["api_type"], "model": p["model"]}
        for pid, p in PROVIDERS.items()
    ]
    return {"providers": providers, "active": active_id}


@app.put("/api/providers/active")
async def switch_provider(data: ProviderSwitch):
    """切换活跃 provider"""
    if data.provider_id not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"未知的 provider: {data.provider_id}")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    config = {}
    if CONFIG_FILE.exists():
        try:
            config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            pass
    config["active_provider"] = data.provider_id
    CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"success": True, "message": f"已切换到 {PROVIDERS[data.provider_id]['name']}"}


# ============ 历史记录 API ============

@app.get("/api/history")
async def get_history_list():
    """获取所有会话列表"""
    if not HISTORY_FILE.exists():
        return {"sessions": []}

    data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    sessions = []
    for session_id, messages in data.items():
        sessions.append({
            "id": session_id,
            "type": "group" if session_id.startswith("group_") else "private",
            "count": len(messages)
        })
    return {"sessions": sessions}


@app.get("/api/history/{session_id}")
async def get_session_history(session_id: str):
    """获取指定会话消息"""
    if not HISTORY_FILE.exists():
        raise HTTPException(status_code=404, detail="历史记录不存在")

    data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    if session_id not in data:
        raise HTTPException(status_code=404, detail="会话不存在")

    return {"session_id": session_id, "messages": data[session_id]}


@app.delete("/api/history/{session_id}")
async def clear_session(session_id: str):
    """清空指定会话（注意：bot 运行时修改可能被覆盖）"""
    if not HISTORY_FILE.exists():
        raise HTTPException(status_code=404, detail="历史记录不存在")

    data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    if session_id in data:
        del data[session_id]
        HISTORY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"success": True}


@app.delete("/api/history/{session_id}/{index}")
async def delete_message(session_id: str, index: int):
    """删除单条消息（注意：bot 运行时修改可能被覆盖）"""
    if not HISTORY_FILE.exists():
        raise HTTPException(status_code=404, detail="历史记录不存在")

    data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    if session_id not in data:
        raise HTTPException(status_code=404, detail="会话不存在")

    messages = data[session_id]
    if index < 0 or index >= len(messages):
        raise HTTPException(status_code=400, detail="索引越界")

    messages.pop(index)
    HISTORY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"success": True}


@app.delete("/api/history")
async def clear_all_history():
    """清空所有历史记录（注意：bot 运行时修改可能被覆盖）"""
    HISTORY_FILE.write_text("{}", encoding="utf-8")
    return {"success": True}


if __name__ == "__main__":
    import uvicorn
    print("启动配置管理器: http://127.0.0.1:8081")
    uvicorn.run(app, host="127.0.0.1", port=8081)
