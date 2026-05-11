import requests
import json

url = "https://api.siliconflow.cn/v1/chat/completions"
api_key = "sk-kflidfkvezitssrkzkbtqhovkcdazisilhpxdehuhfnhstnn"

headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {api_key}"
}

data = {
    "model": "Pro/MiniMaxAI/MiniMax-M2.5",
    "messages": [{"role": "user", "content": "Test"}]
}

try:
    response = requests.post(url, headers=headers, json=data, timeout=120)
    print(f"状态码: {response.status_code}")
    print("返回内容:", response.text)
except Exception as e:
    print(f"请求发生异常: {e}")