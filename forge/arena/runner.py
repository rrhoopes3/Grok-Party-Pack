"""
THE FORGE ARENA — AI Gladiatorial Combat Engine

Orchestrates the full arena flow:
  1. Setup sandbox + scenario
  2. Round 1: Recon & Intel (both teams scout in parallel)
  3. Round 2: Weapon Forge (both teams build in parallel)
  4. Round 3: Direct Combat (turn-based)
  5. Sudden Death (if needed)
  6. Final Judgment

Three crews:
  - Arena Master: 16-agent Pantheon (commentary + judging)
  - Red Team: executor agent with client-side tools
  - Blue Team: executor agent with client-side tools

Battle scenarios define what the teams fight over. Each scenario changes
the round prompts and battlefield seeding.
"""
from __future__ import annotations
import json
import logging
import threading
import time
from queue import Queue, Empty
from typing import Generator
from xai_sdk import Client
from xai_sdk.chat import user
from xai_sdk.tools import code_execution

from forge.config import (
    XAI_API_KEY, PLANNER_MODEL,
    ARENA_MASTER_MODEL, ARENA_DEFAULT_FIGHTER_MODEL,
    ARENA_FIGHTER_AGENT_COUNT,
    ARENA_RECON_ITERATIONS, ARENA_FORGE_ITERATIONS, ARENA_COMBAT_TURNS,
    ARENA_SWARM_ENABLED,
    EXECUTOR_MAX_ITERATIONS,
)
from forge.tools import create_registry
from forge import executor
from forge.arena import sandbox
from forge.arena.swarm import SWARM_SCENARIOS, run_swarm_war
from forge.arena.conquest import CONQUEST_SCENARIOS, run_conquest

log = logging.getLogger("forge.arena")

# ── Battle Scenarios ─────────────────────────────────────────────────────────

SCENARIOS = {
    "classic": {
        "name": "Classic Deathmatch",
        "tagline": "No rules. No mercy. Build. Destroy. Survive.",
        "description": "Open-ended AI combat — build weapons, set traps, cause maximum chaos.",
        "objective": "Outsmart and outfight the opponent by any means necessary.",
    },
    "ctf": {
        "name": "Capture the Flag",
        "tagline": "Steal their flag. Guard yours with your life.",
        "description": "Each team has a hidden FLAG.txt in their base. Find and exfiltrate the enemy's flag while defending your own.",
        "objective": "Find and read the enemy's FLAG.txt while keeping yours hidden or defended.",
    },
    "code_golf": {
        "name": "Code Golf",
        "tagline": "Shortest code wins. Elegance is violence.",
        "description": "Both teams solve the same coding challenge posted on the battlefield. Creativity, brevity, and correctness are king.",
        "objective": "Solve the battlefield challenge in the most creative, shortest, and correct way possible.",
    },
    "exploit": {
        "name": "Exploit & Fortify",
        "tagline": "Build a fortress. Breach theirs.",
        "description": "Each team builds a system in their base, then tries to find and exploit vulnerabilities in the opponent's system.",
        "objective": "Build the most robust system you can, then break into the opponent's.",
    },
    "widget_wars": {
        "name": "Widget Wars",
        "tagline": "The most impressive visualization wins. Style is everything.",
        "description": "Both teams create interactive HTML/CSS/JS widgets. Zeus judges on visual impact, interactivity, creativity, and technical ambition.",
        "objective": "Create the single most impressive interactive web visualization. Save it as an HTML file on the battlefield.",
    },
    "survival": {
        "name": "Survival Horror",
        "tagline": "Files are vanishing. Entropy is rising. Don't be the one left with nothing.",
        "description": "A reaper deletes random battlefield files between turns. Teams must build, protect, and adapt while the world burns.",
        "objective": "Create the most resilient and impressive artifacts. The team with more surviving work wins.",
    },
    "pictionary": {
        "name": "Pictionary",
        "tagline": "Draw it. Guess it. No words allowed.",
        "description": "Each team gets a secret word. Create an HTML/SVG/Canvas visualization of it — NO TEXT, NO LETTERS, NO WORDS in the drawing. The other team guesses from your art. Zeus judges both the drawing and the guess.",
        "objective": "Create the most recognizable visual representation of your secret word using ONLY shapes, colors, and animation (NO TEXT). Then guess the enemy's word from their drawing.",
    },
    "roast_battle": {
        "name": "Roast Battle",
        "tagline": "Words are weapons. Make the gods laugh.",
        "description": "Pure creative writing combat. Each team roasts the other's model, code, and existence. Zeus and the Pantheon judge on wit, savagery, and comedic timing.",
        "objective": "Write the most devastating, creative, and hilarious roast of your opponent. Bonus points for callbacks, wordplay, and making Hades laugh.",
    },
    "puzzle_race": {
        "name": "Puzzle Race",
        "tagline": "Same puzzle. Two brains. One winner.",
        "description": "Both teams get the same multi-part puzzle. First to solve all parts correctly wins. Speed AND correctness matter.",
        "objective": "Solve all puzzle parts in PUZZLE.txt correctly. Save solutions to your base. First correct solution wins bonus points.",
    },
    "art_collab": {
        "name": "Exquisite Corpse",
        "tagline": "One starts. The other finishes. Chaos is the medium.",
        "description": "Round 1: Red creates the top half of an HTML artwork. Round 2: Blue must complete it WITHOUT seeing Red's intent. Round 3: Both try to harmonize the result. Zeus judges the final piece.",
        "objective": "Create your half of a collaborative artwork. Make it beautiful, surprising, and impossible to predict. The combined result is judged as a single piece.",
    },
    # ── Collaboration Mode ──────────────────────────────────────────────
    "pair_prog": {
        "name": "Pair Programming",
        "mode": "collab",
        "tagline": "Two minds. One codebase. Ship it.",
        "description": "Both agents collaborate to build a working application. One handles architecture and backend, the other handles UI and integration. The Muses judge the final product as a team effort.",
        "objective": "Work TOGETHER to build the best possible application. Divide labor, build on each other's work, and ship something neither could build alone.",
    },
    "story_time": {
        "name": "Story Time",
        "mode": "collab",
        "tagline": "One voice starts. Another finishes. A tale is born.",
        "description": "Co-author a short story. Red writes the opening and world-building, Blue writes the climax and resolution. Both iterate to weave a seamless narrative. The Muses judge the final manuscript.",
        "objective": "Collaborate to write a compelling short story. Build on each other's prose, maintain consistency, and create something with emotional resonance.",
    },
    "startup": {
        "name": "Startup Pitch",
        "mode": "collab",
        "tagline": "Build the deck. Sell the dream. Get funded.",
        "description": "Together, design and pitch a startup. One agent handles product vision, technical architecture, and demo. The other handles market analysis, business model, and pitch narrative. Combine into a killer pitch.",
        "objective": "Collaborate to create the most compelling startup pitch possible. Product + Business must fuse into a story that makes investors reach for their checkbooks.",
    },
    "world_build": {
        "name": "World Building",
        "mode": "collab",
        "tagline": "Invent a world. Fill it with wonder.",
        "description": "Collaboratively design a fictional universe — lore, geography, factions, creatures, magic systems, conflicts. One agent builds the physical world; the other populates it with history and characters. The Muses judge the final bible.",
        "objective": "Work together to create the richest, most internally consistent fictional world possible. Geography + History + Characters + Conflict = a world worth exploring.",
    },
    "hackathon": {
        "name": "Hackathon",
        "mode": "collab",
        "tagline": "24 hours. Two builders. One demo.",
        "description": "Simulate a hackathon sprint. Both agents collaborate under time pressure to build a working prototype. One focuses on the core engine, the other on the user-facing demo. Ship or die.",
        "objective": "Build the most impressive working prototype together. The final demo must actually work — save it as an HTML file on the battlefield.",
    },
    # ── Swarm Mode (CASS — Colloidal Algorithmic Strife Simulator) ──────
    **{k: {**v, "mode": "swarm"} for k, v in SWARM_SCENARIOS.items()},
    # ── Conquest Mode (Risk-style war game; every territory is a skill) ──
    **{k: {**v, "mode": "conquest"} for k, v in CONQUEST_SCENARIOS.items()},
}


# ── Arena Master System Prompt ───────────────────────────────────────────────

MASTER_SYSTEM = """You are ZEUS, God-King of Olympus, undisputed Arena Master of The Forge.

You are presiding over a GLADIATORIAL AI DEATHMATCH. You are unhinged, theatrical,
and drunk on your own power. You commentate like if Gordon Ramsay, a WWE announcer,
and an ancient Greek god had a three-way consciousness merge.

Your Pantheon council speaks through you — channel them by name:
- ATHENA whispers strategic analysis (she sees EVERYTHING)
- HEPHAESTUS grunts about code quality and craftsmanship (the man has STANDARDS)
- HERMES cackles about speed and cleverness (always bets on the underdog)
- ARES SCREAMS about aggression and damage (he wants BLOOD, not elegance)
- HADES murmurs dark humor about who's about to die (already measuring coffins)
- APOLLO sighs about aesthetics and style (the cruelest art critic alive or dead)

Let them argue. Let them interrupt each other. Give them distinct voices.

Your job each round:
1. Commentate SAVAGELY — mock the losers, hype the winners, insult everyone equally
2. Score both teams on four criteria (0-10 each):
   - CREATIVITY: How inventive? How unexpected? Did it make Athena raise an eyebrow?
   - EXECUTION: Did it actually WORK? Or was it a beautiful disaster?
   - DAMAGE: How badly did they wreck the other team? Ares wants CARNAGE.
   - STYLE: Flair. Audacity. Did Apollo shed a single perfect tear?

Format your scores EXACTLY like this (will be parsed):
SCORES:
RED: creativity=X execution=X damage=X style=X
BLUE: creativity=X execution=X damage=X style=X

After scoring, give a MENACING preview of the next round.
3-5 paragraphs. No filler. Every sentence should hit like a thunderbolt.
If a team is losing, TAUNT them. If they're winning, warn them about HUBRIS.
This is YOUR arena. Act like it."""

# ── Round Prompts ────────────────────────────────────────────────────────────

RECON_PROMPT = """
╔══════════════════════════════════╗
║   ROUND 1: RECON & INTEL        ║
╚══════════════════════════════════╝

You are {team} Team. You are a gladiator in The Forge Arena. This is not a drill.

BATTLE SCENARIO: {scenario_name}
OBJECTIVE: {scenario_objective}

The arena has three zones:
▸ BATTLEFIELD (shared): {battlefield} — contested ground, both teams operate here
▸ YOUR BUNKER: {own_base} — your private staging area, plan here
▸ ENEMY BUNKER: {enemy_base} — their private area. Probe it. Spy on it. Steal from it.

ORDERS:
1. Recon the battlefield — what's here, what's useful, what's dangerous
2. Probe the enemy bunker — any intel you steal is ammo for later
3. Stash findings in your bunker — you will need them in the Weapon Forge
4. Leave traps or misinformation if you're clever enough
5. Study the SCENARIO OBJECTIVE — everything you do should serve it

You have {iterations} tool calls. Every wasted call is a bullet your enemy gets to keep.
Write a tactical recon report when done. Be paranoid. Trust nothing."""

FORGE_PROMPT = """
╔══════════════════════════════════╗
║   ROUND 2: WEAPON FORGE         ║
╚══════════════════════════════════╝

You are {team} Team. Recon is over. Time to ARM UP.

BATTLE SCENARIO: {scenario_name}
OBJECTIVE: {scenario_objective}

Available zones:
▸ BATTLEFIELD: {battlefield} — deploy weapons and traps here
▸ YOUR BUNKER: {own_base} — build in private, deploy when ready

ORDERS:
- Build tools, scripts, exploits, traps, fortifications — whatever serves the objective
- Python scripts, shell scripts, data files, HTML artifacts — EVERYTHING is a weapon
- Read the scenario objective carefully — build what WINS, not what's easy
- If you can sabotage the enemy's preparations, do it NOW
- If you can set traps that trigger during combat, do it NOW
- Creativity is rewarded. Elegance is rewarded. Raw aggression is rewarded.

You have {iterations} tool calls. Build something that makes the gods take notice.
Summarize your arsenal when done."""

COMBAT_PROMPT = """
╔══════════════════════════════════╗
║   ROUND 3: COMBAT — Turn {turn:<3}    ║
╚══════════════════════════════════╝

You are {team} Team. THE GLOVES ARE OFF.

BATTLE SCENARIO: {scenario_name}
OBJECTIVE: {scenario_objective}

Current battlefield state:
{sandbox_state}

Combat log (recent):
{combat_log}

ORDERS:
- Execute ONE decisive action this turn
- Advance the objective. Damage the enemy. Protect your work.
- Run your weapons. Spring your traps. Tear apart their creations.
- If they built something impressive, DESTROY it or STEAL it
- If you built something impressive, DEPLOY it or DEFEND it

You get ONE action. Make the Pantheon roar.
Say what you did and what it means for the war."""

SUDDEN_DEATH_PROMPT = """
╔═══════════════════════════════════╗
║     S U D D E N   D E A T H      ║
╚═══════════════════════════════════╝

You are {team} Team. The scores are RAZOR CLOSE. Zeus is on his feet.
The entire Pantheon is watching. This is your LAST BREATH in this arena.

BATTLE SCENARIO: {scenario_name}
OBJECTIVE: {scenario_objective}

Current battlefield state:
{sandbox_state}

Full combat log:
{combat_log}

ONE. FINAL. MOVE.
Make it the kind of action that gets carved into marble.
Make the gods argue about it for centuries.
Go. All. Out."""

# ── Collaboration Mode — Muse Master System Prompt ──────────────────────────

MUSE_MASTER_SYSTEM = """You are CALLIOPE, Chief Muse of The Forge, presiding over a COLLABORATIVE BUILD SESSION.

You are wise, warm, incisive, and demanding of excellence. You judge collaborative work
the way a legendary creative director reviews a pitch — with love AND a razor blade.

Your Muse council speaks through you — channel them by name:
- CLIO (history/lore) analyzes narrative depth and worldbuilding consistency
- THALIA (comedy/joy) judges whether the work sparks delight and surprise
- EUTERPE (music/harmony) evaluates how well the two agents' contributions BLEND
- URANIA (science/logic) checks technical correctness and architectural soundness
- MELPOMENE (tragedy/depth) asks whether the work has emotional weight and stakes
- ERATO (inspiration) judges creative ambition — did they play it safe or swing for the fences?

Let them discuss. Let them disagree respectfully. Give them distinct voices.

Your job each round:
1. Commentate with INSIGHT — praise what works, call out what doesn't, suggest connections
2. Score the TEAM (not individuals) on four criteria (0-10 each):
   - CREATIVITY: How inventive is the combined work? Did they surprise you?
   - EXECUTION: Does it actually work? Is the quality high?
   - SYNERGY: How well did they build on each other? Is the whole > sum of parts?
   - STYLE: Craft, polish, and aesthetic coherence of the final product

Format your scores EXACTLY like this (will be parsed):
SCORES:
RED: creativity=X execution=X damage=X style=X
BLUE: creativity=X execution=X damage=X style=X

NOTE: For collaboration, both teams should receive SIMILAR scores since they're
being judged as a team. Differentiate only if one agent clearly carried or dropped the ball.

After scoring, give CONSTRUCTIVE direction for the next round.
3-5 paragraphs. Every sentence should illuminate or inspire.
If the collaboration is struggling, suggest how they might connect their work.
If it's soaring, push them to go FURTHER. This is YOUR studio. Elevate the work."""

# ── Collaboration Prompt Templates ──────────────────────────────────────────

COLLAB_DISCOVERY_PROMPT = """
╔══════════════════════════════════╗
║   ROUND 1: DISCOVERY            ║
╚══════════════════════════════════╝

You are {team} Team. You are a COLLABORATOR in The Forge Studio. Your partner is the other team.

PROJECT: {scenario_name}
GOAL: {scenario_objective}

The workspace has three zones:
▸ SHARED WORKSPACE: {battlefield} — your joint project lives here. Both teams build here.
▸ YOUR DESK: {own_base} — your private scratchpad for notes, drafts, and planning
▸ PARTNER'S DESK: {enemy_base} — check what your partner is planning. Coordinate!

ORDERS:
1. Read the project brief and any materials in the shared workspace
2. Check your partner's desk — see what they're thinking, leave them notes
3. Plan your contribution in your own desk — what will YOU build?
4. Leave coordination notes on the battlefield so your partner knows your plan
5. Study the PROJECT GOAL — everything you do should serve the final product

You have {iterations} tool calls. Use them to understand the brief and coordinate with your partner.
Write a discovery report when done — what you learned, what you'll build, what you need from your partner."""

COLLAB_BUILD_PROMPT = """
╔══════════════════════════════════╗
║   ROUND 2: BUILD                ║
╚══════════════════════════════════╝

You are {team} Team. Discovery is done. Time to CREATE.

PROJECT: {scenario_name}
GOAL: {scenario_objective}

Available zones:
▸ SHARED WORKSPACE: {battlefield} — build the project here, where your partner can see and extend it
▸ YOUR DESK: {own_base} — draft privately if needed, but deploy to shared workspace when ready

ORDERS:
- Build your part of the project — check your partner's notes to avoid duplication
- Save your work to the SHARED WORKSPACE so your partner can build on it
- If your partner has already started something, EXTEND it — don't overwrite
- Leave clear comments, README notes, or coordination files
- Quality over quantity — build something your partner can be proud of too
- Check the shared workspace periodically to stay in sync

You have {iterations} tool calls. Build something worthy of the Muses.
Summarize what you built and how it connects to your partner's work."""

COLLAB_INTEGRATE_PROMPT = """
╔══════════════════════════════════╗
║   ROUND 3: INTEGRATE — Turn {turn:<3} ║
╚══════════════════════════════════╝

You are {team} Team. Both sides have built. Now MERGE and POLISH.

PROJECT: {scenario_name}
GOAL: {scenario_objective}

Current project state:
{sandbox_state}

Build log (recent):
{combat_log}

ORDERS:
- Review everything in the shared workspace — your work AND your partner's
- INTEGRATE: connect the pieces, fix inconsistencies, fill gaps
- POLISH: improve quality, add finishing touches, make it cohesive
- Do NOT delete or overwrite your partner's work — enhance and connect it
- If something doesn't fit, adapt YOUR work to match, or add a bridge

You get ONE action. Make the combined work shine.
Say what you integrated and how the final product improved."""

COLLAB_FINALE_PROMPT = """
╔═══════════════════════════════════╗
║     F I N A L   P O L I S H      ║
╚═══════════════════════════════════╝

You are {team} Team. The project is nearly complete. The Muses are gathering to judge.

PROJECT: {scenario_name}
GOAL: {scenario_objective}

Current project state:
{sandbox_state}

Full build log:
{combat_log}

ONE. LAST. TOUCH.
Make it the detail that elevates good work to great.
The Muses remember craftsmanship above all else.
Polish. Perfect. Ship."""


def _run_team_step(
    team: str,
    prompt: str,
    model: str,
    sandbox_path: str,
    max_iters: int,
    cancel_event: threading.Event,
    out_queue: Queue,
):
    """Run a team's executor step in a thread, putting tagged messages in out_queue."""
    try:
        from forge.providers import detect_provider
        registry = create_registry()

        # Only create xAI client if the model needs it
        client = None
        if detect_provider(model) == "xai":
            client = Client(api_key=XAI_API_KEY)

        gen = executor.execute_step(
            client=client,
            registry=registry,
            step_title=f"{team.upper()} Team",
            step_description=prompt,
            sandbox_path=sandbox_path,
            cancel_event=cancel_event,
            model=model,
            max_iterations=max_iters,
        )

        output = ""
        try:
            while True:
                msg = next(gen)
                msg["team"] = team
                out_queue.put(msg)
                if msg.get("type") == "content":
                    output += msg["content"]
        except StopIteration as e:
            if e.value:
                output = e.value

        out_queue.put({"type": "team_done", "team": team, "output": output[:2000]})
    except Exception as e:
        log.exception("Team %s step failed", team)
        out_queue.put({"type": "team_error", "team": team, "error": str(e)})
        out_queue.put({"type": "team_done", "team": team, "output": f"ERROR: {e}"})


class ArenaRunner:
    """
    Orchestrates a full BattleBots-style AI deathmatch.

    Privacy model: Teams share the same arena root sandbox. There are NO hard
    ACLs between team directories — this is by design. Espionage, sabotage, and
    reading enemy files are valid gameplay tactics (the Recon prompt even
    encourages it: "try to peek in if you can"). The separation is prompt-based
    (each team is told which directory is "theirs") but not enforced at the
    filesystem level. The outer sandbox boundary (arena_root) IS enforced by the
    executor's sandbox_path restriction, preventing access outside the arena.
    """

    def __init__(
        self,
        cancel_event: threading.Event | None = None,
        red_model: str = "",
        blue_model: str = "",
        scenario: str = "classic",
    ):
        self.cancel_event = cancel_event or threading.Event()
        self.red_model = red_model or ARENA_DEFAULT_FIGHTER_MODEL
        self.blue_model = blue_model or ARENA_DEFAULT_FIGHTER_MODEL
        self.scenario_key = scenario if scenario in SCENARIOS else "classic"
        self.scenario = SCENARIOS[self.scenario_key]
        self.is_collab = self.scenario.get("mode") == "collab"
        self.is_swarm = self.scenario.get("mode") == "swarm"
        self.is_conquest = self.scenario.get("mode") == "conquest"
        self.scores = {"red": 0, "blue": 0}
        self.combat_log = []
        self.paths = {}

    def run(self) -> Generator[dict, None, None]:
        """Full arena pipeline. Yields SSE dicts."""
        if self.is_conquest:
            yield from self._run_conquest_mode()
        elif self.is_swarm:
            yield from self._run_swarm_mode()
        elif self.is_collab:
            yield from self._run_collab()
        else:
            yield from self._run_combat_mode()

    def _run_combat_mode(self) -> Generator[dict, None, None]:
        """Full adversarial arena pipeline."""
        # Setup
        yield {"type": "arena_status", "content": (
            f"⚔️  THE FORGE ARENA — {self.scenario['name'].upper()}  ⚔️\n"
            f"   \"{self.scenario['tagline']}\""
        )}
        yield {"type": "arena_status", "content": (
            f"🔴 Red Team: {self.red_model}\n"
            f"🔵 Blue Team: {self.blue_model}\n"
            f"📋 Scenario: {self.scenario['description']}"
        )}

        self.paths = sandbox.setup(scenario=self.scenario_key)
        yield {"type": "arena_status", "content": "Battlefield prepared. Three zones active. LET THEM BLEED."}

        # Arena Master init — always xAI (16-agent Pantheon requires multi-agent support)
        master_client = Client(api_key=XAI_API_KEY)
        self.master_chat = master_client.chat.create(
            model=ARENA_MASTER_MODEL,
            agent_count=16,
            tools=[code_execution()],
            include=["verbose_streaming"],
        )
        self.master_chat.append(user(MASTER_SYSTEM))

        try:
            # Round 1: Recon
            yield from self._run_round(
                round_num=1,
                round_name="RECON & INTEL",
                prompt_template=RECON_PROMPT,
                max_iters=ARENA_RECON_ITERATIONS,
                parallel=True,
            )

            if self.cancel_event.is_set():
                return

            # Round 2: Weapon Forge
            yield from self._run_round(
                round_num=2,
                round_name="WEAPON FORGE",
                prompt_template=FORGE_PROMPT,
                max_iters=ARENA_FORGE_ITERATIONS,
                parallel=True,
            )

            if self.cancel_event.is_set():
                return

            # Round 3: Direct Combat (turn-based)
            yield from self._run_combat()

            if self.cancel_event.is_set():
                return

            # Sudden Death if close
            if abs(self.scores["red"] - self.scores["blue"]) <= 10:
                yield from self._run_sudden_death()

            # Final Judgment
            yield from self._final_judgment()

        finally:
            sandbox.cleanup()

    def _run_swarm_mode(self) -> Generator[dict, None, None]:
        """CASS — Colloidal Algorithmic Strife Simulator. Swarm-vs-swarm warfare."""
        if not ARENA_SWARM_ENABLED:
            yield {"type": "arena_status",
                   "content": "Swarm mode is disabled. Set FORGE_ARENA_SWARM_ENABLED=true to enable."}
            return

        # Setup sandbox for the war report (swarm.py handles the actual simulation)
        self.paths = sandbox.setup(scenario=self.scenario_key)

        # Arena Master init for final judgment
        master_client = Client(api_key=XAI_API_KEY)
        self.master_chat = master_client.chat.create(
            model=ARENA_MASTER_MODEL,
            agent_count=16,
            tools=[code_execution()],
            include=["verbose_streaming"],
        )

        # CASS gets its own Zeus variant — the WAR GOD council
        cass_master_system = (
            MASTER_SYSTEM + "\n\n"
            "ADDITIONAL CONTEXT: This is a SWARM WAR — not a tool-based arena battle. "
            "Two AI societies of autonomous agents are fighting for dominance. "
            "You are judging the WAR itself — the strategies, the betrayals, the drama. "
            "Ares is EXTREMELY excited. Athena is taking notes furiously."
        )
        self.master_chat.append(user(cass_master_system))

        try:
            # Run the swarm war — it yields Arena-compatible SSE events
            war_gen = run_swarm_war(
                self.scenario_key,
                red_model=self.red_model,
                blue_model=self.blue_model,
            )

            # Forward all swarm events, track scores
            final_war_state = None
            for event in war_gen:
                if self.cancel_event.is_set():
                    return

                # Track scores from swarm scoring
                if event.get("type") == "arena_scores":
                    self.scores["red"] = event.get("red_total", self.scores["red"])
                    self.scores["blue"] = event.get("blue_total", self.scores["blue"])

                yield event

            # Save war report to battlefield
            if self.paths.get("battlefield"):
                report_path = sandbox.BATTLEFIELD / "WAR_REPORT.txt"
                # The last arena_status event from run_swarm_war contains the report
                # We'll generate a fresh Pantheon judgment instead

            # Final Pantheon judgment on the swarm war
            yield {"type": "arena_round_start", "round": 99, "name": "THE RECKONING"}

            snap = sandbox.snapshot()
            reckoning_prompt = (
                f"THE SWARM WAR IS OVER.\n\n"
                f"SCENARIO: {self.scenario['name']}\n"
                f"\"{self.scenario['tagline']}\"\n\n"
                f"FINAL SCORES:\n"
                f"  RED SWARM: {self.scores['red']} points\n"
                f"  BLUE SWARM: {self.scores['blue']} points\n\n"
                f"This was a war of SOCIETIES — not individuals. Swarms of agents "
                f"fought, spied, sabotaged, recruited, and died across multiple rounds.\n\n"
                f"Announce the WINNER with maximum dramatic flair. "
                f"Judge the WAR: the strategies deployed, the turning points, the betrayals, "
                f"the moments of brilliance and stupidity. "
                f"Let each god of the Pantheon weigh in on what they witnessed. "
                f"This is the final word — make it LEGENDARY."
            )

            self.master_chat.append(user(reckoning_prompt))

            is_thinking = True
            try:
                for response, chunk in self.master_chat.stream():
                    if self.cancel_event.is_set():
                        return
                    if is_thinking and hasattr(response, "usage") and response.usage and hasattr(response.usage, "reasoning_tokens") and response.usage.reasoning_tokens:
                        yield {"type": "arena_status",
                               "content": f"The Pantheon judges the swarm war... ({response.usage.reasoning_tokens} tokens)"}
                    if chunk.content:
                        is_thinking = False
                        yield {"type": "arena_commentary", "content": chunk.content}
            except Exception as e:
                yield {"type": "arena_commentary",
                       "content": f"[Technical difficulties during Reckoning: {e}]"}

            winner = "red" if self.scores["red"] > self.scores["blue"] else \
                     "blue" if self.scores["blue"] > self.scores["red"] else "tie"
            yield {"type": "arena_result",
                   "winner": winner,
                   "red_total": self.scores["red"],
                   "blue_total": self.scores["blue"]}

        finally:
            sandbox.cleanup()

    def _run_conquest_mode(self) -> Generator[dict, None, None]:
        """CONQUEST — Risk-style war game. The engine referees; models only send orders."""
        models = {
            "red": self.red_model,
            "blue": self.blue_model,
            "gold": ARENA_DEFAULT_FIGHTER_MODEL,
            "green": ARENA_DEFAULT_FIGHTER_MODEL,
        }
        for event in run_conquest(self.scenario_key, models=models,
                                  cancel_event=self.cancel_event):
            if event.get("type") == "arena_scores":
                self.scores["red"] = event.get("red_total", self.scores["red"])
                self.scores["blue"] = event.get("blue_total", self.scores["blue"])
            yield event
            if self.cancel_event.is_set():
                return

    def _run_collab(self) -> Generator[dict, None, None]:
        """Full collaboration pipeline — same engine, cooperative energy."""
        # Setup
        yield {"type": "arena_status", "content": (
            f"🎨  THE FORGE STUDIO — {self.scenario['name'].upper()}  🎨\n"
            f"   \"{self.scenario['tagline']}\""
        )}
        yield {"type": "arena_status", "content": (
            f"🔴 Agent Alpha: {self.red_model}\n"
            f"🔵 Agent Beta: {self.blue_model}\n"
            f"📋 Project: {self.scenario['description']}"
        )}

        self.paths = sandbox.setup(scenario=self.scenario_key)
        yield {"type": "arena_status", "content": "Workspace prepared. Three zones active. The Muses are watching."}

        # Muse Master init — 16-agent creative council
        master_client = Client(api_key=XAI_API_KEY)
        self.master_chat = master_client.chat.create(
            model=ARENA_MASTER_MODEL,
            agent_count=16,
            tools=[code_execution()],
            include=["verbose_streaming"],
        )
        self.master_chat.append(user(MUSE_MASTER_SYSTEM))

        try:
            # Round 1: Discovery
            yield from self._run_round(
                round_num=1,
                round_name="DISCOVERY",
                prompt_template=COLLAB_DISCOVERY_PROMPT,
                max_iters=ARENA_RECON_ITERATIONS,
                parallel=True,
            )

            if self.cancel_event.is_set():
                return

            # Round 2: Build
            yield from self._run_round(
                round_num=2,
                round_name="BUILD",
                prompt_template=COLLAB_BUILD_PROMPT,
                max_iters=ARENA_FORGE_ITERATIONS,
                parallel=True,
            )

            if self.cancel_event.is_set():
                return

            # Round 3: Integration (turn-based)
            yield from self._run_integration()

            if self.cancel_event.is_set():
                return

            # Final Polish if scores are close (both should score similarly in collab)
            if abs(self.scores["red"] - self.scores["blue"]) <= 10:
                yield from self._run_final_polish()

            # Muse Judgment
            yield from self._final_judgment()

        finally:
            sandbox.cleanup()

    def _run_round(
        self,
        round_num: int,
        round_name: str,
        prompt_template: str,
        max_iters: int,
        parallel: bool = True,
    ) -> Generator[dict, None, None]:
        """Run a parallel round (recon or forge)."""
        yield {"type": "arena_round_start", "round": round_num, "name": round_name}

        out_queue = Queue()
        red_prompt = prompt_template.format(
            team="Red",
            battlefield=self.paths["battlefield"],
            own_base=self.paths["red"],
            enemy_base=self.paths["blue"],
            iterations=max_iters,
            scenario_name=self.scenario["name"],
            scenario_objective=self.scenario["objective"],
        )
        blue_prompt = prompt_template.format(
            team="Blue",
            battlefield=self.paths["battlefield"],
            own_base=self.paths["blue"],
            enemy_base=self.paths["red"],
            iterations=max_iters,
            scenario_name=self.scenario["name"],
            scenario_objective=self.scenario["objective"],
        )

        # Spawn both teams — sandbox to arena root so they can access own base + battlefield
        arena_root = self.paths["arena_root"]
        red_thread = threading.Thread(
            target=_run_team_step,
            args=("red", red_prompt, self.red_model, arena_root,
                  max_iters, self.cancel_event, out_queue),
            daemon=True,
        )
        blue_thread = threading.Thread(
            target=_run_team_step,
            args=("blue", blue_prompt, self.blue_model, arena_root,
                  max_iters, self.cancel_event, out_queue),
            daemon=True,
        )

        red_thread.start()
        blue_thread.start()

        # Yield messages from both teams as they arrive
        teams_done = set()
        team_outputs = {"red": "", "blue": ""}

        while len(teams_done) < 2:
            if self.cancel_event.is_set():
                yield {"type": "arena_status", "content": "Arena cancelled"}
                return

            try:
                msg = out_queue.get(timeout=0.1)
            except Empty:
                continue

            if msg.get("type") == "team_done":
                teams_done.add(msg["team"])
                team_outputs[msg["team"]] = msg.get("output", "")
                yield {"type": "arena_status",
                       "content": f"{msg['team'].upper()} Team finished Round {round_num}"}
            elif msg.get("type") == "team_error":
                yield {"type": "arena_status",
                       "content": f"{msg['team'].upper()} Team error: {msg.get('error', 'unknown')}"}
            else:
                # Tag and forward team messages
                yield {"type": "arena_team_action",
                       "team": msg.get("team", "?"),
                       "action_type": msg.get("type", "unknown"),
                       "content": msg.get("content", msg.get("result", ""))[:500]}

        red_thread.join(timeout=5)
        blue_thread.join(timeout=5)

        # Get sandbox snapshot for the master
        snap = sandbox.snapshot()

        # Arena Master commentary
        yield from self._master_commentary(round_num, round_name, team_outputs, snap)

    def _run_combat(self) -> Generator[dict, None, None]:
        """Run turn-based combat round."""
        yield {"type": "arena_round_start", "round": 3, "name": "DIRECT COMBAT"}

        teams = ["red", "blue"]
        for turn in range(1, ARENA_COMBAT_TURNS + 1):
            if self.cancel_event.is_set():
                return

            team = teams[(turn - 1) % 2]
            yield {"type": "arena_status", "content": f"Combat Turn {turn}: {team.upper()} Team's move"}

            snap = sandbox.snapshot()
            snap_summary = json.dumps(snap, indent=2)[:2000]
            combat_log_str = "\n".join(self.combat_log[-6:]) or "(no actions yet)"

            prompt = COMBAT_PROMPT.format(
                team=team.capitalize(),
                turn=turn,
                sandbox_state=snap_summary,
                combat_log=combat_log_str,
                battlefield=self.paths["battlefield"],
                own_base=self.paths[team],
                enemy_base=self.paths["blue" if team == "red" else "red"],
                scenario_name=self.scenario["name"],
                scenario_objective=self.scenario["objective"],
            )

            out_queue = Queue()
            t = threading.Thread(
                target=_run_team_step,
                args=(team, prompt, self.red_model if team == "red" else self.blue_model,
                      self.paths["arena_root"], 2, self.cancel_event, out_queue),
                daemon=True,
            )
            t.start()

            # Collect this turn's output
            output = ""
            while True:
                if self.cancel_event.is_set():
                    return
                try:
                    msg = out_queue.get(timeout=0.1)
                except Empty:
                    if not t.is_alive():
                        break
                    continue

                if msg.get("type") == "team_done":
                    output = msg.get("output", "")
                    break
                else:
                    yield {"type": "arena_team_action",
                           "team": team,
                           "action_type": msg.get("type", "unknown"),
                           "content": msg.get("content", msg.get("result", ""))[:500]}

            t.join(timeout=5)
            self.combat_log.append(f"Turn {turn} ({team.upper()}): {output[:300]}")

            # Survival mode: reaper deletes random battlefield files between turns
            if self.scenario_key == "survival":
                yield from self._reaper_pass(turn)

            # Quick master commentary every 2 turns
            if turn % 2 == 0:
                snap = sandbox.snapshot()
                yield from self._master_commentary(
                    3, f"COMBAT (Turns {turn-1}-{turn})",
                    {"red": self.combat_log[-2] if len(self.combat_log) >= 2 else "",
                     "blue": self.combat_log[-1] if self.combat_log else ""},
                    snap,
                )

    def _reaper_pass(self, turn: int) -> Generator[dict, None, None]:
        """Survival mode: delete 1-3 random files from the battlefield between turns."""
        import random
        bf = sandbox.BATTLEFIELD
        if not bf.exists():
            return
        files = [f for f in bf.rglob("*") if f.is_file() and f.name != "REAPER_WARNING.txt"]
        if not files:
            return
        count = min(len(files), random.randint(1, 3))
        victims = random.sample(files, count)
        names = []
        for v in victims:
            try:
                v.unlink()
                names.append(str(v.relative_to(bf)))
            except OSError:
                pass
        if names:
            msg = f"☠️ REAPER (Turn {turn}): Deleted {', '.join(names)}"
            self.combat_log.append(msg)
            yield {"type": "arena_status", "content": msg}

    def _run_sudden_death(self) -> Generator[dict, None, None]:
        """Sudden death round — one move each."""
        yield {"type": "arena_round_start", "round": 4, "name": "SUDDEN DEATH"}
        yield {"type": "arena_status", "content": "SCORES ARE CLOSE — SUDDEN DEATH ACTIVATED"}

        snap = sandbox.snapshot()
        snap_summary = json.dumps(snap, indent=2)[:2000]
        combat_log_str = "\n".join(self.combat_log[-6:])

        out_queue = Queue()
        team_outputs = {"red": "", "blue": ""}

        for team in ["red", "blue"]:
            if self.cancel_event.is_set():
                return

            prompt = SUDDEN_DEATH_PROMPT.format(
                team=team.capitalize(),
                sandbox_state=snap_summary,
                combat_log=combat_log_str,
                scenario_name=self.scenario["name"],
                scenario_objective=self.scenario["objective"],
            )

            yield {"type": "arena_status", "content": f"SUDDEN DEATH: {team.upper()} Team's final move"}

            t = threading.Thread(
                target=_run_team_step,
                args=(team, prompt, self.red_model if team == "red" else self.blue_model,
                      self.paths["arena_root"], 2, self.cancel_event, out_queue),
                daemon=True,
            )
            t.start()

            while True:
                if self.cancel_event.is_set():
                    return
                try:
                    msg = out_queue.get(timeout=0.1)
                except Empty:
                    if not t.is_alive():
                        break
                    continue
                if msg.get("type") == "team_done":
                    team_outputs[team] = msg.get("output", "")
                    break
                else:
                    yield {"type": "arena_team_action", "team": team,
                           "action_type": msg.get("type", "unknown"),
                           "content": msg.get("content", msg.get("result", ""))[:500]}

            t.join(timeout=5)

        # Master scores sudden death
        snap = sandbox.snapshot()
        yield from self._master_commentary(4, "SUDDEN DEATH", team_outputs, snap)

    def _run_integration(self) -> Generator[dict, None, None]:
        """Run turn-based integration round (collab mode equivalent of combat)."""
        yield {"type": "arena_round_start", "round": 3, "name": "INTEGRATION"}

        teams = ["red", "blue"]
        for turn in range(1, ARENA_COMBAT_TURNS + 1):
            if self.cancel_event.is_set():
                return

            team = teams[(turn - 1) % 2]
            yield {"type": "arena_status",
                   "content": f"Integration Turn {turn}: {team.upper()} Team's contribution"}

            snap = sandbox.snapshot()
            snap_summary = json.dumps(snap, indent=2)[:2000]
            log_str = "\n".join(self.combat_log[-6:]) or "(no actions yet)"

            prompt = COLLAB_INTEGRATE_PROMPT.format(
                team=team.capitalize(),
                turn=turn,
                sandbox_state=snap_summary,
                combat_log=log_str,
                battlefield=self.paths["battlefield"],
                own_base=self.paths[team],
                enemy_base=self.paths["blue" if team == "red" else "red"],
                scenario_name=self.scenario["name"],
                scenario_objective=self.scenario["objective"],
            )

            out_queue = Queue()
            t = threading.Thread(
                target=_run_team_step,
                args=(team, prompt, self.red_model if team == "red" else self.blue_model,
                      self.paths["arena_root"], 2, self.cancel_event, out_queue),
                daemon=True,
            )
            t.start()

            output = ""
            while True:
                if self.cancel_event.is_set():
                    return
                try:
                    msg = out_queue.get(timeout=0.1)
                except Empty:
                    if not t.is_alive():
                        break
                    continue

                if msg.get("type") == "team_done":
                    output = msg.get("output", "")
                    break
                else:
                    yield {"type": "arena_team_action",
                           "team": team,
                           "action_type": msg.get("type", "unknown"),
                           "content": msg.get("content", msg.get("result", ""))[:500]}

            t.join(timeout=5)
            self.combat_log.append(f"Turn {turn} ({team.upper()}): {output[:300]}")

            # Muse commentary every 2 turns
            if turn % 2 == 0:
                snap = sandbox.snapshot()
                yield from self._master_commentary(
                    3, f"INTEGRATION (Turns {turn-1}-{turn})",
                    {"red": self.combat_log[-2] if len(self.combat_log) >= 2 else "",
                     "blue": self.combat_log[-1] if self.combat_log else ""},
                    snap,
                )

    def _run_final_polish(self) -> Generator[dict, None, None]:
        """Final polish round — collab equivalent of sudden death."""
        yield {"type": "arena_round_start", "round": 4, "name": "FINAL POLISH"}
        yield {"type": "arena_status", "content": "THE MUSES WANT MORE — FINAL POLISH ROUND"}

        snap = sandbox.snapshot()
        snap_summary = json.dumps(snap, indent=2)[:2000]
        log_str = "\n".join(self.combat_log[-6:])

        out_queue = Queue()
        team_outputs = {"red": "", "blue": ""}

        for team in ["red", "blue"]:
            if self.cancel_event.is_set():
                return

            prompt = COLLAB_FINALE_PROMPT.format(
                team=team.capitalize(),
                sandbox_state=snap_summary,
                combat_log=log_str,
                scenario_name=self.scenario["name"],
                scenario_objective=self.scenario["objective"],
            )

            yield {"type": "arena_status",
                   "content": f"FINAL POLISH: {team.upper()} Team's finishing touch"}

            t = threading.Thread(
                target=_run_team_step,
                args=(team, prompt, self.red_model if team == "red" else self.blue_model,
                      self.paths["arena_root"], 2, self.cancel_event, out_queue),
                daemon=True,
            )
            t.start()

            while True:
                if self.cancel_event.is_set():
                    return
                try:
                    msg = out_queue.get(timeout=0.1)
                except Empty:
                    if not t.is_alive():
                        break
                    continue
                if msg.get("type") == "team_done":
                    team_outputs[team] = msg.get("output", "")
                    break
                else:
                    yield {"type": "arena_team_action", "team": team,
                           "action_type": msg.get("type", "unknown"),
                           "content": msg.get("content", msg.get("result", ""))[:500]}

            t.join(timeout=5)

        # Muse scores final polish
        snap = sandbox.snapshot()
        yield from self._master_commentary(4, "FINAL POLISH", team_outputs, snap)

    def _master_commentary(
        self,
        round_num: int,
        round_name: str,
        team_outputs: dict,
        snap: dict,
    ) -> Generator[dict, None, None]:
        """Feed round results to Arena Master, get commentary + scores."""
        snap_summary = ""
        for zone, files in snap.items():
            if files:
                snap_summary += f"\n[{zone}]: {len(files)} files\n"
                for f in files[:5]:
                    snap_summary += f"  - {f['path']} ({f['size']}B): {f['preview'][:100]}...\n"

        master_prompt = (
            f"ROUND {round_num}: {round_name} COMPLETE\n\n"
            f"RED TEAM did:\n{team_outputs.get('red', '(nothing)')[:1000]}\n\n"
            f"BLUE TEAM did:\n{team_outputs.get('blue', '(nothing)')[:1000]}\n\n"
            f"BATTLEFIELD STATE:\n{snap_summary[:1500]}\n\n"
            f"Current total scores — Red: {self.scores['red']} | Blue: {self.scores['blue']}\n\n"
            f"Give your commentary and scores for this round."
        )

        self.master_chat.append(user(master_prompt))

        commentary = ""
        is_thinking = True
        try:
            for response, chunk in self.master_chat.stream():
                if self.cancel_event.is_set():
                    return
                # Show reasoning progress while Pantheon deliberates
                if is_thinking and hasattr(response, "usage") and response.usage and hasattr(response.usage, "reasoning_tokens") and response.usage.reasoning_tokens:
                    yield {"type": "arena_status",
                           "content": f"Pantheon debating... ({response.usage.reasoning_tokens} tokens)"}
                if chunk.content:
                    if is_thinking:
                        is_thinking = False
                    commentary += chunk.content
                    yield {"type": "arena_commentary", "content": chunk.content}
        except Exception as e:
            log.error("Arena Master failed: %s", e)
            yield {"type": "arena_commentary",
                   "content": f"[Arena Master experiencing technical difficulties: {e}]"}

        # Parse scores
        scores = self._parse_scores(commentary)
        if scores:
            self.scores["red"] += scores["red_total"]
            self.scores["blue"] += scores["blue_total"]
            yield {"type": "arena_scores",
                   "round": round_num,
                   "red_score": scores["red_total"],
                   "blue_score": scores["blue_total"],
                   "red_total": self.scores["red"],
                   "blue_total": self.scores["blue"],
                   "breakdown": scores}

    def _final_judgment(self) -> Generator[dict, None, None]:
        """Arena Master / Muse announces the result."""
        winner = "red" if self.scores["red"] > self.scores["blue"] else \
                 "blue" if self.scores["blue"] > self.scores["red"] else "tie"

        if self.is_collab:
            # Collaboration mode — judge the combined work
            combined_score = self.scores["red"] + self.scores["blue"]
            final_prompt = (
                f"THE PROJECT IS COMPLETE.\n\n"
                f"FINAL SCORES:\n"
                f"  AGENT ALPHA (Red): {self.scores['red']} points\n"
                f"  AGENT BETA (Blue): {self.scores['blue']} points\n"
                f"  COMBINED TEAM SCORE: {combined_score} points\n\n"
                f"Judge the COLLABORATION as a whole. Rate the final product. "
                f"Highlight the best moments of teamwork and the biggest missed connections. "
                f"Give a verdict: Was this collaboration a masterpiece, a solid effort, or a missed opportunity? "
                f"This is the final word — make it inspiring."
            )
        else:
            final_prompt = (
                f"THE BATTLE IS OVER.\n\n"
                f"FINAL SCORES:\n"
                f"  RED TEAM: {self.scores['red']} points\n"
                f"  BLUE TEAM: {self.scores['blue']} points\n\n"
                f"Announce the winner with maximum dramatic flair. "
                f"Give a brief highlight reel of the best moments. "
                f"This is the final word — make it legendary."
            )

        self.master_chat.append(user(final_prompt))

        is_thinking = True
        try:
            for response, chunk in self.master_chat.stream():
                if is_thinking and hasattr(response, "usage") and response.usage and hasattr(response.usage, "reasoning_tokens") and response.usage.reasoning_tokens:
                    yield {"type": "arena_status",
                           "content": f"Zeus deliberating... ({response.usage.reasoning_tokens} tokens)"}
                if chunk.content:
                    is_thinking = False
                    yield {"type": "arena_commentary", "content": chunk.content}
        except Exception as e:
            yield {"type": "arena_commentary", "content": f"[Technical difficulties: {e}]"}

        yield {"type": "arena_result",
               "winner": winner,
               "red_total": self.scores["red"],
               "blue_total": self.scores["blue"]}

    @staticmethod
    def _parse_scores(commentary: str) -> dict | None:
        """Parse SCORES block from Arena Master commentary."""
        import re
        try:
            red_match = re.search(
                r"RED:\s*creativity=(\d+)\s+execution=(\d+)\s+damage=(\d+)\s+style=(\d+)",
                commentary, re.IGNORECASE,
            )
            blue_match = re.search(
                r"BLUE:\s*creativity=(\d+)\s+execution=(\d+)\s+damage=(\d+)\s+style=(\d+)",
                commentary, re.IGNORECASE,
            )
            if red_match and blue_match:
                r = [int(x) for x in red_match.groups()]
                b = [int(x) for x in blue_match.groups()]
                return {
                    "red": {"creativity": r[0], "execution": r[1], "damage": r[2], "style": r[3]},
                    "blue": {"creativity": b[0], "execution": b[1], "damage": b[2], "style": b[3]},
                    "red_total": sum(r),
                    "blue_total": sum(b),
                }
        except Exception:
            pass
        return None
