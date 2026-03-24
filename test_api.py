"""
独立测试脚本：测试 LLM API 是否正常工作
运行方式：python test_api.py
"""
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_API_URL = os.getenv("LLM_API_URL", "https://api.deepseek.com/chat/completions")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

def test_api():
    if not LLM_API_KEY:
        print("缺少 LLM_API_KEY，请先在 .env 中配置后再运行测试")
        return

    print(f"测试配置:")
    print(f"  URL: {LLM_API_URL}")
    print(f"  Model: {LLM_MODEL}")
    print(f"  API Key: {LLM_API_KEY[:10]}...")
    print()
    
    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "user", "content": "你好，请简单回复一个字"}
        ],
        "max_tokens": 50
    }
    
    print("发送请求...")
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(LLM_API_URL, headers=headers, json=payload)
            
            print(f"状态码: {response.status_code}")
            print(f"响应头 Content-Type: {response.headers.get('content-type')}")
            print()
            print("响应内容:")
            print(response.text)
            
            if response.status_code == 200:
                data = response.json()
                reply = data["choices"][0]["message"]["content"]
                print()
                print(f"✅ API 正常！回复内容: {reply}")
            else:
                print()
                print("❌ API 返回错误")
                
    except Exception as e:
        print(f"❌ 请求失败: {type(e).__name__}: {e}")

if __name__ == "__main__":
    test_api()
