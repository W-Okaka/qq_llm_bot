"""
Admin 管理面板路由 - 集成到 Bot 进程，直接操作内存数据
历史记录的增删直接修改 session_histories，避免与磁盘文件的同步问题。
"""
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from nonebot import get_app
from nonebot.log import logger

from .config import (
    get_system_prompt, set_system_prompt,
    PROVIDERS, get_active_provider_id, set_active_provider,
)
from .history import session_histories, processed_msg_ids, mark_dirty, do_save

# ============ 路径配置 ============
ADMIN_DIR = Path(__file__).parent.parent.parent.parent / "admin"
STATIC_DIR = ADMIN_DIR / "static"

# ============ 鉴权 ============
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")


async def verify_token(request: Request):
    """API 鉴权依赖（仅当配置了 ADMIN_TOKEN 时生效）"""
    if not ADMIN_TOKEN:
        return
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {ADMIN_TOKEN}":
        raise HTTPException(status_code=401, detail="未授权")


# ============ 数据模型 ============
class PromptUpdate(BaseModel):
    prompt: str


class PromptTemplate(BaseModel):
    template_id: str


class ProviderSwitch(BaseModel):
    provider_id: str


# ============ 预设模板 ============
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

# ============ API 路由 ============
router = APIRouter(prefix="/api", dependencies=[Depends(verify_token)])


# -- 提示词 --

@router.get("/prompt")
async def get_prompt():
    return {"prompt": get_system_prompt()}


@router.put("/prompt")
async def update_prompt(data: PromptUpdate):
    set_system_prompt(data.prompt)
    return {"success": True, "message": "提示词已更新，下次对话生效"}


@router.get("/prompt/templates")
async def get_templates():
    return {
        "templates": [
            {"id": k, "name": v["name"], "prompt": v["prompt"]}
            for k, v in PROMPT_TEMPLATES.items()
        ]
    }


@router.post("/prompt/template")
async def apply_template(data: PromptTemplate):
    if data.template_id not in PROMPT_TEMPLATES:
        raise HTTPException(status_code=404, detail="模板不存在")
    prompt = PROMPT_TEMPLATES[data.template_id]["prompt"]
    set_system_prompt(prompt)
    return {"success": True, "prompt": prompt}


# -- 模型 Provider --

@router.get("/providers")
async def get_providers():
    active_id = get_active_provider_id()
    providers = [
        {"id": pid, "name": p["name"], "api_type": p["api_type"], "model": p["model"]}
        for pid, p in PROVIDERS.items()
    ]
    return {"providers": providers, "active": active_id}


@router.put("/providers/active")
async def switch_provider(data: ProviderSwitch):
    try:
        set_active_provider(data.provider_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "message": f"已切换到 {PROVIDERS[data.provider_id]['name']}"}


# -- 历史记录（直接操作内存） --

@router.get("/history")
async def get_history_list():
    sessions = []
    for session_id, messages in session_histories.items():
        sessions.append({
            "id": session_id,
            "type": "group" if session_id.startswith("group_") else "private",
            "count": len(messages)
        })
    return {"sessions": sessions}


@router.get("/history/{session_id}")
async def get_session_detail(session_id: str):
    if session_id not in session_histories:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"session_id": session_id, "messages": list(session_histories[session_id])}


@router.delete("/history/{session_id}/{index}")
async def delete_message(session_id: str, index: int):
    if session_id not in session_histories:
        raise HTTPException(status_code=404, detail="会话不存在")
    history = session_histories[session_id]
    if index < 0 or index >= len(history):
        raise HTTPException(status_code=400, detail="索引越界")
    del history[index]
    mark_dirty()
    return {"success": True}


@router.delete("/history/{session_id}")
async def clear_session(session_id: str):
    if session_id in session_histories:
        del session_histories[session_id]
        mark_dirty()
    return {"success": True}


@router.delete("/history")
async def clear_all_history():
    session_histories.clear()
    processed_msg_ids.clear()
    mark_dirty()
    do_save()
    return {"success": True}


# ============ 挂载到 NoneBot 的 FastAPI 实例 ============
app = get_app()
app.include_router(router)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="admin_static")


@app.get("/admin")
async def admin_page():
    return FileResponse(STATIC_DIR / "index.html")


logger.info("Admin 管理面板已挂载到 /admin")
