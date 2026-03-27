"""
RAG Pipeline — vector search for The Forge.

Ingest documents (text, PDF, code files) into a ChromaDB collection,
then retrieve semantically relevant chunks for agent context augmentation.

Requires: pip install chromadb sentence-transformers

Usage (programmatic):
    from forge.rag import RAGStore

    store = RAGStore()
    store.ingest_file("/path/to/doc.pdf")
    store.ingest_directory("/path/to/codebase", glob="**/*.py")
    results = store.query("How does authentication work?", top_k=5)

Usage (as Forge tool):
    - rag_ingest: Add files or directories to the vector store
    - rag_query: Semantic search over ingested documents
    - rag_status: Show collection stats
    - rag_clear: Clear the vector store
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("forge.rag")

# ── Text Splitting ───────────────────────────────────────────────────────

def _chunk_text(text: str, chunk_size: int = 800, overlap: int = 200) -> list[str]:
    """Split text into overlapping chunks by paragraph boundaries."""
    if len(text) <= chunk_size:
        return [text] if text.strip() else []

    # Split on double newlines first (paragraphs)
    paragraphs = text.split("\n\n")
    chunks = []
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if len(current) + len(para) + 2 <= chunk_size:
            current = current + "\n\n" + para if current else para
        else:
            if current:
                chunks.append(current)
            # If single paragraph is too large, split by sentences
            if len(para) > chunk_size:
                words = para.split()
                current = ""
                for word in words:
                    if len(current) + len(word) + 1 <= chunk_size:
                        current = current + " " + word if current else word
                    else:
                        if current:
                            chunks.append(current)
                        current = word
            else:
                current = para

    if current:
        chunks.append(current)

    # Add overlap
    if overlap > 0 and len(chunks) > 1:
        overlapped = [chunks[0]]
        for i in range(1, len(chunks)):
            prev_tail = chunks[i - 1][-overlap:]
            overlapped.append(prev_tail + "\n" + chunks[i])
        chunks = overlapped

    return chunks


def _extract_text(file_path: str) -> str:
    """Extract text content from a file."""
    path = Path(file_path)
    ext = path.suffix.lower()

    if ext == ".pdf":
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(str(path))
            text = ""
            for page in doc:
                text += page.get_text() + "\n"
            doc.close()
            return text
        except ImportError:
            log.warning("PyMuPDF not installed — skipping PDF: %s", path)
            return ""

    # Plain text / code files
    text_extensions = {
        ".txt", ".md", ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go",
        ".rs", ".c", ".cpp", ".h", ".hpp", ".cs", ".rb", ".php", ".sql",
        ".yaml", ".yml", ".toml", ".json", ".xml", ".html", ".css", ".sh",
        ".bat", ".ps1", ".r", ".scala", ".kt", ".swift", ".lua", ".pl",
        ".env", ".cfg", ".ini", ".conf", ".csv", ".log", ".rst",
    }
    if ext in text_extensions or ext == "":
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            log.warning("Failed to read %s: %s", path, e)
            return ""

    return ""


# ── RAG Store ────────────────────────────────────────────────────────────

class RAGStore:
    """ChromaDB-backed vector store for document retrieval."""

    def __init__(self, collection_name: str = "forge_rag", persist_dir: Path | None = None):
        if persist_dir is None:
            from forge.config import RAG_DATA_DIR
            persist_dir = RAG_DATA_DIR

        self._persist_dir = persist_dir
        self._collection_name = collection_name
        self._client = None
        self._collection = None

    def _ensure_client(self):
        """Lazy-init ChromaDB client and collection."""
        if self._client is None:
            import chromadb
            self._client = chromadb.PersistentClient(path=str(self._persist_dir))
            self._collection = self._client.get_or_create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            log.info(
                "RAG store ready: %s (%d documents)",
                self._collection_name,
                self._collection.count(),
            )

    def ingest_file(self, file_path: str, metadata: dict | None = None) -> dict:
        """Ingest a single file into the vector store."""
        self._ensure_client()
        path = Path(file_path)
        if not path.exists():
            return {"error": f"File not found: {file_path}"}

        text = _extract_text(file_path)
        if not text.strip():
            return {"error": f"No extractable text in: {file_path}", "skipped": True}

        chunks = _chunk_text(text)
        if not chunks:
            return {"error": "No chunks produced", "skipped": True}

        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        ids = []
        documents = []
        metadatas = []

        base_meta = {
            "source": str(path),
            "filename": path.name,
            "extension": path.suffix,
            "file_hash": file_hash,
            "ingested_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        if metadata:
            base_meta.update(metadata)

        for i, chunk in enumerate(chunks):
            doc_id = f"{file_hash}_{i}"
            ids.append(doc_id)
            documents.append(chunk)
            metadatas.append({**base_meta, "chunk_index": i, "total_chunks": len(chunks)})

        # Upsert (handles re-ingestion gracefully)
        self._collection.upsert(ids=ids, documents=documents, metadatas=metadatas)

        return {
            "status": "ok",
            "file": str(path),
            "chunks": len(chunks),
            "total_chars": sum(len(c) for c in chunks),
        }

    def ingest_directory(
        self,
        directory: str,
        glob: str = "**/*",
        max_files: int = 500,
    ) -> dict:
        """Ingest all matching files from a directory."""
        self._ensure_client()
        dir_path = Path(directory)
        if not dir_path.is_dir():
            return {"error": f"Directory not found: {directory}"}

        results = {"ingested": 0, "skipped": 0, "errors": 0, "total_chunks": 0}
        files = sorted(dir_path.glob(glob))[:max_files]

        for fpath in files:
            if not fpath.is_file():
                continue
            # Skip binary/large files
            if fpath.stat().st_size > 5_000_000:  # 5MB limit
                results["skipped"] += 1
                continue

            result = self.ingest_file(str(fpath))
            if result.get("status") == "ok":
                results["ingested"] += 1
                results["total_chunks"] += result.get("chunks", 0)
            elif result.get("skipped"):
                results["skipped"] += 1
            else:
                results["errors"] += 1

        results["status"] = "ok"
        results["directory"] = str(dir_path)
        return results

    def query(
        self,
        query_text: str,
        top_k: int = 5,
        where: dict | None = None,
    ) -> list[dict]:
        """Semantic search over the vector store."""
        self._ensure_client()

        kwargs: dict[str, Any] = {
            "query_texts": [query_text],
            "n_results": min(top_k, 50),
        }
        if where:
            kwargs["where"] = where

        results = self._collection.query(**kwargs)

        hits = []
        for i in range(len(results["ids"][0])):
            hit = {
                "id": results["ids"][0][i],
                "document": results["documents"][0][i],
                "distance": results["distances"][0][i] if results.get("distances") else None,
                "metadata": results["metadatas"][0][i] if results.get("metadatas") else {},
            }
            hits.append(hit)

        return hits

    def status(self) -> dict:
        """Return collection statistics."""
        self._ensure_client()
        count = self._collection.count()
        return {
            "collection": self._collection_name,
            "document_count": count,
            "persist_dir": str(self._persist_dir),
        }

    def clear(self) -> dict:
        """Clear all documents from the collection."""
        self._ensure_client()
        count = self._collection.count()
        if count > 0:
            # ChromaDB doesn't have a bulk delete-all, so we delete the collection and recreate
            self._client.delete_collection(self._collection_name)
            self._collection = self._client.get_or_create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": "cosine"},
            )
        return {"status": "ok", "deleted": count}


# ── Singleton ────────────────────────────────────────────────────────────

_store: RAGStore | None = None


def get_store() -> RAGStore:
    global _store
    if _store is None:
        _store = RAGStore()
    return _store
