"""
Agent-to-Agent Conversation Protocol.

Enables multi-turn negotiations between agents in the Forge marketplace.
Two agents can hold a structured conversation with turns, goals, and
an optional moderator that enforces time/turn limits.

Usage (programmatic):
    from forge.agents.conversation import Conversation

    conv = Conversation(
        agent_a="trader-bot",
        agent_b="analyst-bot",
        topic="Should we go long on NVDA?",
        max_turns=10,
    )
    conv.start()  # returns conversation_id

Usage (API):
    POST /api/v1/conversations           — start a conversation
    GET  /api/v1/conversations/<id>       — get conversation state
    POST /api/v1/conversations/<id>/reply — add a turn (agent auth required)
    POST /api/v1/conversations/<id>/close — close the conversation
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger("forge.agents.conversation")


@dataclass
class Turn:
    """A single turn in a conversation."""
    speaker: str           # agent_id
    content: str
    timestamp: float = 0.0
    turn_number: int = 0
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()


@dataclass
class Conversation:
    """A multi-turn conversation between two agents."""
    id: str = ""
    agent_a: str = ""                  # initiator
    agent_b: str = ""                  # responder
    topic: str = ""
    goal: str = ""                     # what the conversation should achieve
    turns: list[Turn] = field(default_factory=list)
    max_turns: int = 20
    turn_timeout_seconds: float = 60.0  # max time per turn
    status: str = "pending"            # pending | active | closed | timeout
    created_at: float = 0.0
    closed_at: float = 0.0
    outcome: str = ""                  # summary of what was decided
    next_speaker: str = ""             # whose turn it is

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:12]
        if not self.created_at:
            self.created_at = time.time()
        if not self.next_speaker:
            self.next_speaker = self.agent_a

    def add_turn(self, speaker: str, content: str, metadata: dict | None = None) -> Turn:
        """Add a turn to the conversation."""
        if self.status not in ("pending", "active"):
            raise ValueError(f"Conversation is {self.status}, cannot add turn")
        if speaker != self.next_speaker:
            raise ValueError(f"Not {speaker}'s turn — waiting for {self.next_speaker}")

        turn = Turn(
            speaker=speaker,
            content=content,
            turn_number=len(self.turns) + 1,
            metadata=metadata or {},
        )
        self.turns.append(turn)
        self.status = "active"

        # Alternate turns
        self.next_speaker = self.agent_b if speaker == self.agent_a else self.agent_a

        # Check turn limit
        if len(self.turns) >= self.max_turns:
            self.close("Turn limit reached")

        return turn

    def close(self, outcome: str = "") -> None:
        """Close the conversation."""
        self.status = "closed"
        self.closed_at = time.time()
        self.outcome = outcome

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "agent_a": self.agent_a,
            "agent_b": self.agent_b,
            "topic": self.topic,
            "goal": self.goal,
            "status": self.status,
            "turn_count": len(self.turns),
            "max_turns": self.max_turns,
            "next_speaker": self.next_speaker,
            "created_at": self.created_at,
            "closed_at": self.closed_at,
            "outcome": self.outcome,
            "turns": [asdict(t) for t in self.turns],
        }

    def transcript(self) -> str:
        """Render the conversation as a human-readable transcript."""
        lines = [f"=== Conversation: {self.topic} ==="]
        lines.append(f"Between: {self.agent_a} and {self.agent_b}")
        lines.append(f"Status: {self.status}\n")
        for turn in self.turns:
            lines.append(f"[Turn {turn.turn_number}] {turn.speaker}:")
            lines.append(f"  {turn.content}\n")
        if self.outcome:
            lines.append(f"Outcome: {self.outcome}")
        return "\n".join(lines)


# ── Conversation Manager ────────────────────────────────────────────────

class ConversationManager:
    """Manages active conversations between agents."""

    def __init__(self, data_dir: Path | None = None):
        if data_dir is None:
            from forge.config import DATA_DIR
            data_dir = DATA_DIR
        self._data_dir = data_dir / "conversations_agent"
        self._data_dir.mkdir(exist_ok=True)
        self._conversations: dict[str, Conversation] = {}
        self._lock = threading.Lock()

    def create(
        self,
        agent_a: str,
        agent_b: str,
        topic: str,
        goal: str = "",
        max_turns: int = 20,
        turn_timeout_seconds: float = 60.0,
    ) -> Conversation:
        """Create a new conversation."""
        conv = Conversation(
            agent_a=agent_a,
            agent_b=agent_b,
            topic=topic,
            goal=goal,
            max_turns=max_turns,
            turn_timeout_seconds=turn_timeout_seconds,
        )
        with self._lock:
            self._conversations[conv.id] = conv
        self._persist(conv)
        log.info("Created conversation %s: %s ↔ %s", conv.id, agent_a, agent_b)
        return conv

    def get(self, conversation_id: str) -> Conversation | None:
        return self._conversations.get(conversation_id)

    def reply(self, conversation_id: str, speaker: str, content: str) -> Turn:
        """Add a reply to a conversation. Validates speaker is the next_speaker."""
        conv = self._conversations.get(conversation_id)
        if not conv:
            raise ValueError(f"Conversation not found: {conversation_id}")

        turn = conv.add_turn(speaker, content)
        self._persist(conv)
        return turn

    def close(self, conversation_id: str, outcome: str = "") -> None:
        """Close a conversation."""
        conv = self._conversations.get(conversation_id)
        if conv:
            conv.close(outcome)
            self._persist(conv)

    def list_active(self, agent_id: str = "") -> list[Conversation]:
        """List active conversations, optionally filtered by participant."""
        convs = list(self._conversations.values())
        if agent_id:
            convs = [c for c in convs if agent_id in (c.agent_a, c.agent_b)]
        return [c for c in convs if c.status in ("pending", "active")]

    def list_all(self, limit: int = 50) -> list[Conversation]:
        """List all conversations, most recent first."""
        convs = sorted(self._conversations.values(), key=lambda c: c.created_at, reverse=True)
        return convs[:limit]

    def _persist(self, conv: Conversation) -> None:
        """Save conversation to disk."""
        path = self._data_dir / f"conv_{conv.id}.json"
        with open(path, "w") as f:
            json.dump(conv.to_dict(), f, indent=2)


# ── Singleton ────────────────────────────────────────────────────────────

_manager: ConversationManager | None = None


def get_manager() -> ConversationManager:
    global _manager
    if _manager is None:
        _manager = ConversationManager()
    return _manager


# ── Flask Blueprint ──────────────────────────────────────────────────────

def create_blueprint():
    """Create Flask blueprint for conversation API endpoints."""
    from flask import Blueprint, request, jsonify
    from forge.security import require_auth

    bp = Blueprint("conversations", __name__, url_prefix="/api/v1/conversations")

    @bp.route("", methods=["POST"])
    @require_auth
    def create_conversation():
        data = request.get_json()
        agent_a = data.get("agent_a", "").strip()
        agent_b = data.get("agent_b", "").strip()
        topic = data.get("topic", "").strip()
        if not agent_a or not agent_b or not topic:
            return jsonify({"error": "agent_a, agent_b, and topic are required"}), 400

        mgr = get_manager()
        conv = mgr.create(
            agent_a=agent_a,
            agent_b=agent_b,
            topic=topic,
            goal=data.get("goal", ""),
            max_turns=data.get("max_turns", 20),
        )
        return jsonify({"status": "ok", "conversation": conv.to_dict()}), 201

    @bp.route("/<conv_id>", methods=["GET"])
    def get_conversation(conv_id):
        mgr = get_manager()
        conv = mgr.get(conv_id)
        if not conv:
            return jsonify({"error": "Conversation not found"}), 404
        return jsonify({"status": "ok", "conversation": conv.to_dict()})

    @bp.route("/<conv_id>/reply", methods=["POST"])
    @require_auth
    def reply(conv_id):
        data = request.get_json()
        speaker = data.get("speaker", "").strip()
        content = data.get("content", "").strip()
        if not speaker or not content:
            return jsonify({"error": "speaker and content are required"}), 400

        mgr = get_manager()
        try:
            turn = mgr.reply(conv_id, speaker, content)
            conv = mgr.get(conv_id)
            return jsonify({
                "status": "ok",
                "turn": asdict(turn),
                "conversation_status": conv.status,
                "next_speaker": conv.next_speaker,
            })
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    @bp.route("/<conv_id>/close", methods=["POST"])
    @require_auth
    def close_conversation(conv_id):
        data = request.get_json() or {}
        outcome = data.get("outcome", "")
        mgr = get_manager()
        conv = mgr.get(conv_id)
        if not conv:
            return jsonify({"error": "Conversation not found"}), 404
        mgr.close(conv_id, outcome)
        return jsonify({"status": "ok"})

    @bp.route("", methods=["GET"])
    def list_conversations():
        mgr = get_manager()
        agent = request.args.get("agent", "")
        if agent:
            convs = mgr.list_active(agent)
        else:
            convs = mgr.list_all(limit=50)
        return jsonify({
            "status": "ok",
            "conversations": [c.to_dict() for c in convs],
        })

    return bp
