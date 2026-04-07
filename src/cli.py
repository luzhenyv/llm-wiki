import sys
import argparse
from rich.console import Console
from rich.markdown import Markdown
from agent import WikiAgent

console = Console()

def print_header():
    header = """
[bold cyan]
██╗    ██╗██╗██╗  ██╗██╗   █████╗  ██████╗ ███████╗███╗   ██╗████████╗
██║    ██║██║██║ ██╔╝██║  ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝
██║ █╗ ██║██║█████╔╝ ██║  ███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║   
██║███╗██║██║██╔═██╗ ██║  ██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║   
╚███╔███╔╝██║██║  ██╗██║  ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║   
 ╚══╝╚══╝ ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝   
[/bold cyan]
[dim]CLI 知识库维护终端 | Powered by Local FTS5 & Ollama[/dim]
"""
    console.print(header)

def start_repl():
    print_header()
    agent = WikiAgent(workspace="wiki")
    
    console.print("\n[dim]提示: 直接输入指令让 Agent 帮你整理知识，或者输入 /exit 退出[/dim]\n")
    
    while True:
        try:
            # 读取用户输入
            user_input = console.input("\n[bold green]❯ 请输入指令:[/bold green] ").strip()
            
            if not user_input:
                continue
                
            if user_input.lower() in ['/exit', '/quit']:
                console.print("[dim]👋 再见！你的知识库已安全保存到本地。[/dim]")
                break
                
            # 交给大模型执行（Agent Loop）
            agent.run_agent_loop(user_input)
            
        except KeyboardInterrupt:
            # 捕获 Ctrl+C，优雅退出
            console.print("\n[dim]👋 已强行中断。再见！[/dim]")
            break
        except Exception as e:
            console.print(f"[bold red]❌ 终端发生了未捕获的错误: {e}[/bold red]")

if __name__ == "__main__":
    start_repl()
