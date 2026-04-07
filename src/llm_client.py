import httpx
from typing import List, Dict, Any, Optional

class LLMClient:
    def __init__(
        self, 
        base_url: str = "http://localhost:11434/v1", 
        model: str = "qwen3:latest", 
        api_key: str = "ollama"
    ):
        """
        极简的 LLM 客户端。
        默认指向本地 Ollama 的 OpenAI 兼容接口。
        未来哪怕换成远端的大厂 API，只需修改 base_url 和 api_key，下方逻辑完全不用动。
        """
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        # 本地跑大模型可能会慢（尤其是第一次加载到显存），给足超时时间
        self.client = httpx.Client(timeout=120.0)

    def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        发送聊天请求，支持 Tool Calling。
        """
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        payload = {
            "model": self.model,
            "messages": messages,
            # 温度设低，保证工具调用的 JSON 格式稳定且合乎逻辑
            "temperature": 0.1, 
        }
        
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        try:
            response = self.client.post(url, headers=headers, json=payload)
            response.raise_for_status() # 如果是 4xx 或 5xx 会抛出异常
            return response.json()
        except httpx.HTTPError as e:
            print(f"\n❌ [LLM Client 错误] HTTP 请求失败: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"详情: {e.response.text}")
            raise

    def close(self):
        """释放 HTTP 连接池"""
        self.client.close()

if __name__ == "__main__":
    # ==========================================
    # 极简测试：测试连通性与基础问答
    # ==========================================
    # 注：如果你没拉取 qwen3，可以改成 qwen2.5:latest
    llm = LLMClient(model="qwen2.5:latest") 
    print(f"🚀 正在连接本地 Ollama ({llm.model})...")
    try:
        res = llm.chat([{"role": "user", "content": "用一句话回答：什么是 Markdown？"}])
        print("✅ 成功！LLM 回复:")
        print(res["choices"][0]["message"]["content"])
    except Exception as e:
        print("请确保 Ollama 正在运行，并且已拉取对应的模型。")
