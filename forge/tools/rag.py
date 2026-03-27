"""
RAG tool bindings — vector search tools for The Forge.

Tools:
    rag_ingest   — Ingest files or directories into the vector store
    rag_query    — Semantic search over ingested documents
    rag_status   — Show collection statistics
    rag_clear    — Clear all documents from the vector store
"""
from __future__ import annotations

import json
import logging

from .registry import ToolRegistry

log = logging.getLogger("forge.tools.rag")


def rag_ingest(
    path: str,
    glob: str = "**/*",
    max_files: int = 500,
) -> str:
    """Ingest a file or directory into the RAG vector store."""
    try:
        from forge.rag import get_store
        import os

        store = get_store()
        if os.path.isdir(path):
            result = store.ingest_directory(path, glob=glob, max_files=max_files)
        elif os.path.isfile(path):
            result = store.ingest_file(path)
        else:
            return json.dumps({"error": f"Path not found: {path}"})

        return json.dumps(result)

    except ImportError as e:
        return json.dumps({
            "error": f"RAG dependencies missing: {e}. Install: pip install chromadb"
        })
    except Exception as e:
        log.exception("rag_ingest failed")
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def rag_query(
    query: str,
    top_k: int = 5,
    source_filter: str = "",
) -> str:
    """Search the RAG vector store for semantically relevant documents."""
    try:
        from forge.rag import get_store

        store = get_store()
        where = None
        if source_filter:
            where = {"source": {"$contains": source_filter}}

        hits = store.query(query, top_k=top_k, where=where)

        results = []
        for hit in hits:
            results.append({
                "source": hit["metadata"].get("source", ""),
                "chunk_index": hit["metadata"].get("chunk_index", 0),
                "distance": round(hit["distance"], 4) if hit["distance"] is not None else None,
                "text": hit["document"][:1000],  # truncate for tool output
            })

        return json.dumps({
            "status": "ok",
            "query": query,
            "results": results,
            "count": len(results),
        })

    except ImportError as e:
        return json.dumps({
            "error": f"RAG dependencies missing: {e}. Install: pip install chromadb"
        })
    except Exception as e:
        log.exception("rag_query failed")
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def rag_status() -> str:
    """Get RAG vector store statistics."""
    try:
        from forge.rag import get_store
        store = get_store()
        return json.dumps({"status": "ok", **store.status()})
    except ImportError as e:
        return json.dumps({"error": f"RAG dependencies missing: {e}"})
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def rag_clear() -> str:
    """Clear all documents from the RAG vector store."""
    try:
        from forge.rag import get_store
        store = get_store()
        return json.dumps(store.clear())
    except ImportError as e:
        return json.dumps({"error": f"RAG dependencies missing: {e}"})
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


# ── Registration ─────────────────────────────────────────────────────────

def register(registry: ToolRegistry):
    """Register RAG tools with the Forge tool registry."""

    registry.register(
        name="rag_ingest",
        description=(
            "Ingest files or a directory into the RAG vector store for semantic search. "
            "Supports text, code, Markdown, and PDF files. Large files are chunked "
            "automatically with overlap. Use glob pattern to filter files when ingesting "
            "a directory (e.g. '**/*.py' for Python files only)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to a file or directory to ingest",
                },
                "glob": {
                    "type": "string",
                    "default": "**/*",
                    "description": "Glob pattern for directory ingestion (e.g. '**/*.py')",
                },
                "max_files": {
                    "type": "integer",
                    "default": 500,
                    "description": "Maximum files to ingest from a directory",
                },
            },
            "required": ["path"],
        },
        handler=rag_ingest,
    )

    registry.register(
        name="rag_query",
        description=(
            "Semantic search over documents ingested into the RAG vector store. "
            "Returns the most relevant text chunks ranked by cosine similarity. "
            "Use source_filter to narrow results to files matching a substring."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language search query",
                },
                "top_k": {
                    "type": "integer",
                    "default": 5,
                    "description": "Number of results to return",
                },
                "source_filter": {
                    "type": "string",
                    "description": "Filter results to sources containing this substring",
                },
            },
            "required": ["query"],
        },
        handler=rag_query,
    )

    registry.register(
        name="rag_status",
        description="Show RAG vector store statistics: collection name, document count, storage path.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=rag_status,
    )

    registry.register(
        name="rag_clear",
        description="Clear all documents from the RAG vector store. This is irreversible.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=rag_clear,
    )
