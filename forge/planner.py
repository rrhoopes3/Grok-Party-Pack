"""
Multi-agent planner using N Grok agents (default 16) for research and strategy.

Uses the configured PLANNER_MODEL with server-side tools (web_search,
x_search, code_execution) to research and plan. The planner emits a
structured plan; tool-using execution then happens via the EXECUTOR_MODEL.
"""
from __future__ import annotations
import json
import logging
import re
import time
import threading
from typing import Generator
from xai_sdk import Client
from xai_sdk.chat import user
from xai_sdk.tools import web_search, x_search, code_execution

from forge.config import (
    PLANNER_MODEL, PLANNER_AGENT_COUNT, EXECUTOR_MODEL,
    PLANNER_MAX_REASONING_TOKENS,
)
from forge.models import PlanStep, ExecutionPlan

log = logging.getLogger("forge.planner")

MAX_RETRIES = 3
RETRY_DELAYS = [2, 4, 8]  # seconds

# Regex gate for "don't bother spinning up a 16-agent council" input.
# Matches greetings, acknowledgements, and short yes/no-ish chatter. The
# council burns ~30K reasoning tokens deliberating over these otherwise.
_CHAT_PATTERNS = re.compile(
    r"^\s*("
    r"h(ello|i|ey|owdy)|"
    r"yo|sup|greetings|"
    r"how('?s| is| are| goes)\s+(it|you|things|everything|life|things going)|"
    r"what('?s| is)\s+up|"
    r"good\s+(morning|afternoon|evening|night)|"
    r"(ok|okay|cool|nice|great|thanks|thank you|thx|ty|yep|yup|yeah|nah|nope)\b|"
    r"bruh|dude|friend|buddy|"
    r"ping|test|are you there|you there|you up"
    r")(?:\s+\w+){0,2}[\s\?\!\.,]*$",
    re.IGNORECASE,
)


def _is_trivial_input(task: str) -> bool:
    """True if `task` is a greeting / chit-chat that doesn't need a plan.

    Guardrails against false positives:
      - must be short (< 120 chars)
      - must not contain a path separator, URL, code fence, or file ext hint
      - must not contain an imperative verb in a longer sentence
    """
    t = (task or "").strip()
    if not t or len(t) > 120:
        return False
    # Anything that looks like real work bails out
    if any(marker in t for marker in ("/", "\\", "```", "http://", "https://", ".py", ".js", ".md", ".json")):
        return False
    if _CHAT_PATTERNS.match(t):
        return True
    # Short bare questions that aren't imperatives ("you alive?", "is this working?")
    if len(t) < 60 and t.endswith("?") and not re.match(
        r"^\s*(how do i|how can i|write|build|create|fix|run|show|list|find|search|explain|summarize|analyze)",
        t, re.IGNORECASE,
    ):
        return True
    return False


def _trivial_plan(task: str) -> tuple[str, list[PlanStep]]:
    """One-step plan for chit-chat — no tools, just a direct response."""
    raw = (
        "PLAN_START\n"
        "STEP 1: Respond conversationally\n"
        f"Brief, direct reply to: {task[:100]}\n"
        "TOOLS: (none)\n"
        "SUCCESS: User receives a friendly reply, no tools invoked.\n"
        "PLAN_END"
    )
    steps = [PlanStep(
        step_number=1,
        title="Respond conversationally",
        description=f"Reply briefly and directly to: {task[:200]}",
        tools_needed=[],  # no tools — executor answers from context
    )]
    return raw, steps

PLANNER_SYSTEM = """You are The Forge Planner — a 16-agent research council that analyzes tasks and creates execution plans.

Your job:
1. Research the task using web search, X search, and code execution as needed.
2. Break it down into concrete, actionable steps that an executor agent can perform.
3. Output a structured plan.

The executor agent has these tools available:

FILE OPERATIONS:
- read_file(path) — read a file (8K char cap)
- write_file(path, content) — write/create a file
- append_file(path, content) — append to a file
- list_directory(path) — list directory contents
- delete_file(path) — delete a file
- find_files(directory, pattern) — glob search for files in a directory tree
- grep_files(directory, pattern) — search file contents with regex

SHELL & CODE:
- run_command(command) — run a shell command
- run_python(code) — execute Python code and return stdout/stderr

GIT:
- git_status() — show working tree status
- git_diff(file, staged) — show changes
- git_commit(message) — stage all and commit
- git_log(count) — show recent commit history

HTTP:
- http_get(url, headers) — GET request (6K body cap)
- http_post(url, body, headers) — POST request

BROWSER:
- browser_navigate(url) — open a URL in a headless browser
- browser_screenshot(filename) — take a screenshot
- browser_click(selector) — click an element
- browser_type(selector, text) — type into an input
- browser_extract_text(selector) — extract visible text
- browser_info() — get current page URL, title, counts

DATABASE:
- query_sqlite(database, query) — execute SQL on a SQLite database

IMAGE (requires Pillow):
- resize_image(input_path, output_path, width, height) — resize an image
- convert_image(input_path, output_path) — convert image format

ARCHIVE:
- zip_files(output_path, files) — create a ZIP archive
- extract_archive(archive_path, output_dir) — extract ZIP/TAR

CLIPBOARD:
- copy_to_clipboard(text) — copy to system clipboard
- read_clipboard() — read system clipboard

TRADING (real money — Robinhood):
- get_portfolio() — get all current holdings, positions, P&L
- get_market_quote(ticker) — get current price/volume for a crypto or stock
- execute_trade(ticker, side, quantity, order_type, asset_type) — place a buy or sell order. side="buy"|"sell", order_type="market"|"limit", asset_type="crypto"|"stock"
- fetch_pcr(ticker) — get Put/Call Ratio for options analysis
- analyze_sentiment(tickers) — multi-ticker PCR sentiment analysis
- get_options_chain(ticker, expiration_date) — get options chain data
- start_trading_agent(ticker, strategy, max_position_usd, interval_minutes, model) — start autonomous trading bot
- stop_trading_agent() — stop the autonomous trading bot
- get_trading_agent_status() — check if trading bot is running, cycle count, etc.

Format your plan EXACTLY as follows (this will be parsed):

PLAN_START
STEP 1: [title]
[description of what to do]
TOOLS: [comma-separated tool names needed]

STEP 2: [title]
[description of what to do]
TOOLS: [comma-separated tool names needed]

... (as many steps as needed)

SUCCESS: [how to verify the task is complete]
PLAN_END

Be specific in descriptions. Include exact file paths, commands, and expected outcomes.

CRITICAL RULES:
- Stay focused on the user's actual task. Do NOT explore unrelated files or directories.
- If the task references a specific file (e.g. "make flappy_bird.py better"), ONLY plan steps that touch that file and directly related files.
- Do NOT inventory the entire workspace or count lines in unrelated projects.
- If the user's intent is ambiguous or unclear, keep the plan minimal (1-2 steps) and have the first step clarify what's needed before doing extensive work.
- Fewer, focused steps are better than many broad exploratory steps. Prefer 2-4 targeted steps over 5+ vague ones."""


def plan(
    client: Client,
    task: str,
    agent_count: int = 16,
    cancel_event: threading.Event | None = None,
) -> Generator[dict, None, tuple[str, list[PlanStep]]]:
    """
    Use multi-agent model to research and plan a task.

    Yields SSE-style dicts.
    Returns (raw_plan_text, parsed_steps).
    """
    count = max(4, min(16, agent_count))

    # ── Short-circuit: skip the council for chit-chat ────────────────────
    # A 16-agent reasoning council on "how goes it, bruh?" burns 30K tokens
    # before the cap trips. For conversational input, emit a single-step
    # "just reply" plan without touching the API.
    if _is_trivial_input(task):
        log.info("Planner short-circuit: trivial input (%d chars), skipping council", len(task))
        yield {"type": "status", "phase": "planning",
               "content": "Trivial input detected — skipping planner, direct reply."}
        raw, steps = _trivial_plan(task)
        yield {"type": "plan_content", "content": raw}
        yield {"type": "status", "phase": "planning", "content": f"Plan ready: {len(steps)} steps"}
        return raw, steps

    for attempt in range(MAX_RETRIES):
        try:
            chat = client.chat.create(
                model=PLANNER_MODEL,
                agent_count=count,
                tools=[web_search(), x_search(), code_execution()],
                include=["verbose_streaming"],
            )

            chat.append(user(PLANNER_SYSTEM))
            chat.append(user(f"Task: {task}"))

            yield {"type": "status", "phase": "planning", "content": f"{count} agents researching: {task[:100]}..."}

            full_response = ""
            is_thinking = True
            last_reported_tokens = 0
            deliberation_capped = False

            for response, chunk in chat.stream():
                # Check cancellation
                if cancel_event and cancel_event.is_set():
                    yield {"type": "cancelled", "content": "Task cancelled during planning"}
                    return "", []

                if is_thinking:
                    r_tokens = 0
                    if hasattr(response, "usage") and response.usage:
                        if hasattr(response.usage, "reasoning_tokens") and response.usage.reasoning_tokens:
                            r_tokens = response.usage.reasoning_tokens
                    # Hard cap on deliberation. The multi-agent council can
                    # spin up tens of thousands of reasoning tokens before
                    # emitting any plan text — at $2/$6 per Mtok this gets
                    # expensive fast. Bail with a forced minimal plan if
                    # we cross the threshold.
                    if (
                        not deliberation_capped
                        and PLANNER_MAX_REASONING_TOKENS > 0
                        and r_tokens >= PLANNER_MAX_REASONING_TOKENS
                    ):
                        deliberation_capped = True
                        log.warning(
                            "Planner reasoning cap hit (%d tokens >= %d) — "
                            "aborting deliberation, will fall back to minimal plan",
                            r_tokens, PLANNER_MAX_REASONING_TOKENS,
                        )
                        yield {"type": "status", "phase": "planning",
                               "content": f"Reasoning cap reached at {r_tokens:,} "
                                          f"tokens — emitting minimal plan."}
                        # Stop consuming the stream — break out and let the
                        # parser fallback build a single-step catch-all.
                        break
                    # Only emit every 500 reasoning tokens to avoid UI spam
                    if r_tokens and (r_tokens - last_reported_tokens) >= 500:
                        last_reported_tokens = r_tokens
                        yield {"type": "status", "phase": "planning", "content": f"Deliberating... ({r_tokens:,} reasoning tokens)"}

                if chunk.content and is_thinking:
                    is_thinking = False
                    yield {"type": "status", "phase": "planning", "content": "Council responding..."}

                if chunk.content:
                    full_response += chunk.content
                    yield {"type": "plan_content", "content": chunk.content}

            # Parse the structured plan
            steps = parse_plan(full_response)
            yield {"type": "status", "phase": "planning", "content": f"Plan ready: {len(steps)} steps"}

            return full_response, steps

        except Exception as e:
            if cancel_event and cancel_event.is_set():
                yield {"type": "cancelled", "content": "Task cancelled"}
                return "", []

            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAYS[attempt]
                log.warning("Planner attempt %d failed: %s — retrying in %ds", attempt + 1, e, delay)
                yield {"type": "status", "phase": "planning", "content": f"API error, retrying in {delay}s... ({type(e).__name__})"}
                time.sleep(delay)
            else:
                log.error("Planner failed after %d attempts: %s", MAX_RETRIES, e)
                yield {"type": "error", "content": f"Planner failed after {MAX_RETRIES} attempts: {type(e).__name__}: {e}"}
                return "", []

    return "", []


def parse_plan(plan_text: str) -> list[PlanStep]:
    """Parse the structured plan text into PlanStep objects."""
    steps = []

    # Try to extract between PLAN_START and PLAN_END
    match = re.search(r"PLAN_START\s*(.*?)\s*PLAN_END", plan_text, re.DOTALL)
    text = match.group(1) if match else plan_text

    # Split on STEP N: pattern
    step_blocks = re.split(r"STEP\s+(\d+)\s*:\s*", text)

    # step_blocks = ['preamble', '1', 'content...', '2', 'content...', ...]
    i = 1
    while i < len(step_blocks) - 1:
        try:
            step_num = int(step_blocks[i])
        except ValueError:
            i += 2
            continue
        content = step_blocks[i + 1].strip()

        # Extract title (first line) and description
        lines = content.split("\n")
        title = lines[0].strip()
        description_lines = []
        tools_needed = []

        for line in lines[1:]:
            stripped = line.strip()
            if stripped.upper().startswith("TOOLS:"):
                tools_str = stripped[6:].strip()
                tools_needed = [t.strip() for t in tools_str.split(",") if t.strip()]
            elif stripped.upper().startswith("SUCCESS:"):
                pass  # skip success criteria within steps
            else:
                description_lines.append(stripped)

        steps.append(PlanStep(
            step_number=step_num,
            title=title,
            description="\n".join(description_lines).strip(),
            tools_needed=tools_needed,
        ))
        i += 2

    # Fallback: if parsing found nothing, create a single catch-all step
    if not steps:
        log.warning("Could not parse structured plan, creating single step")
        steps = [PlanStep(
            step_number=1,
            title="Execute task",
            description=plan_text[:2000],
            tools_needed=["run_command", "read_file", "write_file"],
        )]

    return steps
