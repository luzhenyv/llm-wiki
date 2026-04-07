from typing import List, Dict, Any

def get_wiki_tools() -> List[Dict[str, Any]]:
    """
    定义 LLM 与 Wiki 系统的交互契约 (JSON Schema)。
    这套格式是事实上的行业标准（兼容 OpenAI / Ollama / DeepSeek 等）。
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "search_wiki",
                "description": "搜索现有的 Wiki 知识库，寻找相关的文本片段。当你遇到未知概念，或需要关联已有知识时，必须首先调用此工具。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "搜索关键词，例如 'LLM 架构' 或 'Project X'"
                        }
                    },
                    "required": ["query"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "read_page",
                "description": "读取某一篇现存 Wiki 页面的完整内容和 Frontmatter。通常在 search_wiki 发现某页面重要后，调用此工具深度阅读。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filepath": {
                            "type": "string",
                            "description": "Markdown 文件的路径，如 'concepts/LLM.md'"
                        }
                    },
                    "required": ["filepath"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "write_page",
                "description": "创建或覆盖写入一个 Wiki 页面。一旦决定要写，这个操作会被放入 Git 的暂存区 (Staging Area)。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filepath": {
                            "type": "string",
                            "description": "要写入的文件路径，如 'entities/Apple.md'"
                        },
                        "content": {
                            "type": "string",
                            "description": "纯净的 Markdown 内容正文（不包含顶部 YAML Frontmatter）。"
                        },
                        "frontmatter": {
                            "type": "object",
                            "description": "YAML 头部的元数据。必须包含 'tags' (列表) 和 'last_updated' (日期字符串)。",
                            "additionalProperties": True
                        }
                    },
                    "required": ["filepath", "content", "frontmatter"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "ask_human",
                "description": "当你遇到歧义，或认为需要人工确认/补充信息时，调用此工具向用户提问。执行会暂停，直到用户回复。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "你要问用户的问题"
                        }
                    },
                    "required": ["question"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "finish_task",
                "description": "当你认为你已经完成了资料的阅读、检索、并且所有的 write_page 都已经执行完毕，且日志/目录也更新妥当后，调用此工具结束工作流。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": "给用户的一句话总结，说明你做了什么修改"
                        }
                    },
                    "required": ["summary"]
                }
            }
        }
    ]
