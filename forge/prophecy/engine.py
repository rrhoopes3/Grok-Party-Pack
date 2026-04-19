"""Prophecy Engine — Swarm-intelligence prediction simulation.

Inspired by MiroFish's multi-agent social simulation engine, this module runs
self-contained prediction simulations entirely within the Forge's LLM providers.
No external dependencies (no Zep, no OASIS, no Camel) — just raw LLM power
driving a society of opinionated prophets who argue their way to the future.

Architecture:
    1. SEED    — LLM generates a world model + diverse agent roster from seed material
    2. IGNITE  — Agents take initial positions on the topic
    3. CHURN   — N rounds of interaction: posts, replies, reactions, position shifts
    4. DISTILL — LLM analyzes the simulation transcript and extracts a prediction
    5. REPORT  — Structured prediction report with confidence, consensus, dissent

Supports two deliberation modes:
  - HIVEMIND: Single batched LLM call per round (fast, cheap — one voice puppeteers all)
  - INDEPENDENT: Each prophet gets own parallel LLM call (richer, costlier — true swarm)
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from forge.config import (
    XAI_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY,
    LMSTUDIO_BASE_URL, OLLAMA_BASE_URL, DATA_DIR,
)
from forge.prophecy.types import (
    ActionType, AgentAction, DeliberationMode, Prophet, ProphecyReport,
    ProphecySimulation, ProphecyWorld, ProphetPersonality, RoundState,
    SimulationStatus, SimulationType, WorldEvent,
)

log = logging.getLogger("forge.prophecy")

# ── Persistence ──────────────────────────────────────────────────────────────

PROPHECY_DIR = DATA_DIR / "prophecy"
PROPHECY_DIR.mkdir(parents=True, exist_ok=True)


# ── LLM Abstraction ─────────────────────────────────────────────────────────

def _llm_call(
    prompt: str,
    system: str = "",
    model: str = "",
    temperature: float = 0.9,
    max_tokens: int = 4096,
) -> str:
    """Make a single LLM call using the best available provider.

    Tries xAI first (since the Forge is xAI-native), falls back to Anthropic,
    then OpenAI, then local. If a specific model is passed, routes to the
    appropriate provider.
    """
    # Determine provider + model
    if not model:
        if XAI_API_KEY:
            model = "grok-4-1-fast-non-reasoning"
            provider = "xai"
        elif ANTHROPIC_API_KEY:
            model = "claude-haiku-4-20250414"
            provider = "anthropic"
        elif OPENAI_API_KEY:
            model = "gpt-4o-mini"
            provider = "openai"
        else:
            model = "default"
            provider = "lmstudio"
    else:
        if model.startswith("claude-"):
            provider = "anthropic"
        elif model.startswith(("gpt-", "o1-", "o3-", "o4-", "chatgpt-")):
            provider = "openai"
        elif model.startswith("lmstudio:"):
            provider = "lmstudio"
        elif model.startswith("ollama:"):
            provider = "ollama"
        else:
            provider = "xai"

    if provider == "anthropic":
        return _call_anthropic(prompt, system, model, temperature, max_tokens)
    elif provider in ("openai", "lmstudio", "ollama"):
        base_url = None
        api_key = OPENAI_API_KEY or "local"
        if provider == "lmstudio":
            base_url = LMSTUDIO_BASE_URL
            api_key = "lm-studio"
            model = model.removeprefix("lmstudio:") or "default"
        elif provider == "ollama":
            base_url = OLLAMA_BASE_URL
            api_key = "ollama"
            model = model.removeprefix("ollama:") or "default"
        return _call_openai_compat(prompt, system, model, temperature, max_tokens, base_url, api_key)
    else:
        # xAI — use OpenAI-compatible endpoint
        return _call_openai_compat(
            prompt, system, model, temperature, max_tokens,
            base_url="https://api.x.ai/v1",
            api_key=XAI_API_KEY or "",
        )


# Reasoning / newest-gen models that 400 on `temperature`. Anthropic Claude
# 4.5+ and OpenAI o-series / GPT-5 tier all refuse it. Detect by name
# prefix and drop the knob.
def _model_rejects_temperature(model: str) -> bool:
    m = (model or "").lower()
    if m.startswith(("claude-opus-4-7", "claude-sonnet-4-7", "claude-haiku-4-5",
                     "claude-opus-4-5", "claude-sonnet-4-5")):
        return True
    if m.startswith(("o1-", "o3-", "o4-", "gpt-5")):
        return True
    return False


# OpenAI renamed `max_tokens` → `max_completion_tokens` on o-series + GPT-5.
# Anthropic kept max_tokens. xAI/LM Studio/Ollama still accept max_tokens.
def _model_uses_max_completion_tokens(model: str) -> bool:
    m = (model or "").lower()
    return m.startswith(("o1-", "o3-", "o4-", "gpt-5"))


def _call_anthropic(prompt: str, system: str, model: str, temperature: float, max_tokens: int) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system
    if temperature is not None and not _model_rejects_temperature(model):
        kwargs["temperature"] = min(temperature, 1.0)  # Anthropic caps at 1.0
    resp = client.messages.create(**kwargs)
    return resp.content[0].text


def _call_openai_compat(
    prompt: str, system: str, model: str, temperature: float, max_tokens: int,
    base_url: str | None = None, api_key: str = "",
) -> str:
    from openai import OpenAI
    kwargs: dict[str, Any] = {"api_key": api_key or "none"}
    if base_url:
        kwargs["base_url"] = base_url
    client = OpenAI(**kwargs)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    call_kwargs: dict[str, Any] = {
        "model": model, "messages": messages,
    }
    if _model_uses_max_completion_tokens(model):
        call_kwargs["max_completion_tokens"] = max_tokens
    else:
        call_kwargs["max_tokens"] = max_tokens
    if not _model_rejects_temperature(model):
        call_kwargs["temperature"] = temperature
    resp = client.chat.completions.create(**call_kwargs)
    return resp.choices[0].message.content or ""


# ── JSON Extraction ──────────────────────────────────────────────────────────

def _extract_json(text: str) -> Any:
    """Extract JSON from LLM output that may have markdown fences or preamble."""
    # Try the whole text first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code fences
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Try finding the first { ... } or [ ... ] block
    for open_char, close_char in [("{", "}"), ("[", "]")]:
        start = text.find(open_char)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == open_char:
                depth += 1
            elif text[i] == close_char:
                depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    break

    raise ValueError(f"Could not extract JSON from LLM output:\n{text[:500]}")


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1: SEED — Generate World + Prophets
# ═══════════════════════════════════════════════════════════════════════════════

WORLD_SYSTEM = """You are the Prophecy Engine — a world-builder for predictive simulations.
Your role is to construct a rich simulation world from seed material, then populate it
with diverse agents who will debate and predict outcomes through social interaction.

You must produce structured JSON. Be creative but grounded in reality."""


def _generate_world(sim: ProphecySimulation) -> ProphecyWorld:
    """Generate the world model from seed material."""
    prompt = f"""Analyze this topic and create a simulation world for predictive debate.

TOPIC: {sim.seed_topic}

{"SEED MATERIAL:" + chr(10) + sim.seed_material[:8000] if sim.seed_material else "No additional seed material provided."}

Generate a JSON object with these fields:
{{
    "topic": "precise question being predicted (rewrite for clarity if needed)",
    "context": "2-3 paragraph background providing the full context — key facts, history, current state",
    "simulation_type": "one of: social_media, market, geopolitical, election, crisis, custom",
    "key_variables": ["list of 4-8 factors that will determine the outcome"],
    "possible_outcomes": ["list of 3-6 distinct possible outcomes, from most to least likely"],
    "initial_conditions": "description of the starting state of the world",
    "rules": "any special rules or dynamics that should govern this simulation"
}}

Be specific and substantive. The context should contain real-world information."""

    raw = _llm_call(prompt, system=WORLD_SYSTEM, model=sim.model, temperature=0.7, max_tokens=3000)
    data = _extract_json(raw)
    # Guard against LLM returning a list instead of a dict
    if isinstance(data, list):
        data = data[0] if data else {}
    return ProphecyWorld(**data)


def _generate_prophets(sim: ProphecySimulation) -> list[Prophet]:
    """Generate a diverse roster of simulation agents."""
    world = sim.world
    prompt = f"""Create {sim.num_prophets} diverse agents ("prophets") for this prediction simulation.

TOPIC: {world.topic}
CONTEXT: {world.context}
POSSIBLE OUTCOMES: {json.dumps(world.possible_outcomes)}
SIMULATION TYPE: {world.simulation_type}

Design agents with MAXIMUM DIVERSITY:
- Different professional backgrounds (analysts, laypeople, insiders, academics, contrarians)
- Different personality types (cautious, bold, data-driven, intuitive, cynical, optimistic)
- Different initial positions across the possible outcomes
- Some should be highly influential, others more reactive/follower-types
- Include at least one wild-card contrarian and one consensus-builder

Return a JSON array of {sim.num_prophets} agents:
[
    {{
        "name": "distinctive realistic name",
        "role": "their professional/social role relevant to this topic",
        "background": "2-3 sentences: who they are, what they know, why they care",
        "personality": {{
            "archetype": "short label like 'Contrarian Analyst' or 'Cautious Insider'",
            "openness": 0.0-1.0,
            "influence": 0.0-1.0,
            "risk_tolerance": 0.0-1.0,
            "knowledge_domains": ["relevant domains"],
            "biases": ["specific cognitive biases they exhibit"]
        }},
        "initial_position": "their starting prediction/opinion on the topic (1-2 sentences)",
        "confidence": 0.0-1.0
    }},
    ...
]

Make each agent feel like a real, distinct person with genuine expertise and blind spots."""

    raw = _llm_call(prompt, system=WORLD_SYSTEM, model=sim.model, temperature=0.95, max_tokens=6000)
    data = _extract_json(raw)

    prophets = []
    for i, agent_data in enumerate(data):
        # Normalize personality
        pers_data = agent_data.get("personality", {})
        personality = ProphetPersonality(
            archetype=pers_data.get("archetype", "Unknown"),
            openness=float(pers_data.get("openness", 0.5)),
            influence=float(pers_data.get("influence", 0.5)),
            risk_tolerance=float(pers_data.get("risk_tolerance", 0.5)),
            knowledge_domains=pers_data.get("knowledge_domains", []),
            biases=pers_data.get("biases", []),
        )
        prophet = Prophet(
            id=f"prophet_{i:02d}",
            name=agent_data["name"],
            role=agent_data.get("role", "Unknown"),
            background=agent_data.get("background", ""),
            personality=personality,
            initial_position=agent_data.get("initial_position", ""),
            current_position=agent_data.get("initial_position", ""),
            confidence=float(agent_data.get("confidence", 0.5)),
        )
        prophets.append(prophet)

    return prophets


def seed_simulation(sim: ProphecySimulation, progress_cb: Callable[[str], None] | None = None) -> ProphecySimulation:
    """Phase 1: Generate world model and agent roster from seed material."""
    def emit(msg: str):
        log.info(msg)
        if progress_cb:
            progress_cb(msg)

    sim.status = SimulationStatus.SEEDING
    emit(f"[SEED] Generating world model for: {sim.seed_topic[:100]}")

    try:
        sim.world = _generate_world(sim)
        emit(f"[SEED] World created: {sim.world.simulation_type} — {len(sim.world.possible_outcomes)} outcomes")

        sim.prophets = _generate_prophets(sim)
        emit(f"[SEED] {len(sim.prophets)} prophets generated")

        # Log the roster
        for p in sim.prophets:
            emit(f"  → {p.name} ({p.personality.archetype}): \"{p.initial_position[:80]}\"")

        sim.save(PROPHECY_DIR)
        return sim

    except Exception as e:
        sim.status = SimulationStatus.FAILED
        sim.error = f"Seed failed: {type(e).__name__}: {e}"
        log.exception("Seed phase failed")
        sim.save(PROPHECY_DIR)
        raise


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2: CHURN — Run Simulation Rounds
# ═══════════════════════════════════════════════════════════════════════════════

ROUND_SYSTEM = """You are the Prophecy Engine's simulation controller. You orchestrate
a round of social interaction between prediction agents ("prophets").

Each prophet has a personality, current position, and memory. You must simulate
ALL prophets taking actions this round — posting opinions, replying to each other,
reacting, and potentially updating their positions.

Your simulation must feel organic: agents respond to what others said, form alliances,
get into arguments, and evolve their thinking. The most influential agents should
drive conversation while others react. Contrarians should push back. Consensus-builders
should try to find middle ground.

Output STRICT JSON only."""


def _run_round(
    sim: ProphecySimulation,
    round_num: int,
    injected_events: list[WorldEvent] | None = None,
) -> RoundState:
    """Execute a single simulation round — all agents act in one LLM call."""
    world = sim.world
    prophets = [p for p in sim.prophets if p.active]

    # Build agent state summary
    agent_states = []
    for p in prophets:
        recent_memory = p.memory[-3:] if p.memory else ["(no prior events)"]
        agent_states.append({
            "id": p.id,
            "name": p.name,
            "role": p.role,
            "archetype": p.personality.archetype,
            "openness": p.personality.openness,
            "influence": p.personality.influence,
            "biases": p.personality.biases,
            "current_position": p.current_position,
            "confidence": p.confidence,
            "recent_memory": recent_memory,
        })

    # Build event injection text
    event_text = ""
    if injected_events:
        event_text = "\n\nBREAKING EVENTS THIS ROUND:\n"
        for ev in injected_events:
            event_text += f"  — {ev.title}: {ev.description}\n"
        event_text += "\nAgents MUST react to these events. They significantly affect the prediction landscape.\n"

    # Previous round context
    prev_context = ""
    if sim.rounds:
        last = sim.rounds[-1]
        if last.round_summary:
            prev_context = f"\nLAST ROUND SUMMARY:\n{last.round_summary}\n"
        # Include a few key actions
        if last.actions:
            prev_context += "\nKey recent statements:\n"
            for act in last.actions[:6]:
                prev_context += f'  {act.prophet_name}: "{act.content[:120]}"\n'

    prompt = f"""SIMULATION ROUND {round_num} of {sim.num_rounds}

TOPIC: {world.topic}
WORLD STATE: {world.current_state or world.initial_conditions}
POSSIBLE OUTCOMES: {json.dumps(world.possible_outcomes)}
{prev_context}{event_text}
ACTIVE PROPHETS ({len(prophets)}):
{json.dumps(agent_states, indent=2)}

Simulate this round. Each prophet should take 1-3 actions based on their personality:
- High-influence agents POST original analyses or REPLY to challenge others
- High-openness agents may UPDATE_POSITION if convinced by arguments
- Low-openness agents dig in and DISSENT from emerging consensus
- Some agents REACT (agree/disagree) to recent statements
- Occasionally agents form ALLIANCE or DISSENT from groups

Return JSON:
{{
    "actions": [
        {{
            "prophet_id": "prophet_XX",
            "prophet_name": "Name",
            "action_type": "post|reply|react|update|leak|alliance|dissent",
            "content": "the actual text of what they say/do (make it vivid and specific)",
            "target_id": "prophet_XX or empty if no target",
            "target_name": "target name or empty",
            "sentiment": -1.0 to 1.0,
            "confidence_delta": -0.2 to 0.2
        }},
        ... (aim for {len(prophets) + len(prophets) // 2} total actions)
    ],
    "opinion_distribution": {{"outcome description": count_of_prophets_supporting_it}},
    "consensus_score": 0.0-1.0,
    "polarization_score": 0.0-1.0,
    "key_moment": "one-sentence description of the most significant event this round",
    "round_summary": "2-3 sentence narrative summary of what happened this round"
}}

Make the simulation feel ALIVE. Agents should reference each other by name, argue
specific points, cite evidence from their backgrounds, and evolve their thinking.
Round {round_num}/{sim.num_rounds} — {"early exploration phase" if round_num <= 2 else "mid-simulation crystallization" if round_num <= sim.num_rounds - 2 else "late-stage convergence (or deepening division)"}."""

    raw = _llm_call(prompt, system=ROUND_SYSTEM, model=sim.model, temperature=0.95, max_tokens=5000)
    data = _extract_json(raw)

    # Parse actions
    actions = []
    for act_data in data.get("actions", []):
        try:
            action_type = ActionType(act_data.get("action_type", "post"))
        except ValueError:
            action_type = ActionType.POST
        actions.append(AgentAction(
            prophet_id=act_data.get("prophet_id", ""),
            prophet_name=act_data.get("prophet_name", ""),
            action_type=action_type,
            content=act_data.get("content", ""),
            target_id=act_data.get("target_id", ""),
            target_name=act_data.get("target_name", ""),
            sentiment=float(act_data.get("sentiment", 0)),
            confidence_delta=float(act_data.get("confidence_delta", 0)),
        ))

    # Parse events
    events = injected_events or []

    round_state = RoundState(
        round_number=round_num,
        actions=actions,
        events=events,
        opinion_distribution=data.get("opinion_distribution", {}),
        consensus_score=float(data.get("consensus_score", 0)),
        polarization_score=float(data.get("polarization_score", 0)),
        key_moment=data.get("key_moment", ""),
        round_summary=data.get("round_summary", ""),
    )

    # Update prophet states based on actions
    prophet_map = {p.id: p for p in sim.prophets}
    for act in actions:
        p = prophet_map.get(act.prophet_id)
        if not p:
            continue
        # Update confidence
        p.confidence = max(0.0, min(1.0, p.confidence + act.confidence_delta))
        # Add to memory
        if act.action_type == ActionType.UPDATE_POSITION:
            p.current_position = act.content
            p.memory.append(f"R{round_num}: Changed position to: {act.content[:100]}")
        elif act.action_type in (ActionType.POST, ActionType.REPLY):
            p.memory.append(f"R{round_num}: {act.content[:100]}")
        elif act.action_type == ActionType.ALLIANCE:
            if act.target_id:
                p.relationships[act.target_id] = "ally"
                p.memory.append(f"R{round_num}: Allied with {act.target_name}")
        elif act.action_type == ActionType.DISSENT:
            if act.target_id:
                p.relationships[act.target_id] = "rival"
                p.memory.append(f"R{round_num}: Broke from {act.target_name}")
        # Trim memory to last 10 entries
        if len(p.memory) > 10:
            p.memory = p.memory[-10:]

    # Update world state
    world.current_state = round_state.round_summary

    return round_state


# ── Independent Minds: Per-Prophet Parallel Deliberation ────────────────────

INDEPENDENT_SYSTEM = """You are a single prophet in a prediction simulation. You have a
distinct personality, expertise, and worldview. You must respond IN CHARACTER as this
prophet — not as an AI assistant.

You will see the current world state, what other prophets said recently, and any breaking
events. Respond with your genuine analysis, reactions, and decisions based on your
personality and biases.

Output STRICT JSON only."""


def _prophet_deliberate(
    prophet: Prophet,
    sim: ProphecySimulation,
    round_num: int,
    other_actions_summary: str,
    event_text: str,
) -> list[dict]:
    """Single prophet deliberates independently via its own LLM call."""
    world = sim.world
    prev_context = ""
    if sim.rounds:
        last = sim.rounds[-1]
        if last.round_summary:
            prev_context = f"\nLAST ROUND SUMMARY:\n{last.round_summary}\n"

    prompt = f"""SIMULATION ROUND {round_num} of {sim.num_rounds}

YOU ARE: {prophet.name}
ROLE: {prophet.role}
BACKGROUND: {prophet.background}
ARCHETYPE: {prophet.personality.archetype}
OPENNESS TO CHANGE: {prophet.personality.openness:.1f}/1.0
INFLUENCE: {prophet.personality.influence:.1f}/1.0
RISK TOLERANCE: {prophet.personality.risk_tolerance:.1f}/1.0
BIASES: {', '.join(prophet.personality.biases) or 'none'}
CURRENT POSITION: {prophet.current_position or prophet.initial_position}
CONFIDENCE: {prophet.confidence:.0%}
RECENT MEMORY: {json.dumps(prophet.memory[-5:] if prophet.memory else ['(first round)'])}
RELATIONSHIPS: {json.dumps(prophet.relationships) if prophet.relationships else '(none yet)'}

TOPIC: {world.topic}
WORLD STATE: {world.current_state or world.initial_conditions}
POSSIBLE OUTCOMES: {json.dumps(world.possible_outcomes)}
{prev_context}{event_text}
WHAT OTHERS ARE SAYING THIS ROUND:
{other_actions_summary or '(You are among the first to speak this round)'}

As {prophet.name}, take 1-3 actions. Choose from:
- "post": Publish an original analysis or opinion
- "reply": Respond directly to another prophet's statement
- "react": Brief agreement/disagreement with someone
- "update": Change your position (only if genuinely persuaded)
- "alliance": Formally align with another prophet
- "dissent": Break from emerging consensus or a group

Stay in character. Be specific. Reference other prophets by name. {"Push back hard — you resist groupthink." if prophet.personality.openness < 0.3 else "Consider others' arguments seriously." if prophet.personality.openness > 0.7 else ""}

Return JSON:
{{
    "actions": [
        {{
            "action_type": "post|reply|react|update|alliance|dissent",
            "content": "what you say/do (vivid, specific, in-character)",
            "target_name": "name of prophet you're addressing (or empty)",
            "sentiment": -1.0 to 1.0,
            "confidence_delta": -0.2 to 0.2
        }}
    ]
}}"""

    raw = _llm_call(prompt, system=INDEPENDENT_SYSTEM, model=sim.model, temperature=0.95, max_tokens=2000)
    data = _extract_json(raw)
    actions_raw = data.get("actions", []) if isinstance(data, dict) else []

    # Tag each action with this prophet's identity
    for act in actions_raw:
        act["prophet_id"] = prophet.id
        act["prophet_name"] = prophet.name

    return actions_raw


def _run_round_parallel(
    sim: ProphecySimulation,
    round_num: int,
    injected_events: list[WorldEvent] | None = None,
) -> RoundState:
    """Execute a round with each prophet deliberating independently in parallel."""
    prophets = [p for p in sim.prophets if p.active]

    # Build event text
    event_text = ""
    if injected_events:
        event_text = "\nBREAKING EVENTS THIS ROUND:\n"
        for ev in injected_events:
            event_text += f"  — {ev.title}: {ev.description}\n"

    # Build summary of previous round's key statements for context
    prev_statements = ""
    if sim.rounds:
        last = sim.rounds[-1]
        if last.actions:
            lines = []
            for act in last.actions[:8]:
                lines.append(f'  {act.prophet_name}: "{act.content[:150]}"')
            prev_statements = "\n".join(lines)

    # Fire all prophet calls in parallel
    all_actions_raw: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(len(prophets), 16)) as pool:
        futures = {
            pool.submit(
                _prophet_deliberate,
                p, sim, round_num, prev_statements, event_text,
            ): p
            for p in prophets
        }
        for future in as_completed(futures):
            prophet = futures[future]
            try:
                acts = future.result()
                all_actions_raw.extend(acts)
            except Exception as e:
                log.warning("Prophet %s failed to deliberate: %s", prophet.name, e)
                # Fallback: generate a simple post action
                all_actions_raw.append({
                    "prophet_id": prophet.id,
                    "prophet_name": prophet.name,
                    "action_type": "post",
                    "content": f"[{prophet.name} remains silent this round, contemplating.]",
                    "target_name": "",
                    "sentiment": 0.0,
                    "confidence_delta": 0.0,
                })

    # Parse into AgentAction objects
    actions = []
    for act_data in all_actions_raw:
        try:
            action_type = ActionType(act_data.get("action_type", "post"))
        except ValueError:
            action_type = ActionType.POST

        # Resolve target_id from target_name
        target_name = act_data.get("target_name", "")
        target_id = ""
        if target_name:
            for p in prophets:
                if p.name.lower() == target_name.lower():
                    target_id = p.id
                    break

        actions.append(AgentAction(
            prophet_id=act_data.get("prophet_id", ""),
            prophet_name=act_data.get("prophet_name", ""),
            action_type=action_type,
            content=act_data.get("content", ""),
            target_id=target_id,
            target_name=target_name,
            sentiment=float(act_data.get("sentiment", 0)),
            confidence_delta=float(act_data.get("confidence_delta", 0)),
        ))

    # Synthesize round-level stats via a quick LLM call
    synth_prompt = f"""Analyze these {len(actions)} actions from round {round_num} of a prediction simulation about "{sim.world.topic}".

ACTIONS:
{json.dumps([{"prophet": a.prophet_name, "type": a.action_type.value, "content": a.content[:200], "sentiment": a.sentiment} for a in actions], indent=1)}

Return JSON:
{{
    "opinion_distribution": {{"outcome description": count_of_prophets_supporting_it}},
    "consensus_score": 0.0-1.0,
    "polarization_score": 0.0-1.0,
    "key_moment": "one-sentence description of the most significant event",
    "round_summary": "2-3 sentence narrative summary"
}}"""

    synth_raw = _llm_call(synth_prompt, system="Analyze simulation data. Output STRICT JSON only.",
                          model=sim.model, temperature=0.3, max_tokens=1000)
    synth = _extract_json(synth_raw) if synth_raw else {}

    round_state = RoundState(
        round_number=round_num,
        actions=actions,
        events=injected_events or [],
        opinion_distribution=synth.get("opinion_distribution", {}),
        consensus_score=float(synth.get("consensus_score", 0)),
        polarization_score=float(synth.get("polarization_score", 0)),
        key_moment=synth.get("key_moment", ""),
        round_summary=synth.get("round_summary", ""),
    )

    # Update prophet states
    prophet_map = {p.id: p for p in sim.prophets}
    for act in actions:
        p = prophet_map.get(act.prophet_id)
        if not p:
            continue
        p.confidence = max(0.0, min(1.0, p.confidence + act.confidence_delta))
        if act.action_type == ActionType.UPDATE_POSITION:
            p.current_position = act.content
            p.memory.append(f"R{round_num}: Changed position to: {act.content[:100]}")
        elif act.action_type in (ActionType.POST, ActionType.REPLY):
            p.memory.append(f"R{round_num}: {act.content[:100]}")
        elif act.action_type == ActionType.ALLIANCE:
            if act.target_id:
                p.relationships[act.target_id] = "ally"
                p.memory.append(f"R{round_num}: Allied with {act.target_name}")
        elif act.action_type == ActionType.DISSENT:
            if act.target_id:
                p.relationships[act.target_id] = "rival"
                p.memory.append(f"R{round_num}: Broke from {act.target_name}")
        if len(p.memory) > 10:
            p.memory = p.memory[-10:]

    # Update world state
    sim.world.current_state = round_state.round_summary

    return round_state


def run_simulation(
    sim: ProphecySimulation,
    progress_cb: Callable[[str], None] | None = None,
    round_cb: Callable[[RoundState], None] | None = None,
) -> ProphecySimulation:
    """Phase 2: Run all simulation rounds."""
    def emit(msg: str):
        log.info(msg)
        if progress_cb:
            progress_cb(msg)

    sim.status = SimulationStatus.RUNNING
    start_round = len(sim.rounds) + 1  # support resuming

    emit(f"[CHURN] Starting simulation: {sim.num_rounds} rounds, {len(sim.prophets)} prophets")

    try:
        for round_num in range(start_round, sim.num_rounds + 1):
            emit(f"[CHURN] ═══ Round {round_num}/{sim.num_rounds} ═══")

            # Check for injected events this round
            round_events = [e for e in sim.injected_events if e.round_number == round_num]

            if sim.deliberation_mode == DeliberationMode.INDEPENDENT:
                emit(f"  [INDEPENDENT] {len([p for p in sim.prophets if p.active])} prophets deliberating in parallel...")
                round_state = _run_round_parallel(sim, round_num, round_events or None)
            else:
                round_state = _run_round(sim, round_num, round_events or None)
            sim.rounds.append(round_state)
            sim.consensus_trajectory.append(round_state.consensus_score)

            # Emit round summary
            emit(f"  Key moment: {round_state.key_moment}")
            emit(f"  Consensus: {round_state.consensus_score:.0%} | Polarization: {round_state.polarization_score:.0%}")
            emit(f"  Actions: {len(round_state.actions)} | Opinions: {round_state.opinion_distribution}")

            if round_cb:
                round_cb(round_state)

            # Save checkpoint
            sim.save(PROPHECY_DIR)

        sim.status = SimulationStatus.COMPLETED
        emit("[CHURN] Simulation complete")
        sim.save(PROPHECY_DIR)
        return sim

    except Exception as e:
        sim.status = SimulationStatus.FAILED
        sim.error = f"Simulation failed at round {len(sim.rounds) + 1}: {type(e).__name__}: {e}"
        log.exception("Simulation round failed")
        sim.save(PROPHECY_DIR)
        raise


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 3: DISTILL — Extract Prediction + Generate Report
# ═══════════════════════════════════════════════════════════════════════════════

REPORT_SYSTEM = """You are the Prophecy Engine's Oracle — the analyst who reads the
entrails of a completed prediction simulation and distills truth from chaos.

You have the complete transcript of a multi-agent prediction simulation where diverse
experts debated and evolved their views over multiple rounds. Your job is to synthesize
their collective intelligence into a rigorous prediction report.

Be specific, cite agents by name, reference specific turning points, and quantify
your confidence. This report should feel like it was written by a senior analyst at
a top consulting firm who happens to have access to a simulation engine."""


def generate_report(sim: ProphecySimulation, progress_cb: Callable[[str], None] | None = None) -> ProphecyReport:
    """Phase 3: Analyze simulation transcript and produce structured prediction report."""
    def emit(msg: str):
        log.info(msg)
        if progress_cb:
            progress_cb(msg)

    emit("[DISTILL] Analyzing simulation transcript...")

    world = sim.world
    # Build transcript summary
    transcript_parts = []
    for rs in sim.rounds:
        round_text = f"\n═══ ROUND {rs.round_number} ═══\n"
        round_text += f"Summary: {rs.round_summary}\n"
        round_text += f"Key Moment: {rs.key_moment}\n"
        round_text += f"Consensus: {rs.consensus_score:.0%} | Polarization: {rs.polarization_score:.0%}\n"
        round_text += f"Opinion Distribution: {rs.opinion_distribution}\n"
        for act in rs.actions:
            round_text += f"  [{act.action_type.value.upper()}] {act.prophet_name}: {act.content[:200]}\n"
        transcript_parts.append(round_text)

    transcript = "\n".join(transcript_parts)
    # Cap transcript to avoid token limits
    if len(transcript) > 15000:
        # Keep first 2 rounds, last 2 rounds in full; summarize middle
        kept = transcript_parts[:2] + ["\n... [middle rounds condensed] ...\n"] + transcript_parts[-2:]
        transcript = "\n".join(kept)

    # Final prophet states
    prophet_states = []
    for p in sim.prophets:
        prophet_states.append(f"  {p.name} ({p.personality.archetype}): "
                              f"Position=\"{p.current_position[:100]}\" "
                              f"Confidence={p.confidence:.0%}")

    prompt = f"""Analyze this completed prediction simulation and produce a comprehensive report.

TOPIC: {world.topic}
CONTEXT: {world.context}
POSSIBLE OUTCOMES: {json.dumps(world.possible_outcomes)}
SIMULATION: {sim.num_rounds} rounds, {len(sim.prophets)} prophets
CONSENSUS TRAJECTORY: {[f"{c:.0%}" for c in sim.consensus_trajectory]}

FULL TRANSCRIPT:
{transcript}

FINAL PROPHET POSITIONS:
{chr(10).join(prophet_states)}

Generate a JSON prediction report:
{{
    "executive_summary": "3-5 sentence summary of the simulation and its key finding",
    "prediction": "the specific prediction — what will happen, stated clearly and directly",
    "confidence": 0.0-1.0,
    "methodology": "2-3 sentences on how the simulation was structured",
    "key_findings": ["list of 4-6 specific findings from the simulation"],
    "opinion_evolution": "paragraph describing how opinions shifted over the simulation — name specific agents and turning points",
    "consensus_analysis": "paragraph analyzing the final consensus (or lack thereof) — what drove agreement or division",
    "dissenting_views": ["list of 2-4 notable dissenting positions that survived the simulation"],
    "risk_factors": ["list of 3-5 factors that could invalidate this prediction"],
    "timeline_narrative": "paragraph telling the story of the simulation as a narrative — the drama, the turning points, the resolution"
}}

Be specific. Name agents. Reference specific rounds and events. Quantify where possible.
Your prediction should reflect the EMERGENT consensus of the simulation, weighted by
agent expertise and the quality of their arguments — not just a majority vote."""

    emit("[DISTILL] Generating prediction report...")
    raw = _llm_call(prompt, system=REPORT_SYSTEM, model=sim.model, temperature=0.5, max_tokens=5000)
    data = _extract_json(raw)

    report = ProphecyReport(
        simulation_id=sim.id,
        topic=world.topic,
        executive_summary=data.get("executive_summary", ""),
        prediction=data.get("prediction", ""),
        confidence=float(data.get("confidence", 0)),
        methodology=data.get("methodology", ""),
        key_findings=data.get("key_findings", []),
        opinion_evolution=data.get("opinion_evolution", ""),
        consensus_analysis=data.get("consensus_analysis", ""),
        dissenting_views=data.get("dissenting_views", []),
        risk_factors=data.get("risk_factors", []),
        timeline_narrative=data.get("timeline_narrative", ""),
        raw_data={
            "consensus_trajectory": sim.consensus_trajectory,
            "final_opinions": {p.name: p.current_position for p in sim.prophets},
            "final_confidence": {p.name: p.confidence for p in sim.prophets},
        },
    )

    # Store report on simulation
    sim.prediction = report.prediction
    sim.prediction_confidence = report.confidence
    sim.final_report = _format_report_markdown(report)
    sim.save(PROPHECY_DIR)

    emit(f"[DISTILL] Prediction: {report.prediction[:200]}")
    emit(f"[DISTILL] Confidence: {report.confidence:.0%}")
    return report


def _format_report_markdown(report: ProphecyReport) -> str:
    """Format a ProphecyReport as readable Markdown."""
    lines = [
        f"# PROPHECY REPORT",
        f"**Topic:** {report.topic}",
        "",
        "---",
        "",
        "## Executive Summary",
        report.executive_summary,
        "",
        "## Prediction",
        f"> {report.prediction}",
        f">",
        f"> **Confidence: {report.confidence:.0%}**",
        "",
        "## Methodology",
        report.methodology,
        "",
        "## Key Findings",
    ]
    for i, finding in enumerate(report.key_findings, 1):
        lines.append(f"{i}. {finding}")
    lines += [
        "",
        "## Opinion Evolution",
        report.opinion_evolution,
        "",
        "## Consensus Analysis",
        report.consensus_analysis,
        "",
        "## Dissenting Views",
    ]
    for dv in report.dissenting_views:
        lines.append(f"- {dv}")
    lines += [
        "",
        "## Risk Factors",
    ]
    for rf in report.risk_factors:
        lines.append(f"- {rf}")
    lines += [
        "",
        "## Timeline Narrative",
        report.timeline_narrative,
        "",
        "---",
        f"*Generated by The Forge — Prophecy Engine*",
    ]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 4: INTERVIEW — Chat with Individual Prophets
# ═══════════════════════════════════════════════════════════════════════════════

def interview_prophet(
    sim: ProphecySimulation,
    prophet_id: str,
    question: str,
) -> str:
    """Interview a specific prophet about their perspective.

    The LLM roleplays as the prophet, drawing on their background, personality,
    simulation memories, and final position to answer the question in-character.
    """
    prophet = None
    for p in sim.prophets:
        if p.id == prophet_id or p.name.lower() == prophet_id.lower():
            prophet = p
            break
    if not prophet:
        return json.dumps({"error": f"Prophet not found: {prophet_id}"})

    system = f"""You are {prophet.name}, {prophet.role}. Stay completely in character.

Background: {prophet.background}
Personality: {prophet.personality.archetype} — openness {prophet.personality.openness:.0%},
influence {prophet.personality.influence:.0%}, risk tolerance {prophet.personality.risk_tolerance:.0%}
Biases: {', '.join(prophet.personality.biases) or 'none noted'}
Current position: {prophet.current_position}
Confidence: {prophet.confidence:.0%}

Your memories from the simulation:
{chr(10).join(prophet.memory) if prophet.memory else '(nothing notable)'}

Key relationships:
{chr(10).join(f'  {k}: {v}' for k, v in prophet.relationships.items()) if prophet.relationships else '(none formed)'}

Respond in-character. Reference your background, expertise, and what you witnessed
during the simulation. Be opinionated — you earned your position through debate."""

    prompt = f"""The simulation on "{sim.world.topic}" has concluded.
Your final position: {prophet.current_position}

Someone asks you: {question}

Respond as {prophet.name}. Be specific, draw on your expertise, and don't be shy
about your opinions. Reference other prophets you agreed or disagreed with."""

    return _llm_call(prompt, system=system, model=sim.model, temperature=0.9, max_tokens=1500)


# ═══════════════════════════════════════════════════════════════════════════════
# CONVENIENCE: Full Pipeline
# ═══════════════════════════════════════════════════════════════════════════════

def run_prophecy(
    topic: str,
    seed_material: str = "",
    num_prophets: int = 12,
    num_rounds: int = 8,
    model: str = "",
    deliberation_mode: str = "hivemind",
    events: list[dict] | None = None,
    progress_cb: Callable[[str], None] | None = None,
    round_cb: Callable[[RoundState], None] | None = None,
) -> ProphecySimulation:
    """Full pipeline: seed → simulate → report.

    Args:
        topic: The question or topic to predict.
        seed_material: Optional background text, data, or context.
        num_prophets: Number of agents (default 12).
        num_rounds: Simulation rounds (default 8).
        model: LLM model to use (empty = auto-detect best available).
        deliberation_mode: "hivemind" (1 call/round) or "independent" (1 call/prophet/round).
        events: Optional list of events to inject: [{"round": int, "title": str, "description": str}]
        progress_cb: Called with status messages.
        round_cb: Called after each round with RoundState.

    Returns:
        Completed ProphecySimulation with prediction and report.
    """
    sim = ProphecySimulation(
        seed_topic=topic,
        seed_material=seed_material,
        num_prophets=num_prophets,
        num_rounds=num_rounds,
        model=model,
        deliberation_mode=DeliberationMode(deliberation_mode),
    )

    # Parse injected events
    if events:
        for ev in events:
            sim.injected_events.append(WorldEvent(
                round_number=ev.get("round", 1),
                title=ev.get("title", "Event"),
                description=ev.get("description", ""),
                impact=ev.get("impact", ""),
                injected_by="user",
            ))

    # Phase 1: Seed
    seed_simulation(sim, progress_cb)

    # Phase 2: Simulate
    run_simulation(sim, progress_cb, round_cb)

    # Phase 3: Report
    generate_report(sim, progress_cb)

    return sim


# ═══════════════════════════════════════════════════════════════════════════════
# MANAGEMENT: List, Load, Resume
# ═══════════════════════════════════════════════════════════════════════════════

def list_simulations() -> list[dict]:
    """List all saved simulations."""
    sims = []
    for path in sorted(PROPHECY_DIR.glob("prophecy_*.json"), reverse=True):
        try:
            sim = ProphecySimulation.load(path)
            sims.append({
                "id": sim.id,
                "topic": sim.seed_topic[:100],
                "status": sim.status.value,
                "prophets": len(sim.prophets),
                "rounds_completed": len(sim.rounds),
                "rounds_total": sim.num_rounds,
                "prediction": sim.prediction[:200] if sim.prediction else "",
                "confidence": sim.prediction_confidence,
                "created_at": sim.created_at,
            })
        except Exception as e:
            log.warning("Failed to load simulation %s: %s", path.name, e)
    return sims


def load_simulation(sim_id: str) -> ProphecySimulation | None:
    """Load a simulation by ID."""
    path = PROPHECY_DIR / f"{sim_id}.json"
    if not path.exists():
        # Try partial match
        for p in PROPHECY_DIR.glob(f"*{sim_id}*.json"):
            path = p
            break
        else:
            return None
    return ProphecySimulation.load(path)
