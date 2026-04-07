"""Hybrid FTS5 + vector search over wiki markdown files."""

import math
import re
import sqlite3
import struct
from pathlib import Path

from llm_wiki import llm


def _cjk_space(text: str) -> str:
    """Insert spaces around CJK characters for better FTS tokenization."""
    return re.sub(r"([\u4e00-\u9fff])", r" \1 ", text)


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class WikiIndexer:
    """SQLite-based hybrid keyword + vector search engine."""

    def __init__(self, db_path: str, config: dict | None = None):
        self.db_path = db_path
        self.config = config or {}
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        cur = self.conn.cursor()
        cur.executescript("""
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                filepath UNINDEXED, chunk_id UNINDEXED,
                content UNINDEXED, content_indexed,
                tokenize='porter unicode61'
            );
            CREATE TABLE IF NOT EXISTS chunk_embeddings (
                chunk_id TEXT PRIMARY KEY,
                embedding BLOB
            );
        """)
        self.conn.commit()

    def _chunk_markdown(self, text: str) -> list[str]:
        """Split markdown by headings, dropping empty chunks."""
        parts = re.split(r"(?m)^#+\s+", text)
        return [p.strip() for p in parts if p.strip()]

    def index_file(self, filepath: str) -> int:
        """Index a single markdown file. Returns number of chunks indexed."""
        path = Path(filepath)
        if not path.exists() or path.suffix != ".md":
            return 0
        content = path.read_text(encoding="utf-8")
        chunks = self._chunk_markdown(content)
        rel = str(path)

        cur = self.conn.cursor()
        # Remove old entries for this file
        cur.execute("DELETE FROM chunks_fts WHERE filepath = ?", (rel,))
        cur.execute(
            "DELETE FROM chunk_embeddings WHERE chunk_id LIKE ?",
            (f"{rel}#%",),
        )

        for i, chunk in enumerate(chunks):
            chunk_id = f"{rel}#{i}"
            indexed = _cjk_space(chunk)
            cur.execute(
                "INSERT INTO chunks_fts (filepath, chunk_id, content, content_indexed) "
                "VALUES (?, ?, ?, ?)",
                (rel, chunk_id, chunk, indexed),
            )
            # Try embedding
            try:
                vecs = llm.embed([chunk], self.config)
                vec = vecs[0]
                blob = struct.pack(f"{len(vec)}f", *vec)
                cur.execute(
                    "INSERT OR REPLACE INTO chunk_embeddings (chunk_id, embedding) "
                    "VALUES (?, ?)",
                    (chunk_id, blob),
                )
            except Exception:
                pass  # keyword search still works

        self.conn.commit()
        return len(chunks)

    def index_directory(self, dir_path: str) -> int:
        """Recursively index all .md files under dir_path. Returns file count."""
        count = 0
        for md in sorted(Path(dir_path).rglob("*.md")):
            self.index_file(str(md))
            count += 1
        return count

    def rebuild(self, wiki_dir: str) -> int:
        """Drop everything and re-index from scratch."""
        cur = self.conn.cursor()
        cur.executescript("""
            DROP TABLE IF EXISTS chunks_fts;
            DROP TABLE IF EXISTS chunk_embeddings;
        """)
        self.conn.commit()
        self._create_tables()
        return self.index_directory(wiki_dir)

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """Hybrid FTS5 + vector search. Returns top results."""
        fts_results = self._fts_search(query, limit * 2)
        vec_results = self._vector_search(query, limit * 2)
        return self._merge(fts_results, vec_results, limit)

    def _fts_search(self, query: str, limit: int) -> list[dict]:
        """Full-text keyword search via FTS5."""
        indexed_query = _cjk_space(query)
        # Sanitize: quote each word to avoid FTS5 syntax errors from special chars
        words = indexed_query.split()
        safe_query = " ".join(f'"{w}"' for w in words if w.strip())
        if not safe_query:
            return []
        cur = self.conn.cursor()
        # Try word-quoted query; fall back to whole phrase
        for q in (safe_query, f'"{indexed_query}"'):
            try:
                rows = cur.execute(
                    "SELECT filepath, chunk_id, content, rank "
                    "FROM chunks_fts WHERE chunks_fts MATCH ? "
                    "ORDER BY rank LIMIT ?",
                    (q, limit),
                ).fetchall()
                break
            except sqlite3.OperationalError:
                rows = []
        if not rows:
            return []
        # Normalize BM25 scores (rank is negative in FTS5)
        raw = [(-r["rank"], dict(r)) for r in rows]
        max_score = max(s for s, _ in raw) or 1.0
        results = []
        for score, row in raw:
            results.append({
                "filepath": row["filepath"],
                "chunk_id": row["chunk_id"],
                "content": row["content"],
                "score": score / max_score,
            })
        return results

    def _vector_search(self, query: str, limit: int) -> list[dict]:
        """Embedding-based cosine similarity search."""
        try:
            q_vec = llm.embed([query], self.config)[0]
        except Exception:
            return []

        cur = self.conn.cursor()
        rows = cur.execute(
            "SELECT e.chunk_id, e.embedding, f.filepath, f.content "
            "FROM chunk_embeddings e "
            "JOIN chunks_fts f ON e.chunk_id = f.chunk_id"
        ).fetchall()
        if not rows:
            return []

        scored = []
        for row in rows:
            n = len(row["embedding"]) // 4
            if len(row["embedding"]) != n * 4 or n == 0:
                continue
            try:
                vec = list(struct.unpack(f"{n}f", row["embedding"]))
            except struct.error:
                continue
            sim = _cosine(q_vec, vec)
            scored.append({
                "filepath": row["filepath"],
                "chunk_id": row["chunk_id"],
                "content": row["content"],
                "score": sim,
            })
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]

    def _merge(
        self,
        fts: list[dict],
        vec: list[dict],
        limit: int,
    ) -> list[dict]:
        """Merge FTS and vector results with 50/50 weighting."""
        if not vec:
            return fts[:limit]
        if not fts:
            return vec[:limit]

        # Normalize vector scores to [0, 1]
        max_vec = max(r["score"] for r in vec) or 1.0
        vec_map: dict[str, dict] = {}
        for r in vec:
            r["score"] /= max_vec
            vec_map[r["chunk_id"]] = r

        combined: dict[str, dict] = {}
        for r in fts:
            cid = r["chunk_id"]
            fts_score = r["score"]
            vec_score = vec_map.pop(cid, {}).get("score", 0.0)
            combined[cid] = {**r, "score": 0.5 * fts_score + 0.5 * vec_score}
        for cid, r in vec_map.items():
            combined[cid] = {**r, "score": 0.5 * r["score"]}

        ranked = sorted(combined.values(), key=lambda x: x["score"], reverse=True)
        return ranked[:limit]

    def close(self):
        """Close the SQLite connection."""
        self.conn.close()
