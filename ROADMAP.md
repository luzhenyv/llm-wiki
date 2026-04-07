# WikiAgent: Future Roadmap

This document outlines the planned evolution of WikiAgent, building upon the core architecture established in Phase 1-4 (SQLite FTS5, Native `httpx` Tool Calling, ReAct Loop, and Markdown/YAML schema). 

The goal remains consistent: **Maintain a compounding, interlinked knowledge base with zero maintenance burden on the human user.**

---

## 🎯 Phase 5: Enhanced Ingestion & Tool Expansion (The "Hands-Off" Reader)
Currently, the LLM reads text provided directly in the prompt or from the local Wiki. We need to expand its ability to ingest external raw sources autonomously.

- [ ] **`read_raw_source(filepath)` Tool**: 
  - Add a tool specifically for reading immutable files from the `sources/` directory (PDFs, txt, markdown clippings).
  - *Implementation note*: Use `PyPDF2` or `pdfplumber` to extract text from PDFs before passing to the LLM to handle documents like the full 10-K.
- [ ] **Web Clipping Integration**:
  - Integrate with Obsidian Web Clipper or build a simple URL fetcher tool (`fetch_url(url)`) using `BeautifulSoup` to allow the Agent to ingest articles directly from the web.
- [ ] **Image Handling (Optional)**:
  - If using multimodal models (e.g., GPT-4o, Llama 3.2 Vision), add a tool to describe images (`analyze_image(path)`) downloaded alongside web clippings.

## 🧹 Phase 6: The "Lint" Workflow (The Autonomous Janitor)
The original specification emphasized the importance of maintaining the Wiki's health over time. We need a background process that acts as a librarian.

- [ ] **Periodic Health Checks (`lint.py`)**:
  - Create a standalone script that runs daily/weekly.
  - **Dead Link Checker**: A Python script to scan all `[[WikiLinks]]` and flag those that point to non-existent pages.
  - **Orphan Page Finder**: Identify pages with no incoming links.
- [ ] **Semantic Linting (LLM-Powered)**:
  - Feed the orphan pages or conflicting statements to the Agent.
  - *Prompt*: "Review these two pages. Do they contradict each other based on newer sources? If so, update the older page and add a deprecation note."
- [ ] **Automated Index Generation**:
  - Instead of relying solely on the LLM to update `index.md` via `write_page`, write a Python script that parses the YAML frontmatter of all files and automatically rebuilds `index.md` based on tags (e.g., grouping all `sector: Information Technology` companies).

## 🧠 Phase 7: Advanced Context Management (Scaling to 10,000 pages)
As the Wiki grows, even FTS5 chunking might overwhelm the context window if the LLM requests too many chunks.

- [ ] **Vector Search Fallback (Hybrid Search)**:
  - While FTS5 BM25 is excellent for keyword matching (e.g., "NVDA revenue"), semantic queries (e.g., "companies facing supply chain risks") require embeddings.
  - *Implementation*: Keep it simple. Use `SentenceTransformers` (local, CPU-friendly) to generate embeddings for the chunks already in the SQLite database. Add a `vector_blob` column to the FTS table.
- [ ] **Map-Reduce Summarization**:
  - If a user asks a query that spans 50 documents, implement a Map-Reduce workflow: The Agent spawns sub-agents to read subsets of the documents, summarize them, and then the main Agent synthesizes the final answer.

## 📊 Phase 8: Output Formatting & Export
The Wiki shouldn't just be a destination; it should be a starting point for creating deliverables.

- [ ] **Presentation Generator Tool (`generate_deck`)**:
  - Add a tool that allows the LLM to format its synthesis into [Marp](https://marp.app/) markdown syntax. 
  - *Use Case*: "Create a 5-slide presentation summarizing the S&P 500 semiconductor landscape based on the Wiki."
- [ ] **Dataview Integration**:
  - Ensure all YAML frontmatter written by the LLM is strictly compatible with the Obsidian Dataview plugin, allowing the human user to create dynamic tables (e.g., a table of all companies, sorted by `last_updated` date).

---
*Architecture Philosophy Reminder: Keep dependencies minimal. Prefer standard libraries, raw text, and simple schemas over complex frameworks. The Wiki is the codebase; the LLM is the compiler.*