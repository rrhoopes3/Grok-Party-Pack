from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from .registry import ToolRegistry


_WRITE_PREFIXES = (
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "REPLACE",
    "ATTACH", "DETACH", "VACUUM", "REINDEX", "INTO", "GRANT", "REVOKE",
)


def query_sqlite(database: str, query: str) -> str:
    """Read-only SQL query against a SQLite database file. Never commits."""
    p = Path(database)
    if not p.exists():
        return json.dumps({"error": f"Database not found: {database}"})

    q = (query or "").strip()
    if not q:
        return json.dumps({"error": "query required"})
    if ";" in q.rstrip(";"):
        return json.dumps({"error": "multiple SQL statements are not allowed"})
    head = q.lstrip("(").split(None, 1)
    verb = (head[0] if head else "").upper()
    if verb in _WRITE_PREFIXES or verb not in ("SELECT", "PRAGMA", "EXPLAIN", "WITH"):
        return json.dumps({"error": "read-only: only SELECT / PRAGMA / EXPLAIN / WITH queries are allowed"})

    try:
        uri = p.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only = ON")
            cursor = conn.cursor()
            cursor.execute(q)
            rows = cursor.fetchmany(100)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            data = [dict(row) for row in rows]
            result = {
                "columns": columns,
                "row_count": len(data),
                "rows": data,
            }
            if len(data) == 100:
                result["truncated"] = True
        finally:
            conn.close()
        output = json.dumps(result, default=str, separators=(",", ":"))
        return output[:6_000] if len(output) > 6_000 else output

    except sqlite3.Error as e:
        return json.dumps({"error": f"SQLite error: {e}"})
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


# -- Registration ------------------------------------------------------------

def register(registry: ToolRegistry):
    registry.register(
        name="query_sqlite",
        description="Read-only SQL query on a SQLite database file. SELECT/PRAGMA/EXPLAIN/WITH only (max 100 rows). Writes are rejected.",
        parameters={
            "type": "object",
            "properties": {
                "database": {"type": "string", "description": "Absolute path to the SQLite database file"},
                "query": {"type": "string", "description": "SQL query to execute"},
            },
            "required": ["database", "query"],
        },
        handler=query_sqlite,
    )
