# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

基于 NoneBot2 框架的 QQ 聊天机器人，通过 OneBot V11 协议与 NapCat 通信，使用兼容 OpenAI 格式的 LLM API（默认 DeepSeek）进行智能对话。

**环境要求**：Python >= 3.10

## 常用命令

```bash
# Windows 一键启动
.\start.bat

# 或手动运行
.\venv\Scripts\activate && python bot.py

# 安装依赖（如需重建环境）
pip install nonebot2 nonebot-adapter-onebot httpx
```

## 架构

```
qq_llm_bot/
├── bot.py              # 入口：初始化 NoneBot、注册 OneBot V11 适配器、加载插件
├── pyproject.toml      # NoneBot 适配器和插件目录配置
├── .env                # 环境变量：HOST、PORT、LLM API 配置
└── src/plugins/        # NoneBot2 插件目录（自动加载）
    └── llm_chat/       # LLM 对话插件
        └── data/       # 运行时生成：chat_history.json、processed_msg_ids.json
```

**消息流程**：QQ 客户端 → NapCat → OneBot V11 → NoneBot2 → llm_chat 插件 → LLM API → 回复

## 关键配置

**`.env` 环境变量**（变量名大小写不敏感）：
- `HOST`/`PORT`: NoneBot 监听地址（需与 NapCat 配置一致）
- `LLM_API_KEY`/`LLM_API_URL`/`LLM_MODEL`: LLM 服务配置

**硬编码配置**（`src/plugins/llm_chat/__init__.py`）：
- `MASTER_QQ`: 主人 QQ 号，只有主人可使用禁言命令
- `MAX_HISTORY`: 单会话最大历史消息数（100条）

## 核心逻辑

**响应规则**：私聊消息 或 群聊 @机器人时回复，但群聊会记录所有消息到历史（便于 LLM 了解上下文）

**session_id 规则**：
- 私聊：`private_{user_id}`（按用户隔离）
- 群聊：`group_{group_id}`（群内共享上下文）

**禁言功能**：仅主人（MASTER_QQ）可用，通过 `@某人 禁言 X分钟/小时/天` 或 `@某人 解禁` 触发。禁言命令在调用 LLM 之前直接执行，由 `parse_user_ban_intent()` 解析

**离线消息同步**：机器人连接时自动拉取已跟踪群聊的历史消息（`fetch_offline_history()`），通过 `processed_msg_ids` 去重

## 关键函数

核心逻辑均在 `src/plugins/llm_chat/__init__.py`：
- `handle_chat()`: 消息处理主入口，判断是否回复、记录历史、调用 LLM
- `call_llm_api()`: 封装 LLM API 调用（httpx 异步请求，60s 超时）
- `parse_user_ban_intent()`: 解析用户禁言命令意图（支持分钟/小时/天/秒）
- `fetch_offline_history()`: 机器人上线时拉取离线期间的群聊消息
- `save_histories()` / `load_histories()`: 对话历史持久化（JSON 格式）

## 添加新插件

在 `src/plugins/` 下创建新目录，NoneBot2 会自动加载（由 `pyproject.toml` 中 `plugin_dirs` 配置）
