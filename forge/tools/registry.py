from __future__ import annotations
import json
import logging
import re
from pathlib import Path
from typing import Callable
from xai_sdk.chat import tool as xai_tool

log = logging.getLogger("forge.tools")

# Tools with path arguments — sandbox checks apply here
# Maps tool_name → list of argument names that must be within the sandbox
_SANDBOX_PATH_ARGS = {
    "read_file": ["path"],
    "write_file": ["path"],
    "delete_file": ["path"],
    "list_directory": ["path"],
    "append_file": ["path"],
    "find_files": ["directory"],
    "grep_files": ["directory"],
    "resize_image": ["input_path", "output_path"],
    "convert_image": ["input_path", "output_path"],
    "query_sqlite": ["database"],
    "extract_archive": ["archive_path", "output_dir"],
    "zip_files": ["output_path"],
    "provenance_sign": ["input_path", "output_path"],
    "provenance_verify": ["input_path"],
    "provenance_inspect": ["input_path"],
}

# Tools that should have their cwd overridden in sandbox mode
_SANDBOX_CWD_TOOLS = {"run_command", "run_python", "git_status", "git_diff", "git_commit", "git_log"}

# ── Tool Categories (Lazy Discovery) ─────────────────────────────────────
# Maps category name → set of tool names in that category
TOOL_CATEGORIES = {
    "filesystem": {"read_file", "write_file", "list_directory", "append_file", "delete_file"},
    "search":     {"find_files", "grep_files"},
    "shell":      {"run_command"},
    "python":     {"run_python"},
    "git":        {"git_status", "git_diff", "git_commit", "git_log"},
    "http":       {"http_get", "http_post"},
    "browser":    {"browser_navigate", "browser_screenshot", "browser_click",
                   "browser_type", "browser_extract_text", "browser_info"},
    "database":   {"query_sqlite"},
    "image":      {"resize_image", "convert_image"},
    "archive":    {"zip_files", "extract_archive"},
    "clipboard":  {"copy_to_clipboard", "read_clipboard"},
    "email":      {"email_check_dmarc", "email_check_health", "email_list_domains",
                   "email_add_domain", "email_verify_domain", "email_list_aliases",
                   "email_create_alias", "email_get_logs", "email_block_sender",
                   "email_get_analytics"},
    "escalation": {"escalate_to_human"},
    "generative_ui": {"render_widget"},
    "trading": {"fetch_pcr", "analyze_sentiment", "get_options_chain",
                "set_alert", "get_portfolio", "execute_trade", "get_market_quote",
                "start_trading_agent", "stop_trading_agent", "get_trading_agent_status"},
    "prophecy": {"prophecy_create", "prophecy_run", "prophecy_report", "prophecy_full",
                 "prophecy_status", "prophecy_interview", "prophecy_list", "prophecy_inject"},
    "surgeon":  {"surgeon_check", "surgeon_methods", "surgeon_scan", "surgeon_operate",
                 "surgeon_analyze", "surgeon_compare", "surgeon_status", "surgeon_list"},
    "tribe":    {"tribe_neuro_score", "tribe_compare", "tribe_roi_breakdown"},
    "github":   {"github_list_issues", "github_get_issue", "github_create_issue",
                 "github_create_pr", "github_pr_review", "github_ci_status",
                 "github_list_repos", "github_search_code"},
    "image_gen": {"generate_image", "generate_speech", "transcribe_audio"},
    "rag":      {"rag_ingest", "rag_query", "rag_status", "rag_clear"},
    "fake_audio": {"fake_audio_detect", "fake_audio_scan", "fake_audio_neuro_compare"},
    "deception": {"veracity_analyze", "veracity_baseline", "veracity_compare", "veracity_quick"},
    "provenance": {"provenance_sign", "provenance_verify", "provenance_inspect"},
    # The `salesforce` category now points at MCP-routed tools
    # (@salesforce/mcp via forge.mcp_client). The legacy `sf` CLI wrappers
    # are still registered globally for direct access, just under a
    # separate `salesforce_cli` category so the pack default uses MCP.
    "salesforce":     {"mcp_call_tool", "mcp_list_tools", "mcp_list_namespaces",
                       "salesforce_mcp_call", "salesforce_mcp_list_tools"},
    "salesforce_cli": {"salesforce_soql", "salesforce_describe",
                       "salesforce_record_get", "salesforce_record_update",
                       "salesforce_list_orgs"},
    "blender":    {"blender_list_tools", "blender_call_tool",
                   "blender_get_scene_info", "blender_get_object_info",
                   "blender_execute_code", "blender_viewport_screenshot"},
    "playwright": {"playwright_list_tools", "playwright_call_tool",
                   "playwright_navigate", "playwright_snapshot",
                   "playwright_screenshot", "playwright_click",
                   "playwright_fill", "playwright_eval", "playwright_close"},
    "music":      {"music_generate", "music_status"},
    "nes":        {"nes_list_roms", "nes_list_sessions", "nes_get_session",
                   "nes_coach_plan", "nes_log_event"},
    "mcp":        {"mcp_store", "mcp_recall", "mcp_call_tool",
                   "mcp_list_tools", "mcp_list_namespaces"},
}

# Reverse map: tool_name → category
TOOL_TO_CATEGORY = {}
for _cat, _tools in TOOL_CATEGORIES.items():
    for _t in _tools:
        TOOL_TO_CATEGORY[_t] = _cat

# Core tools always included (cheap, universally useful)
CORE_TOOLS = {"read_file", "write_file", "list_directory", "find_files", "grep_files", "run_command", "escalate_to_human"}


def resolve_tools_for_step(tools_needed: list[str]) -> set[str]:
    """Given a list of tool names or category hints from the planner, resolve the
    full set of tools to make available for a step.

    Always includes CORE_TOOLS. Expands category names to their member tools.
    Also includes any explicitly named tools.
    """
    resolved = set(CORE_TOOLS)
    for hint in tools_needed:
        hint_lower = hint.strip().lower()
        # Check if it's a category name
        if hint_lower in TOOL_CATEGORIES:
            resolved.update(TOOL_CATEGORIES[hint_lower])
        # Check if it's a direct tool name
        elif hint_lower in TOOL_TO_CATEGORY:
            resolved.add(hint_lower)
        else:
            # Fuzzy: check if any tool name contains the hint
            for tool_name in TOOL_TO_CATEGORY:
                if hint_lower in tool_name or tool_name in hint_lower:
                    resolved.add(tool_name)
    return resolved


# ── Task-based tool inference (for direct mode without a planner) ────────
# Maps keyword patterns → categories to include. Evaluated in order;
# all matching categories are merged, then CORE_TOOLS are added.
_TASK_TOOL_HINTS: list[tuple[re.Pattern, list[str]]] = [
    (re.compile(r"\b(git\b|commit|branch|merge|diff|log|repo)", re.I),
     ["git"]),
    (re.compile(r"\b(http|fetch|curl|api|endpoint|url|request|download)\b", re.I),
     ["http"]),
    (re.compile(r"\b(browse|screenshot|click|webpage|scrape|web\s*page)\b", re.I),
     ["browser"]),
    (re.compile(r"\b(python|script|import\s|def\s|class\s)\b", re.I),
     ["python"]),
    (re.compile(r"\b(sql|database|sqlite|query|table)\b", re.I),
     ["database"]),
    (re.compile(r"\b(image|resize|convert|png|jpg|jpeg|gif)\b", re.I),
     ["image"]),
    (re.compile(r"\b(zip|tar|archive|extract|compress)\b", re.I),
     ["archive"]),
    (re.compile(r"\b(trade|buy|sell|portfolio|stock|crypto|ticker|market)\b", re.I),
     ["trading"]),
    (re.compile(r"\b(email|dmarc|alias|inbox)\b", re.I),
     ["email"]),
    (re.compile(r"\b(github|issue|pull\s*request|pr|ci)\b", re.I),
     ["github"]),
    (re.compile(r"\b(blender|3d|mesh|scene)\b", re.I),
     ["blender"]),
    (re.compile(r"\b(rag|ingest|vector|embed)\b", re.I),
     ["rag"]),
    (re.compile(r"\b(prophecy|predict|forecast)\b", re.I),
     ["prophecy"]),
    (re.compile(r"\b(surgeon|obliterat|scan\s+code)\b", re.I),
     ["surgeon"]),
    (re.compile(r"\b(salesforce|soql|sfdc)\b", re.I),
     ["salesforce"]),
    (re.compile(r"\b(playwright|automate|e2e)\b", re.I),
     ["playwright"]),
]


def infer_tools_for_task(task: str) -> set[str] | None:
    """Infer which tools a task needs from keywords — no LLM required.

    Returns a filtered tool set, or None if the task doesn't match any
    specific category (caller should fall back to all tools).
    """
    categories: list[str] = []
    for pattern, cats in _TASK_TOOL_HINTS:
        if pattern.search(task):
            categories.extend(cats)

    if not categories:
        # Always include shell + filesystem + search — the safe minimum
        # for generic tasks that don't match a specialty.
        return set(CORE_TOOLS) | TOOL_CATEGORIES["shell"] | TOOL_CATEGORIES["python"]

    return resolve_tools_for_step(categories)


class ToolRegistry:
    """Central registry mapping tool names → SDK definitions + handlers."""

    def __init__(self):
        self._handlers: dict[str, Callable] = {}
        self._definitions: list = []
        self._raw_tools: list[dict] = []  # raw schemas for cross-provider conversion

    def register(
        self,
        name: str,
        description: str,
        parameters: dict,
        handler: Callable,
    ):
        defn = xai_tool(name=name, description=description, parameters=parameters)
        self._definitions.append(defn)
        self._handlers[name] = handler
        self._raw_tools.append({"name": name, "description": description, "parameters": parameters})
        log.info("Registered tool: %s", name)

    def get_definitions(self, only: set[str] | None = None) -> list:
        """Return list of xai_sdk tool objects to pass to chat.create().

        If `only` is provided, filters to just those tool names (lazy discovery).
        """
        if only is None:
            return list(self._definitions)
        return [d for d in self._definitions if d.function.name in only]

    def get_raw_tools(self, only: set[str] | None = None) -> list[dict]:
        """Return raw tool schemas {name, description, parameters} for non-xAI providers.

        If `only` is provided, filters to just those tool names (lazy discovery).
        """
        if only is None:
            return list(self._raw_tools)
        return [t for t in self._raw_tools if t["name"] in only]

    def execute(self, name: str, arguments: dict, sandbox_path: str = "") -> str:
        """Execute a tool by name with the given arguments. Returns JSON string.

        If sandbox_path is set, filesystem tools are restricted to that directory
        and run_command uses it as the working directory.
        """
        if name not in self._handlers:
            return json.dumps({"error": f"Unknown tool: {name}"})

        # ── Sandbox enforcement ──────────────────────────────────────
        if sandbox_path:
            sandbox_root = Path(sandbox_path).resolve()

            # Check all path-based arguments using Path.relative_to()
            # (string prefix check is bypassable: "B:\Grok2" starts with "B:\Grok")
            if name in _SANDBOX_PATH_ARGS:
                for arg_name in _SANDBOX_PATH_ARGS[name]:
                    if arg_name in arguments:
                        target = Path(arguments[arg_name]).resolve()
                        try:
                            target.relative_to(sandbox_root)
                        except ValueError:
                            log.warning("Sandbox blocked %s: %s outside %s", name, target, sandbox_root)
                            return json.dumps({
                                "error": f"Sandbox: {target} is outside allowed directory {sandbox_root}",
                            })

            # Override cwd for shell/python/git commands
            if name in _SANDBOX_CWD_TOOLS:
                arguments = {**arguments, "_sandbox_cwd": str(sandbox_root)}

        # ── Execute ──────────────────────────────────────────────────
        handler = self._handlers[name]
        try:
            result = handler(**arguments)
            if isinstance(result, str):
                return result
            return json.dumps(result, default=str)
        except Exception as e:
            # Re-raise EscalationError so the executor can handle it
            from forge.tools.escalation import EscalationError
            if isinstance(e, EscalationError):
                raise
            log.exception("Tool %s failed", name)
            return json.dumps({"error": f"{type(e).__name__}: {e}"})

    def list_tools(self) -> list[str]:
        return list(self._handlers.keys())
