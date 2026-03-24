@echo off
chcp 65001 >nul
title QQ LLM Bot
cd /d %~dp0

echo ========================================
echo   QQ LLM Bot
echo ========================================
echo.

:: 检查虚拟环境
if not exist venv\Scripts\activate (
    echo [!] 未找到虚拟环境，正在创建...
    python -m venv venv
    call venv\Scripts\activate
    echo [*] 正在安装依赖...
    pip install nonebot2 nonebot-adapter-onebot httpx
) else (
    call venv\Scripts\activate
)

:: 检查 .env
if not exist .env (
    echo [!] 未找到 .env 配置文件，请先配置后再启动
    pause
    exit /b 1
)

echo [*] 启动 Bot...
echo.
python bot.py
pause
