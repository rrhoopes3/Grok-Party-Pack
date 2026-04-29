"""
Context Engine — adaptive context management inspired by OpenDev paper.

Four capabilities:
  1. Context Compaction  — progressively summarize old step outputs to prevent bloat
  2. Session Memory      — accumulate project knowledge across tasks
  3. Auto Model Routing  — select model based on task complexity
  4. Knowledge Graph     — graph-based session memory with entity relationships
                           (cross-pollinated from AgentOS, arXiv:2603.08938)
"""
from __future__ import annotations
import json
import logging
import re
import time as _time
from pathlib import Path
from forge.config import DATA_DIR

log = logging.getLogger("forge.context_engine")

# ── Context Compaction ────────────────────────────────────────────────────

# When context exceeds this character count, older steps get compacted
COMPACT_THRESHOLD = 6000
# Keep this many recent steps at full detail
KEEP_RECENT_STEPS = 2


def compact_context(context_so_far: str, current_step: int) -> str:
    """Progressively compact older step outputs to prevent context bloat.

    Keeps the most recent KEEP_RECENT_STEPS at full detail.
    Older steps are compressed to a single-line summary.
    """
    if len(context_so_far) < COMPACT_THRESHOLD:
        return context_so_far

    # Parse individual step blocks from context
    # Format: "\nStep N (title): output\n"
    step_pattern = re.compile(
        r"\nStep\s+(\d+)\s+\(([^)]+)\):\s*(.*?)(?=\nStep\s+\d+\s+\(|$)",
        re.DOTALL,
    )
    steps = list(step_pattern.finditer(context_so_far))

    if len(steps) <= KEEP_RECENT_STEPS:
        return context_so_far

    compacted_parts = []
    cutoff = len(steps) - KEEP_RECENT_STEPS

    for i, match in enumerate(steps):
        step_num = match.group(1)
        title = match.group(2)
        output = match.group(3).strip()

        if i < cutoff:
            # Compact: keep first 150 chars as summary
            summary = output[:150].replace("\n", " ").strip()
            if len(output) > 150:
                summary += "..."
            compacted_parts.append(f"\nStep {step_num} ({title}): [COMPACTED] {summary}\n")
        else:
            # Keep recent steps at full detail
            compacted_parts.append(f"\nStep {step_num} ({title}): {output}\n")

    result = "".join(compacted_parts)
    saved = len(context_so_far) - len(result)
    if saved > 0:
        log.info("Context compacted: saved %d chars (%d steps compacted)", saved, cutoff)
    return result


# ── Session Memory ────────────────────────────────────────────────────────

MEMORY_FILE = DATA_DIR / "session_memory.json"
MAX_MEMORIES = 50  # cap total stored memories


def _load_memories() -> list[dict]:
    if not MEMORY_FILE.exists():
        return []
    try:
        return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        log.warning("Corrupted session memory at %s — starting fresh", MEMORY_FILE)
        return []
    except Exception as exc:
        log.warning("Failed to load session memory: %s — starting fresh", exc)
        return []


def _save_memories(memories: list[dict]):
    # Keep only the most recent MAX_MEMORIES
    trimmed = memories[-MAX_MEMORIES:]
    MEMORY_FILE.write_text(json.dumps(trimmed, indent=2, default=str), encoding="utf-8")


def remember_task(task: str, tools_used: list[str], key_paths: list[str], outcome: str):
    """Store a learning from a completed task.

    Extracts and stores:
    - What the task was about (first 200 chars)
    - Which tools were effective
    - Key file paths discovered
    - Outcome summary
    """
    memories = _load_memories()

    memory = {
        "task": task[:200],
        "tools_effective": list(set(tools_used))[:10],
        "key_paths": key_paths[:10],
        "outcome": outcome[:300],
    }

    # Don't store duplicates (same task substring)
    task_prefix = task[:80].lower()
    memories = [m for m in memories if m.get("task", "")[:80].lower() != task_prefix]
    memories.append(memory)

    _save_memories(memories)
    log.info("Session memory updated: %d total memories", len(memories))


def recall_relevant(task: str, limit: int = 5) -> str:
    """Retrieve session memories relevant to a new task.

    Returns a formatted string to inject into the planner/executor prompt.
    Uses simple keyword overlap scoring.
    """
    memories = _load_memories()
    if not memories:
        return ""

    # Score each memory by keyword overlap with current task
    task_words = set(re.findall(r"\w+", task.lower()))

    scored = []
    for mem in memories:
        mem_words = set(re.findall(r"\w+", mem.get("task", "").lower()))
        mem_words.update(re.findall(r"\w+", " ".join(mem.get("key_paths", [])).lower()))
        overlap = len(task_words & mem_words)
        if overlap > 0:
            scored.append((overlap, mem))

    if not scored:
        return ""

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:limit]

    lines = ["[SESSION MEMORY — learnings from previous tasks]"]
    for _, mem in top:
        tools = ", ".join(mem.get("tools_effective", []))
        paths = ", ".join(mem.get("key_paths", []))
        lines.append(
            f"- Task: {mem['task']}\n"
            f"  Tools: {tools}\n"
            f"  Paths: {paths}\n"
            f"  Outcome: {mem.get('outcome', 'N/A')}"
        )
    lines.append("[END SESSION MEMORY]\n")
    return "\n".join(lines)


def extract_key_paths(step_outputs: list[str]) -> list[str]:
    """Extract file paths mentioned in step outputs for session memory."""
    paths = set()
    # Match common path patterns (Unix and Windows)
    path_pattern = re.compile(r'[A-Za-z]:[/\\][\w./\\-]+|/[\w./\\-]{3,}')
    for output in step_outputs:
        for match in path_pattern.findall(output):
            # Filter out very short or common noise
            if len(match) > 5 and not match.endswith(("/", "\\")):
                paths.add(match)
    return list(paths)[:15]


# ── Auto Model Routing ────────────────────────────────────────────────────

# Task complexity signals
_COMPLEX_SIGNALS = [
    r"\b(refactor|architect|redesign|migrate|optimize|overhaul)\b",
    r"\b(implement|build|create)\b.*\b(system|framework|pipeline|api|server|service)\b",
    r"\b(debug|investigate|diagnose)\b.*\b(complex|intermittent|race condition)\b",
    r"\bmulti[- ]?(file|step|component|module)\b",
    r"\b(security|vulnerability|audit|pentest)\b",
    r"\b(deploy|ci/?cd|infrastructure)\b",
    r"\b(authentication|database|schema|migration)\b",
]

_SIMPLE_SIGNALS = [
    r"\b(fix typo|rename|add comment|update readme|change color)\b",
    r"\b(list|show|display|print|echo|what is)\b",
    r"\b(read|check|look at|open)\b",
    r"\b(simple|quick|small|minor|trivial)\b",
]

# Model tiers — FAST_MODEL should be genuinely cheaper/faster than POWER_MODEL
FAST_MODEL = "grok-4-1-fast-non-reasoning"
POWER_MODEL = "grok-4.20-0309-reasoning"


def classify_task_complexity(task: str) -> str:
    """Classify a task as 'simple', 'moderate', or 'complex'.

    Used for auto model routing.
    """
    task_lower = task.lower()

    complex_score = sum(1 for pat in _COMPLEX_SIGNALS if re.search(pat, task_lower))
    simple_score = sum(1 for pat in _SIMPLE_SIGNALS if re.search(pat, task_lower))

    # Word count as a proxy for complexity
    word_count = len(task.split())
    if word_count > 50:
        complex_score += 1
    elif word_count < 15:
        simple_score += 1

    if complex_score >= 2:
        return "complex"
    if simple_score >= 2 and complex_score == 0:
        return "simple"
    return "moderate"


def auto_select_model(task: str, trust_ledger=None) -> str:
    """Auto-select the best executor model based on task complexity and trust.

    When a TrustLedger is provided, also considers historical trust scores
    to avoid routing to models that have been underperforming
    (cross-pollinated from arXiv:2602.11865 §4.2 trust calibration).

    Returns the model ID string.
    """
    complexity = classify_task_complexity(task)

    if complexity == "simple":
        model = FAST_MODEL
    elif complexity == "complex":
        model = POWER_MODEL
    else:
        # Moderate: use the fast reasoning model (good balance)
        model = FAST_MODEL

    # Trust-aware override: if the chosen model has low trust, swap
    if trust_ledger is not None:
        from forge.delegation import TRUST_REASSIGNMENT_THRESHOLD
        trust = trust_ledger.get_trust(model)
        if trust < TRUST_REASSIGNMENT_THRESHOLD:
            # Pick the alternative tier
            alt = POWER_MODEL if model == FAST_MODEL else FAST_MODEL
            alt_trust = trust_ledger.get_trust(alt)
            if alt_trust >= TRUST_REASSIGNMENT_THRESHOLD:
                log.info(
                    "Trust override: %s (trust=%.3f) → %s (trust=%.3f)",
                    model, trust, alt, alt_trust,
                )
                model = alt

    log.info("Auto-routing: task complexity=%s → model=%s", complexity, model)
    return model


# ── Personal Knowledge Graph ────────────────────────────────────────────────
# Cross-pollinated from AgentOS (arXiv:2603.08938 §4.1):
# "AgentOS processes multimodal interaction streams and applies NLP and
#  relational extraction techniques to update the PKG in real time, with
#  the Agent Kernel performing graph-augmented reasoning to infer implicit
#  user preferences."
#
# This is a lightweight graph that captures entity relationships discovered
# during task execution — tools → files, files → modules, tasks → outcomes.
# It supplements the flat session memory with relational structure.

PKG_FILE = DATA_DIR / "knowledge_graph.json"
MAX_NODES = 200
MAX_EDGES = 500


class KnowledgeGraph:
    """Personal Knowledge Graph for session-persistent relational memory.

    Nodes represent entities (files, tools, modules, tasks).
    Edges represent relationships (uses, produces, contains, depends_on).
    Supports graph-augmented recall: given a query entity, traverse
    relationships to find contextually related knowledge.
    """

    def __init__(self, path: Path | None = None):
        self._path = path or PKG_FILE
        self._nodes: dict[str, KGNode] = {}
        self._edges: list[KGEdge] = []
        self._load()

    def _load(self):
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for n in data.get("nodes", []):
                self._nodes[n["id"]] = KGNode(**n)
            self._edges = [KGEdge(**e) for e in data.get("edges", [])]
        except json.JSONDecodeError:
            log.warning("Corrupted knowledge graph at %s — starting fresh", self._path)
        except Exception as exc:
            log.warning("Failed to load knowledge graph: %s — starting fresh", exc)

    def _save(self):
        # Trim to limits
        if len(self._nodes) > MAX_NODES:
            # Keep most recently updated
            sorted_nodes = sorted(
                self._nodes.values(), key=lambda n: n.last_seen, reverse=True
            )
            self._nodes = {n.id: n for n in sorted_nodes[:MAX_NODES]}
            # Remove edges referencing deleted nodes
            valid_ids = set(self._nodes.keys())
            self._edges = [
                e for e in self._edges
                if e.source in valid_ids and e.target in valid_ids
            ]
        if len(self._edges) > MAX_EDGES:
            self._edges = self._edges[-MAX_EDGES:]

        data = {
            "nodes": [
                {"id": n.id, "kind": n.kind, "label": n.label,
                 "properties": n.properties, "last_seen": n.last_seen}
                for n in self._nodes.values()
            ],
            "edges": [
                {"source": e.source, "target": e.target,
                 "relation": e.relation, "weight": e.weight}
                for e in self._edges
            ],
        }
        self._path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    def add_node(self, node_id: str, kind: str, label: str = "", **properties):
        """Add or update a node in the graph."""
        if node_id in self._nodes:
            self._nodes[node_id].last_seen = _time.time()
            self._nodes[node_id].properties.update(properties)
        else:
            self._nodes[node_id] = KGNode(
                id=node_id, kind=kind,
                label=label or node_id,
                properties=properties,
                last_seen=_time.time(),
            )

    def add_edge(self, source: str, target: str, relation: str, weight: float = 1.0):
        """Add or strengthen a relationship edge."""
        for e in self._edges:
            if e.source == source and e.target == target and e.relation == relation:
                e.weight = min(e.weight + 0.5, 10.0)  # strengthen existing edge, cap at 10
                return
        self._edges.append(KGEdge(
            source=source, target=target, relation=relation, weight=weight,
        ))

    def record_task_knowledge(
        self,
        task: str,
        tools_used: list[str],
        key_paths: list[str],
        outcome: str,
    ):
        """Extract entities and relationships from a completed task.

        Builds graph nodes for the task, tools, and files, then
        creates edges representing how they relate.
        """
        task_id = f"task:{task[:60].lower().replace(' ', '_')}"
        self.add_node(task_id, "task", task[:80], outcome=outcome[:200])

        for tool in set(tools_used):
            tool_id = f"tool:{tool}"
            self.add_node(tool_id, "tool", tool)
            self.add_edge(task_id, tool_id, "used_tool")

        for path in key_paths:
            file_id = f"file:{path}"
            self.add_node(file_id, "file", path.split("/")[-1] if "/" in path else path)
            self.add_edge(task_id, file_id, "touched_file")

            # Infer tool→file relationships from co-occurrence
            for tool in tools_used:
                if tool in ("read_file", "write_file", "grep_files", "find_files"):
                    self.add_edge(f"tool:{tool}", file_id, "operated_on")

        self._save()
        log.info("Knowledge graph updated: %d nodes, %d edges",
                 len(self._nodes), len(self._edges))

    def query_related(self, entity: str, max_hops: int = 2, limit: int = 10) -> list[dict]:
        """Find entities related to a query entity via graph traversal.

        BFS up to max_hops, returning related nodes ranked by
        edge weight and proximity.
        """
        # Fuzzy match: find nodes whose ID contains the query
        start_nodes = [
            nid for nid in self._nodes
            if entity.lower() in nid.lower()
        ]
        if not start_nodes:
            return []

        visited = set(start_nodes)
        results = []
        frontier = [(nid, 0) for nid in start_nodes]

        while frontier:
            current_id, depth = frontier.pop(0)
            if depth >= max_hops:
                continue

            for edge in self._edges:
                neighbor = None
                if edge.source == current_id and edge.target not in visited:
                    neighbor = edge.target
                    rel_direction = "outgoing"
                elif edge.target == current_id and edge.source not in visited:
                    neighbor = edge.source
                    rel_direction = "incoming"

                if neighbor and neighbor in self._nodes:
                    visited.add(neighbor)
                    node = self._nodes[neighbor]
                    results.append({
                        "id": neighbor,
                        "kind": node.kind,
                        "label": node.label,
                        "relation": edge.relation,
                        "direction": rel_direction,
                        "weight": edge.weight,
                        "depth": depth + 1,
                    })
                    frontier.append((neighbor, depth + 1))

        # Rank by weight (descending) then depth (ascending)
        results.sort(key=lambda r: (-r["weight"], r["depth"]))
        return results[:limit]

    def recall_graph_context(self, task: str, limit: int = 5) -> str:
        """Build a context string from graph-related entities.

        Supplements the flat session memory with relational knowledge.
        """
        # Extract keywords from task to query the graph
        keywords = set(re.findall(r"\w{4,}", task.lower()))
        all_related = []

        for kw in list(keywords)[:5]:  # limit queries
            related = self.query_related(kw, max_hops=2, limit=3)
            all_related.extend(related)

        if not all_related:
            return ""

        # Deduplicate by ID
        seen = set()
        unique = []
        for r in all_related:
            if r["id"] not in seen:
                seen.add(r["id"])
                unique.append(r)

        if not unique:
            return ""

        lines = ["[KNOWLEDGE GRAPH — related entities from previous tasks]"]
        for r in unique[:limit]:
            props = ""
            node = self._nodes.get(r["id"])
            if node and node.properties:
                if "outcome" in node.properties:
                    props = f" (outcome: {node.properties['outcome'][:100]})"
            lines.append(
                f"- {r['kind']}: {r['label']} [{r['relation']}] weight={r['weight']:.1f}{props}"
            )
        lines.append("[END KNOWLEDGE GRAPH]\n")
        return "\n".join(lines)

    def record_correction(self, original_task_id: str, signal_type: str, description: str):
        """Record a user correction as a negative-weight edge.

        OpenClaw-RL (arXiv:2603.10165): user kills and resubmissions create
        negative edges that inform future context recall.
        """
        correction_id = f"correction:{signal_type}:{original_task_id}"
        task_id = f"task:{original_task_id}"
        self.add_node(correction_id, "correction", description)
        # Use add_edge with negative weight to signal dissatisfaction
        # add_edge normally strengthens, so we insert directly
        self._edges.append(KGEdge(
            source=task_id, target=correction_id,
            relation="user_corrected", weight=-1.0,
        ))
        self._save()
        log.info("Correction recorded: %s → %s (%s)", task_id, correction_id, description)

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        return len(self._edges)


class KGNode:
    """A node in the knowledge graph."""
    __slots__ = ("id", "kind", "label", "properties", "last_seen")

    def __init__(self, id: str, kind: str, label: str = "",
                 properties: dict | None = None, last_seen: float = 0.0):
        self.id = id
        self.kind = kind
        self.label = label or id
        self.properties = properties or {}
        self.last_seen = last_seen if last_seen is not None and last_seen != 0.0 else _time.time()


class KGEdge:
    """A relationship edge in the knowledge graph."""
    __slots__ = ("source", "target", "relation", "weight")

    def __init__(self, source: str, target: str, relation: str, weight: float = 1.0):
        self.source = source
        self.target = target
        self.relation = relation
        self.weight = weight
