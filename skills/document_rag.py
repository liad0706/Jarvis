"""Document RAG skill -- ingest documents and answer questions over them.

שליפת מידע ממסמכים -- קליטת מסמכים ומענה על שאלות על בסיס תוכנם.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from core.skill_base import BaseSkill

logger = logging.getLogger(__name__)

# Database path
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "documents.db"

# Chunking parameters
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50

# Max chunks returned per query
MAX_QUERY_RESULTS = 10

# Supported extensions and their readers
SUPPORTED_EXTENSIONS = {".txt", ".md", ".py", ".js", ".ts", ".json", ".csv", ".cfg",
                         ".ini", ".yaml", ".yml", ".toml", ".html", ".xml", ".log",
                         ".sh", ".bat", ".ps1", ".sql", ".pdf"}

# SQL for creating tables
CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT UNIQUE NOT NULL,
    file_name TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    chunk_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_documents_file_path ON documents(file_path);
"""


def _extract_keywords(text: str) -> str:
    """Extract meaningful keywords from text for search indexing."""
    # Lowercase, split on non-alphanumeric (keeping Hebrew chars)
    words = re.findall(r'[\w\u0590-\u05FF]+', text.lower())
    # Filter short words and common stop words
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "can", "shall", "to", "of", "in", "for",
        "on", "with", "at", "by", "from", "as", "into", "through", "during",
        "before", "after", "above", "below", "between", "out", "off", "over",
        "under", "again", "further", "then", "once", "here", "there", "when",
        "where", "why", "how", "all", "both", "each", "few", "more", "most",
        "other", "some", "such", "no", "nor", "not", "only", "own", "same",
        "so", "than", "too", "very", "just", "and", "but", "or", "if", "it",
        "its", "this", "that", "these", "those", "i", "me", "my", "we", "our",
        "you", "your", "he", "him", "his", "she", "her", "they", "them", "their",
        "what", "which", "who", "whom",
        # Hebrew stop words
        "\u05e9\u05dc", "\u05d4\u05d5\u05d0", "\u05d4\u05d9\u05d0",
        "\u05d0\u05ea", "\u05d6\u05d4", "\u05d6\u05d0\u05ea",
        "\u05e2\u05dc", "\u05d0\u05dc", "\u05dc\u05d0",
        "\u05d2\u05dd", "\u05d0\u05d5", "\u05db\u05d9",
    }
    keywords = [w for w in words if len(w) > 2 and w not in stop_words]
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for w in keywords:
        if w not in seen:
            seen.add(w)
            unique.append(w)
    return " ".join(unique)


def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks."""
    if not text.strip():
        return []

    chunks = []
    start = 0
    text_len = len(text)

    while start < text_len:
        end = start + chunk_size

        # Try to break at a natural boundary (newline, period, space)
        if end < text_len:
            # Look for a good break point near the end
            for sep in ["\n\n", "\n", ". ", " "]:
                pos = text.rfind(sep, start + chunk_size // 2, end + 50)
                if pos > start:
                    end = pos + len(sep)
                    break

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        start = end - overlap
        if start <= (end - chunk_size):
            # Avoid infinite loop for tiny text
            start = end

    return chunks


def _read_text_file(path: Path) -> str:
    """Read a text-based file."""
    encodings = ["utf-8", "utf-8-sig", "cp1255", "latin-1"]
    for enc in encodings:
        try:
            return path.read_text(encoding=enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def _read_pdf_file(path: Path) -> str:
    """Read a PDF file using PyPDF2."""
    try:
        import PyPDF2
    except ImportError:
        raise ImportError(
            "PyPDF2 is required to read PDF files. Install with: pip install PyPDF2"
        )

    text_parts = []
    with open(path, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)

    return "\n\n".join(text_parts)


def _read_document(path: Path) -> str:
    """Read a document file and return its text content."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _read_pdf_file(path)
    else:
        return _read_text_file(path)


class DocumentRAGSkill(BaseSkill):
    """Ingest documents and answer questions over them using keyword-based retrieval."""

    name = "document_rag"
    description = (
        "Ingest documents (txt, pdf, md, py, json, csv, etc.) and answer "
        "questions by searching their content. "
        "קליטת מסמכים ומענה על שאלות לפי תוכנם."
    )

    RISK_MAP = {
        "ingest_file": "low",
        "ask": "low",
        "list_documents": "low",
        "remove_document": "medium",
    }

    def __init__(self):
        self._db_initialized = False

    async def _get_db(self):
        """Get an aiosqlite connection, initializing tables if needed."""
        try:
            import aiosqlite
        except ImportError:
            raise ImportError(
                "aiosqlite is required. Install with: pip install aiosqlite"
            )

        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        db = await aiosqlite.connect(str(DB_PATH))
        db.row_factory = aiosqlite.Row

        if not self._db_initialized:
            await db.executescript(CREATE_TABLES_SQL)
            await db.commit()
            self._db_initialized = True

        return db

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        try:
            return await method(**(params or {}))
        except ImportError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.exception("document_rag.%s failed", action)
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------

    async def do_ingest_file(self, file_path: str) -> dict:
        """Read a document and store its chunks in the database for later retrieval. Supports txt, pdf, md, py, json, csv, and more. קליטת מסמך למאגר."""
        p = Path(file_path)
        if not p.exists():
            return {"error": f"File not found: {file_path}"}
        if not p.is_file():
            return {"error": f"Not a file: {file_path}"}
        if p.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return {
                "error": f"Unsupported file type: {p.suffix}. "
                f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            }

        # Read the document content (potentially blocking for PDFs)
        loop = asyncio.get_event_loop()
        try:
            text = await loop.run_in_executor(None, _read_document, p)
        except ImportError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"Failed to read file: {e}"}

        if not text.strip():
            return {"error": "File is empty or contains no readable text."}

        # Chunk the text
        chunks = _chunk_text(text)
        if not chunks:
            return {"error": "No chunks could be created from the document."}

        # Store in database
        db = await self._get_db()
        try:
            resolved_path = str(p.resolve())

            # Remove existing document if re-ingesting
            async with db.execute(
                "SELECT id FROM documents WHERE file_path = ?", (resolved_path,)
            ) as cursor:
                existing = await cursor.fetchone()

            if existing:
                doc_id = existing[0]
                await db.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
                await db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

            # Insert document
            now = datetime.now().isoformat(timespec="seconds")
            cursor = await db.execute(
                "INSERT INTO documents (file_path, file_name, ingested_at, chunk_count) "
                "VALUES (?, ?, ?, ?)",
                (resolved_path, p.name, now, len(chunks)),
            )
            doc_id = cursor.lastrowid

            # Insert chunks
            for i, chunk in enumerate(chunks):
                keywords = _extract_keywords(chunk)
                await db.execute(
                    "INSERT INTO chunks (doc_id, chunk_index, content, keywords) "
                    "VALUES (?, ?, ?, ?)",
                    (doc_id, i, chunk, keywords),
                )

            await db.commit()
        finally:
            await db.close()

        return {
            "status": "ok",
            "file": p.name,
            "path": resolved_path,
            "chunks_created": len(chunks),
            "total_chars": len(text),
            "message": f"Document '{p.name}' ingested successfully with {len(chunks)} chunks.",
        }

    async def do_ask(self, question: str) -> dict:
        """Search ingested documents for relevant content using keyword matching and return the best chunks. שהעירה על מסמכים."""
        if not question.strip():
            return {"error": "Question cannot be empty."}

        # Extract search keywords from the question
        query_words = set(re.findall(r'[\w\u0590-\u05FF]+', question.lower()))
        # Remove very short words
        query_words = {w for w in query_words if len(w) > 2}

        if not query_words:
            return {"error": "Question does not contain searchable keywords."}

        db = await self._get_db()
        try:
            # Get all chunks with their document info
            async with db.execute(
                "SELECT c.id, c.doc_id, c.chunk_index, c.content, c.keywords, "
                "d.file_name, d.file_path "
                "FROM chunks c JOIN documents d ON c.doc_id = d.id"
            ) as cursor:
                rows = await cursor.fetchall()

            if not rows:
                return {
                    "status": "empty",
                    "message": "No documents have been ingested yet. Use ingest_file first.",
                }

            # Score each chunk by keyword overlap
            scored = []
            for row in rows:
                chunk_keywords = set(row[4].split())  # keywords column
                # Also check the actual content for matches
                content_lower = row[3].lower()

                match_count = 0
                for qw in query_words:
                    if qw in chunk_keywords or qw in content_lower:
                        match_count += 1

                if match_count > 0:
                    score = match_count / len(query_words)
                    scored.append({
                        "chunk_id": row[0],
                        "doc_id": row[1],
                        "chunk_index": row[2],
                        "content": row[3],
                        "file_name": row[5],
                        "file_path": row[6],
                        "score": round(score, 3),
                        "matched_keywords": match_count,
                    })

            # Sort by score descending
            scored.sort(key=lambda x: x["score"], reverse=True)
            top_results = scored[:MAX_QUERY_RESULTS]

        finally:
            await db.close()

        if not top_results:
            return {
                "status": "no_results",
                "question": question,
                "message": "No relevant content found in ingested documents.",
            }

        return {
            "status": "ok",
            "question": question,
            "result_count": len(top_results),
            "results": top_results,
        }

    async def do_list_documents(self) -> dict:
        """List all ingested documents with their metadata. רשימת מסמכים במאגר."""
        db = await self._get_db()
        try:
            async with db.execute(
                "SELECT id, file_path, file_name, ingested_at, chunk_count "
                "FROM documents ORDER BY ingested_at DESC"
            ) as cursor:
                rows = await cursor.fetchall()

            documents = []
            for row in rows:
                documents.append({
                    "id": row[0],
                    "file_path": row[1],
                    "file_name": row[2],
                    "ingested_at": row[3],
                    "chunk_count": row[4],
                })

        finally:
            await db.close()

        return {
            "status": "ok",
            "count": len(documents),
            "documents": documents,
        }

    async def do_remove_document(self, file_path: str) -> dict:
        """Remove a document and its chunks from the index. הסרת מסמך מהמאגר."""
        p = Path(file_path)
        resolved_path = str(p.resolve())

        db = await self._get_db()
        try:
            async with db.execute(
                "SELECT id, file_name, chunk_count FROM documents WHERE file_path = ?",
                (resolved_path,),
            ) as cursor:
                row = await cursor.fetchone()

            if not row:
                # Also try the original path as given
                async with db.execute(
                    "SELECT id, file_name, chunk_count FROM documents WHERE file_path = ?",
                    (file_path,),
                ) as cursor:
                    row = await cursor.fetchone()

            if not row:
                return {"error": f"Document not found in index: {file_path}"}

            doc_id, file_name, chunk_count = row[0], row[1], row[2]

            await db.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            await db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            await db.commit()

        finally:
            await db.close()

        return {
            "status": "ok",
            "file_name": file_name,
            "chunks_removed": chunk_count,
            "message": f"Document '{file_name}' removed from index ({chunk_count} chunks deleted).",
        }
