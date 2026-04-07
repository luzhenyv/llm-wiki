import json
import pathlib
from typing import List, Dict, Any, Optional
from rich.console import Console

from llm_client import LLMClient
from tools_schema import get_wiki_tools
from indexer import WikiIndexer

class WikiAgent:
    def __init__(self, workspace: str = "wiki"):
        self.console = Console()
        self.workspace = pathlib.Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        
        self.llm = LLMClient(model="qwen3:latest") # 默认使用 qwen3，也可以换成 gemma4
        self.tools = get_wiki_tools()
        
        self.indexer = WikiIndexer(db_path=".wiki_index.db")
        # 启动时增量索引本地所有 Markdown，保证检索池永远是最新的
        count = self.indexer.index_directory(self.workspace)
        self.console.print(f"[dim]系统就绪: 已连接本地搜索缓存 (包含 {count} 篇文档)[/dim]")

        # 动态加载根目录下的 Schema (System Prompt)
        schema_path = pathlib.Path(__file__).parent / ".wiki_schema.md"
        if schema_path.exists():
            self.system_prompt = schema_path.read_text(encoding='utf-8')
        else:
            self.system_prompt = """
你是一个负责维护个人知识库 (Wiki) 的 AI 智能体。你的工作目录位于本地文件系统中。
你具备搜索和读写文件的能力。请遵循以下规则：
1. 遇到用户提供的资料，你必须主动搜索已有知识库（调用 search_wiki）。
2. 如果存在相关文件，你应该阅读（read_page），并将新知识整合进去（write_page）。
3. 如果不存在，你可以新建文件（write_page）。
4. 每次调用 write_page，你必须提供 YAML Frontmatter。
5. 遇到不确定的内容，调用 ask_human 请求用户的人工确认。
6. 完成全部流程后，调用 finish_task 并给出简短的总结汇报。
"""

    def _dict_to_yaml_frontmatter(self, data: dict) -> str:
        """不依赖第三方库，手写一个基础的字典转 YAML Frontmatter"""
        yaml_lines = ["---"]
        for key, value in data.items():
            if isinstance(value, list):
                yaml_lines.append(f"{key}:")
                for item in value:
                    # 去掉双引号干扰
                    safe_item = str(item).replace('"', '')
                    yaml_lines.append(f"  - \"{safe_item}\"")
            else:
                safe_val = str(value).replace('"', '')
                yaml_lines.append(f"{key}: \"{safe_val}\"")
        yaml_lines.append("---")
        return "\n".join(yaml_lines)

    def run_agent_loop(self, user_prompt: str):
        """核心的 ReAct Tool Calling 死循环"""
        messages = [
            {"role": "system", "content": self.system_prompt.strip()},
            {"role": "user", "content": user_prompt}
        ]

        self.console.print("\n[bold cyan]🧠 WikiAgent 正在思考...[/bold cyan]")
        
        while True:
            try:
                response = self.llm.chat(messages, tools=self.tools)
            except Exception as e:
                self.console.print(f"[bold red]LLM 接口异常: {e}[/bold red]")
                break
            
            choice = response["choices"][0]
            message = choice["message"]
            messages.append(message) # 必须把 LLM 的回复原样加进历史记录

            # 没有工具调用，说明 LLM 想直接说普通对话
            if not message.get("tool_calls"):
                content = message.get("content")
                if content:
                    self.console.print(f"\n🤖 [bold green]WikiAgent:[/bold green] {content}")
                break

            # 处理所有的工具调用
            for tool_call in message["tool_calls"]:
                tool_name = tool_call["function"]["name"]
                try:
                    args = json.loads(tool_call["function"]["arguments"])
                except json.JSONDecodeError:
                    args = {} # 兜底防止模型发疯输出坏的 JSON

                # CLI UI 打印当前动作
                self.console.print(f"[dim]⚡ 执行工具: {tool_name}({json.dumps(args, ensure_ascii=False)})[/dim]")
                
                # 执行本地逻辑
                tool_result = self._execute_local_tool(tool_name, args)
                
                # 核心拦截器 1: 任务完成
                if tool_name == "finish_task":
                    self.console.print(f"\n🎉 [bold green]任务完成:[/bold green] {args.get('summary', '所有操作已结束。')}")
                    return # 退出大循环

                # 核心拦截器 2: 人在回路 (Human in the loop)
                if tool_name == "ask_human":
                    self.console.print(f"\n🤔 [bold yellow]Agent 提问:[/bold yellow] {args.get('question')}")
                    user_reply = self.console.input("[bold yellow]❯ 你的回答:[/bold yellow] ")
                    tool_result = f"用户回复: {user_reply}"

                # 包装结果反馈给 LLM
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": str(tool_result)
                })

    def _execute_local_tool(self, tool_name: str, args: Dict) -> str:
        """本地函数路由表"""
        try:
            if tool_name == "search_wiki":
                results = self.indexer.search(args.get("query", ""))
                if not results:
                    return "未搜索到相关的本地知识。"
                return "检索结果:\n" + "\n---\n".join([f"文件:{r['filepath']}\n片段:{r['content']}" for r in results])
                
            elif tool_name == "read_page":
                target_path = self.workspace / args["filepath"]
                if not target_path.exists():
                    return f"错误：文件 {args['filepath']} 不存在。"
                return target_path.read_text(encoding='utf-8')
                
            elif tool_name == "write_page":
                filepath = args["filepath"]
                content = args["content"]
                frontmatter = args.get("frontmatter", {})
                
                # 安全路径检查（防止路径穿越）
                target_path = self.workspace / filepath
                if not str(target_path.resolve()).startswith(str(self.workspace.resolve())):
                    return "错误：非法的路径写入。"
                
                # 拼接文件并写入磁盘
                target_path.parent.mkdir(parents=True, exist_ok=True)
                yaml_header = self._dict_to_yaml_frontmatter(frontmatter)
                full_content = f"{yaml_header}\n\n{content}"
                
                target_path.write_text(full_content, encoding='utf-8')
                
                # 极其重要：一写入文件，马上更新本地索引池，让 Agent 下一秒就能搜到它自己写的！
                self.indexer.index_file(target_path)
                return "SUCCESS: 页面已成功写入并重新索引。"
                
            elif tool_name in ["ask_human", "finish_task"]:
                return "OK" # 特殊控制流工具，在 run_agent_loop 里拦截处理
                
            else:
                return f"错误：未知的工具 {tool_name}"
                
        except Exception as e:
            return f"工具执行异常: {str(e)}"
