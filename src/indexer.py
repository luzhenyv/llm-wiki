import sqlite3
import pathlib
import re
from typing import List, Dict

class WikiIndexer:
    def __init__(self, db_path: str = ".wiki_index.db"):
        """初始化一个极简的本地搜索引擎，底层使用 SQLite FTS5 (Full-Text Search)"""
        self.db_path = db_path
        self.conn = sqlite3.connect(self.db_path)
        self._init_db()

    def _init_db(self):
        """初始化 SQLite FTS5 虚拟表。自带原生的 BM25 排序算法。"""
        cursor = self.conn.cursor()
        # tokenize='unicode61' 会开启词根提取（比如搜 running 也能搜到 run）
        # 我们使用 FTS5 隐藏列技巧：用 content_indexed (带空格的 CJK) 做检索，但返回原汁原味的 content
        cursor.execute('''
            CREATE VIRTUAL TABLE IF NOT EXISTS wiki_chunks 
            USING fts5(filepath UNINDEXED, chunk_id UNINDEXED, content UNINDEXED, content_indexed, tokenize='unicode61')
        ''')
        self.conn.commit()

    def _chunk_markdown(self, content: str) -> List[str]:
        """
        语义化切块 (Semantic Chunking)
        为了避免大模型上下文爆炸，我们不能整篇阅读。这里我们按 Markdown 的标题 (##) 进行切块。
        """
        # 利用正则，按行首的 `# ` 切分文本
        chunks = re.split(r'(?m)^#+\s+', content)
        
        cleaned_chunks = []
        for i, chunk in enumerate(chunks):
            chunk = chunk.strip()
            if not chunk:
                continue
            
            # 第一块通常是 YAML frontmatter 或者无标题的引言，原样保留
            # 后面的块加上一个标记，提示 LLM 这是一篇文档的某个子章节
            if i > 0:
                chunk = f"[Section / 章节]\n{chunk}"
                
            cleaned_chunks.append(chunk)
            
        return cleaned_chunks if cleaned_chunks else [content.strip()]

    def index_file(self, filepath: pathlib.Path | str):
        """读取一个 Markdown 文件，切块后丢进全文检索引擎。"""
        filepath = pathlib.Path(filepath)
        if not filepath.exists() or filepath.suffix != '.md':
            return
            
        content = filepath.read_text(encoding='utf-8')
        chunks = self._chunk_markdown(content)
        
        cursor = self.conn.cursor()
        
        # 幂等操作：先删掉这篇老文档的全部旧切块，防止更新时产生重复数据
        cursor.execute('DELETE FROM wiki_chunks WHERE filepath = ?', (str(filepath),))
        
        # 插入新的切块，生成 CJK 加空格的隐藏列以便支持中文检索
        for i, chunk in enumerate(chunks):
            content_indexed = re.sub(r'([\u4e00-\u9fff])', r'\1 ', chunk)
            cursor.execute(
                'INSERT INTO wiki_chunks (filepath, chunk_id, content, content_indexed) VALUES (?, ?, ?, ?)',
                (str(filepath), f"{filepath.name}#{i}", chunk, content_indexed)
            )
            
        self.conn.commit()

    def index_directory(self, dir_path: pathlib.Path | str) -> int:
        """递归扫描目录，索引所有 Markdown 文件。"""
        dir_path = pathlib.Path(dir_path)
        count = 0
        for md_file in dir_path.rglob("*.md"):
            self.index_file(md_file)
            count += 1
        return count

    def search(self, query: str, limit: int = 3) -> List[Dict[str, str]]:
        """
        供大模型调用的检索工具：返回最相关的文本块。
        利用 FTS5 原生的 BM25 算法 (FTS5 rank) 进行排序。
        """
        cursor = self.conn.cursor()
        
        # 为查询串中的每个中文字符插入空格，保证其与 content_indexed 匹配
        query_indexed = re.sub(r'([\u4e00-\u9fff])', r'\1 ', query)
        
        safe_query = " ".join(f'"{word}"' for word in query_indexed.split() if word.isalnum())
        if not safe_query:
            safe_query = f'"{query_indexed.replace('"', '""')}"'

        # FTS5 的 rank 分数是负数（越小越好），查询必须在虚拟表 wiki_chunks 上执行 MATCH
        sql = '''
            SELECT filepath, chunk_id, content, rank
            FROM wiki_chunks 
            WHERE wiki_chunks MATCH ? 
            ORDER BY rank 
            LIMIT ?
        '''
        
        try:
            cursor.execute(sql, (safe_query, limit))
        except sqlite3.OperationalError as e:
            # 极少数情况下兜底（比如纯符号搜索）
            fallback_query = f'"{query_indexed.replace('"', '""')}"'
            cursor.execute(sql, (fallback_query, limit))

        results = []
        for row in cursor.fetchall():
            results.append({
                "filepath": row[0],
                "chunk_id": row[1],
                "content": row[2],
                # 分数仅用于调试，实际喂给 LLM 时可以隐藏
                "score": round(row[3], 4) 
            })
            
        return results

if __name__ == '__main__':
    # ==========================================
    # 极简的测试用例 (可以直接 python indexer.py 跑)
    # ==========================================
    import tempfile
    
    print("🚀 正在测试本地 SQLite FTS 检索引擎...")
    with tempfile.TemporaryDirectory() as tmpdir:
        # 1. 模拟两篇知识库文章
        md1 = pathlib.Path(tmpdir) / "Apple.md"
        md1.write_text("# Apple Silicon\nM1 芯片基于 ARM 架构，功耗极低。\n\n# macOS\nmacOS Big Sur 引入了全新的 UI 设计。", encoding='utf-8')
        
        md2 = pathlib.Path(tmpdir) / "LLM.md"
        md2.write_text("# 大语言模型\nGPT-4 和 Claude 3 都是极强的大模型。\n\n# Tool Calling\n让模型调用外部 API 是 Agent 的核心能力。", encoding='utf-8')
        
        # 2. 初始化引擎并索引目录
        db_path = str(pathlib.Path(tmpdir) / "test.db")
        indexer = WikiIndexer(db_path=db_path)
        indexed_count = indexer.index_directory(tmpdir)
        print(f"✅ 成功索引了 {indexed_count} 篇文章")
        
        # 3. 测试搜索 (模拟 LLM 工具调用)
        query = "ARM 芯片"
        print(f"\n🔍 模拟 LLM 搜索: '{query}'")
        results = indexer.search(query)
        
        for res in results:
            print(f"\n📄 命中文件: {res['filepath']}")
            print(f"🎯 块 ID: {res['chunk_id']} (BM25 分数: {res['score']})")
            print(f"📝 内容摘要: {res['content'][:50]}...")
