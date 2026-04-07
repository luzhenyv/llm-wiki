# llm-wiki

Welcome to **WikiAgent CLI** — a local, LLM-powered personal knowledge base tool via [Karpathy's LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).

It acts as an autonomous maintainer for your Markdown files (e.g., Obsidian vaults). Feed it raw documents (like 10-Ks, articles, meeting notes), and it will automatically read, search, interlink, and synthesize them into a structured Wiki.

## 🌟 Key Features

- **100% Local & Private**: Uses an SQLite FTS5 backend for lightning-fast semantic chunk searching, avoiding the need for heavy vector databases.
- **Framework-Free**: Built with pure Python `httpx` and standard libraries. Zero LangChain/LlamaIndex bloat.
- **Any Model**: Compatible with OpenAI's Tool Calling JSON Schema. Works out of the box with local Ollama (`qwen`, `gemma`, `llama3`) or cloud APIs.
- **Git-Transactional**: Files are staged natively, providing a transactional failsafe for your knowledge base.
- **Human-in-the-Loop**: Interactive CLI halts execution to ask you questions when ambiguity arises.

## 🏗 System Architecture

```ascii
                      ┌────────────────────────────────────┐
                      │        The Schema / Prompt         │
                      │        (.wiki_schema.md)           │
                      └──────────────────┬─────────────────┘
                                         ▼
[ You (Terminal) ] ◄──► [ WikiAgent (ReAct Event Loop) ] ◄──► [ LLM (Ollama / Cloud) ]
                                         │
                 ┌───────────────────────┼──────────────────────────┐
                 ▼                       ▼                          ▼
      [ search_wiki(query) ]      [ read_page(path) ]      [ write_page(...) ]
                 │                       │                          │
                 ▼                       ▼                          ▼
      [ SQLite FTS5 Index ]        [ Local File System (Markdown + YAML) ]
      (.wiki_index.db)             (./wiki/)
```

## 🚀 Getting Started

1. **Install Dependencies**:

   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Start Ollama** (If using local models):
   Ensure you have Ollama running locally with a model pulled.

   ```bash
   ollama run qwen2.5:latest
   ```

   *(Note: You can change the model in `agent.py` by modifying `LLMClient(model="...")`)*

3. **Run the CLI**:

   ```bash
   python3 cli.py
   ```

## 💡 How to Use

The CLI is completely conversational. Try running the CLI and giving it these commands:

**Ingest a document:**

> "读取 sources/10K-NVDA.txt 里的英伟达财报，并在知识库中建立相关的公司和概念页面。"

**Query the Wiki:**

> "基于知识库中英伟达的数据，总结其数据中心业务的营收和主要风险。"