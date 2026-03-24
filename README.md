# QQ LLM Bot

> 本项目纯 Claude Code 开发。

基于 NoneBot2 + OneBot V11 的 QQ 聊天机器人，支持多模型切换与群聊技能路由（聊天、画图、搜索、语音、音乐等）。

## 功能特性

- 私聊对话（使用当前活跃模型）
- 群聊技能路由（chat / draw / ban / search / voice / video / music）
- 管理面板在线修改提示词、切换模型、管理历史
- 会话历史与长期记忆持久化
- 启动时自动拉取离线期间群聊历史

## 环境要求

- Python >= 3.10
- NapCat（OneBot V11）

## 快速开始

### 1) 安装依赖

```bash
pip install nonebot2 nonebot-adapter-onebot httpx
```

### 2) 配置环境变量

在项目根目录创建 `.env`，至少配置：

- `HOST` / `PORT`
- `LLM_API_KEY` / `LLM_API_URL` / `LLM_MODEL`
- `MINIMAX_API_KEY`
- `ADMIN_TOKEN`（可选）

### 3) 启动 Bot

```bash
# Windows 一键启动
.\start.bat

# 或手动启动
.\venv\Scripts\activate && python bot.py
```

### 4) 打开管理面板

- Bot 运行时（推荐）：`http://HOST:PORT/admin`
- Bot 未运行时（离线模式）：

```bash
python admin/server.py
```

## 项目结构

```text
qq_llm_bot/
├── bot.py
├── start.bat
├── admin/
│   ├── server.py
│   └── static/
└── src/plugins/llm_chat/
    ├── __init__.py
    ├── api.py
    ├── config.py
    ├── history.py
    ├── memory.py
    ├── skill_router.py
    └── ...
```

## 常用命令

- 查看模型列表（仅主人）：`模型列表`
- 切换模型（仅主人）：`切换模型 <id>`

## 隐私说明

以下内容默认不上传仓库：

- `.env`
- `src/plugins/llm_chat/data/`
- `CLAUDE.md`
