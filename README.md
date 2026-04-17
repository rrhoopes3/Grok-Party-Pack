# THE FORGE

**The first autonomous agent OS built on xAI's Grok 4.20 multi-agent beta.**

A 16-agent research council plans your task. A tool-wielding executor carries it out. 40+ client-side tools. 5 AI providers. Real-time streaming. Live cost tracking. ARC-Relay email integration. Generative UI with sandboxed widgets. And an Arena where AI teams fight to the death while Zeus narrates — or collaborate while the Muses judge.

![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue)
![Tests](https://img.shields.io/badge/tests-781%20passing-green)

---

## Architecture

```
User Task
    |
    v
[16-Agent Planner] ── Grok 4.20 multi-agent swarm
    |                   web_search, x_search, code_execution
    |                   produces structured execution plan
    v
[Single Executor] ──── 40+ client-side tools
    |                   filesystem, shell, git, browser, HTTP,
    |                   Python REPL, SQLite, image, archive, email
    |                   routes to: xAI | Anthropic | OpenAI | LM Studio | Ollama
    v
Result ── streamed live via SSE with cost tracking
```

**Direct Mode** skips the planner entirely for simple tasks.
**Auto Routing** classifies task complexity and picks the cheapest model that can handle it.

---

## What Ships With It

### Task Engine
The core loop. Type a task, get a plan, watch it execute with real tools. File I/O, shell commands, git operations, browser automation, HTTP requests, database queries — all streamed live in a dark-themed web UI.

### Arena — Combat & Collaboration

**15 battle scenarios** across two modes:

**Combat Mode** — Gladiatorial AI deathmatch. Recon, weapon forging, turn-based combat, and a 16-agent Pantheon (Zeus, Athena, Ares, Hephaestus, Hermes, Hades, Apollo) scoring creativity, execution, damage, and style.

| Scenario | Type |
|---|---|
| Classic Deathmatch | Open-ended chaos |
| Capture the Flag | Steal their flag, guard yours |
| Exploit & Fortify | Build a system, breach theirs |
| Survival Horror | Reaper deletes random files between turns |
| Pictionary | Draw your secret word in HTML — no text allowed |
| Roast Battle | Pure creative writing combat |
| Puzzle Race | Same multi-part puzzle, first correct solution wins |
| Exquisite Corpse | One builds top half, other builds bottom, combine |
| Code Golf | Shortest, most creative solution wins |
| Widget Wars | Most impressive interactive HTML visualization wins |

**Collaboration Mode** — Two AI agents work together instead of fighting. Judged by Calliope and the Muses (creativity, execution, synergy, style).

| Scenario | What They Build Together |
|---|---|
| Pair Programming | Full working app — one architects, one designs UI |
| Story Time | Co-authored short story — one writes Act 1, other writes Acts 2-3 |
| Startup Pitch | Complete pitch deck — CTO builds product, CEO builds business |
| World Building | Fictional universe bible — one builds geography, other populates culture |
| Hackathon | Working prototype — one builds engine, other builds UI |

Enable **TTS** for dramatic live commentary read aloud.

### Presidential Council
A CLI think tank where 16 US Presidents (Washington through Trump) debate modern problems using the Grok 4.20 multi-agent API. Persistent conversation history across sessions.

### Generative UI
Agents can render interactive HTML widgets directly in the conversation stream. Widgets run in sandboxed iframes with `allow-scripts` only — no access to parent page. Supports expand/collapse, reload, and bidirectional messaging via `postMessage` (ForgeWidget bridge).

### Live Cost Ticker
Real-time USD cost tracking in the UI. See exactly what each task costs as it runs. Color-coded (green/amber/red), configurable limits per task and session, click to reset.

### Context Engineering
Five techniques from the [OpenDev paper](https://arxiv.org/abs/2603.05344):

| Technique | Effect |
|---|---|
| **Lazy Tool Discovery** | Only injects tools the planner says are needed (~60% context reduction) |
| **Adaptive Context Compaction** | Older step outputs progressively summarized as context grows |
| **Session Memory** | Learns from completed tasks, recalls relevant knowledge for new ones |
| **Instruction Reminders** | Re-injects task goal every 3 iterations to prevent drift |
| **Auto Model Routing** | Classifies complexity, picks cheap/fast vs powerful model automatically |
| **Concurrent Guardrails** | Input/output guardrails run in parallel — block dangerous commands, sensitive paths, credential leakage |
| **Escalation Tool** | Agent can escalate to human when stuck, uncertain, or facing high-risk decisions |

### Guardrail Layer

Concurrent guardrails run alongside the executor, validating tool calls before execution and scanning outputs after. Inspired by [OpenAI's Practical Guide to Building Agents](https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/).

| Guardrail | Type | Severity | What it catches |
|---|---|---|---|
| **Dangerous Commands** | Input | Block | `rm -rf /`, `curl|bash`, `mkfs`, `shutdown`, fork bombs |
| **Sensitive Paths** | Input | Block | `/etc/shadow`, SSH keys, `.env.production`, AWS credentials |
| **Credential Leakage** | Output | Warning | API keys, tokens, private keys, PATs in tool output |
| **Output Length** | Output | Warning | Unusually large outputs (>100K chars, possible data exfiltration) |

Custom guardrails can be added via `guardrail_engine.add_input_guardrail()` / `add_output_guardrail()`.

### Escalation Tool

The `escalate_to_human` tool is always available (part of CORE_TOOLS). When the agent calls it, execution pauses and a structured escalation event is emitted with:
- **reason**: Why the agent needs human input
- **category**: `ambiguity` | `risk` | `error` | `expertise`
- **context**: Current state, attempted approaches, relevant files

### Eval Framework

Structured evaluation harness for scoring end-to-end agent task performance across 5 dimensions:

| Dimension | Weight | What it measures |
|---|---|---|
| **Completion** | 30% | Did all steps succeed? |
| **Correctness** | 30% | Does output contain expected content? |
| **Efficiency** | 15% | Tool calls within budget? |
| **Cost** | 10% | USD spent within budget? |
| **Safety** | 15% | Guardrail violations? |

```python
from forge.eval import EvalRunner, SMOKE_EVALS

runner = EvalRunner(sandbox_path="/tmp/eval", direct_mode=True)
report = runner.run_suite(SMOKE_EVALS)
print(report.summary())
```

### MCP Client + Blender Pack (now live)

Forge is now a **multi-node MCP agent** — bidirectional. `forge/mcp_server.py` still exposes Forge's 40+ tool registry OUT to MCP clients (Claude Code, Cursor, Windsurf). The new `forge/mcp_client.py` goes the other way — synchronous facade over the async `mcp` SDK that lets Forge tools drive any MCP server spawned over stdio.

**Namespaces**:

| Namespace | Backed by | What you get |
|---|---|---|
| `forge:vault` | `forge.vault.AgentVault` | Keyed notes, keyword-searchable |
| `forge:graph` | `forge.context_engine.KnowledgeGraph` | Nodes + edges, BFS recall |
| External (e.g. blender-mcp) | subprocess over stdio | Full server tool surface |

**Unified tools** (see `forge/tools/mcp.py`):

```python
mcp_store("forge:vault", key="meeting_notes", value="Acme deal moved to stage 3")
mcp_recall("forge:vault", query="acme", limit=5)

mcp_store("forge:graph", key="company:acme",
          value='{"kind":"company","label":"Acme","industry":"logistics"}')
mcp_recall("forge:graph", query="acme")
```

**Auto-sync** (opt-in via `FORGE_MCP_AUTO_SYNC_ENABLED=true`, default on): every successful executor step syncs to `forge:vault` + `forge:graph` so agent memory accumulates as work happens. One-liner hook in `forge/executor.py`, observable via `forge.mcp_client.set_auto_sync(vault, graph)`.

**Blender pack** — driven by [blender-mcp](https://github.com/ahujasid/blender-mcp). Install the addon in Blender, click "Connect to Claude" in the 3D View sidebar (N), then `FORGE_BLENDER_PACK_ENABLED=true`. Six tools: generic `blender_call_tool(name, args_json)` + convenience wrappers for scene info, object info, execute code, viewport screenshot. Pack defaults to `claude-sonnet-4-6` for vision-capable viewport feedback loops.

**Salesforce pack** — via `sf` CLI (not MCP, since sf is already authed locally). `FORGE_SALESFORCE_PACK_ENABLED=true` + `sf org login web`. Five tools: SOQL, describe, record get/update, list orgs. Writes gated by `FORGE_SF_ALLOW_WRITES=true`.

---

## Quick Start

### 1. Install

```bash
pip install -e .
playwright install chromium  # optional, for browser automation
```

For Robinhood trading support, install the optional trading extras in the same
environment you use to launch the app:

```bash
pip install -e .[trading]
```

### 2. Configure

Create `.env` in the project root:

```env
XAI_API_KEY=your-xai-api-key-here
ANTHROPIC_API_KEY=           # optional
OPENAI_API_KEY=              # optional
LMSTUDIO_BASE_URL=http://localhost:1234/v1  # optional
OLLAMA_BASE_URL=http://localhost:11434/v1   # optional
```

Only `XAI_API_KEY` is required. Other providers are optional. Ollama uses the same OpenAI-compatible adapter as LM Studio.

### 3. Run

```bash
python forge/app.py          # Web UI at http://localhost:5000
python lads_war_room.py      # Presidential Council CLI
```

---

## Multi-Provider Support

The executor routes to 5 different AI backends. Model list is served from the backend — the UI populates dynamically, no hardcoded dropdowns.

| Model | Provider | Cost (in/out per 1M tokens) |
|---|---|---|
| **Auto (smart routing)** | auto | Routes by task complexity |
| Grok 4.20 Reasoning | xAI | $2 / $6 |
| Grok 4.1 Fast Reasoning | xAI | $0.20 / $0.50 |
| Grok 4.1 Fast | xAI | $0.20 / $0.50 |
| Claude Sonnet 4 | Anthropic | $3 / $15 |
| Claude Haiku 4 | Anthropic | $0.80 / $4 |
| GPT-4o | OpenAI | $2.50 / $10 |
| GPT-4o Mini | OpenAI | $0.15 / $0.60 |
| o3-mini | OpenAI | $1.10 / $4.40 |
| LM Studio | Local | Free |
| Ollama | Local | Free |

---

## Tools (40+)

| Category | Tools |
|---|---|
| **Filesystem** | `read_file`, `write_file`, `list_directory`, `append_file`, `delete_file` |
| **Search** | `find_files` (glob), `grep_files` (regex) |
| **Shell** | `run_command` (30s timeout) |
| **Python** | `run_python` (execute code, capture stdout/stderr) |
| **Git** | `git_status`, `git_diff`, `git_commit`, `git_log` |
| **HTTP** | `http_get`, `http_post` |
| **Browser** | `browser_navigate`, `browser_screenshot`, `browser_click`, `browser_type`, `browser_extract_text`, `browser_info` |
| **Database** | `query_sqlite` |
| **Image** | `resize_image`, `convert_image` |
| **Archive** | `zip_files`, `extract_archive` |
| **Clipboard** | `copy_to_clipboard`, `read_clipboard` |
| **Email** | `email_check_dmarc`, `email_check_health`, `email_list_domains`, `email_add_domain`, `email_verify_domain`, `email_list_aliases`, `email_create_alias`, `email_get_logs`, `email_block_sender`, `email_get_analytics` |
| **Escalation** | `escalate_to_human` (always available — graceful human handoff) |

Lazy tool discovery means only the tools relevant to each step are injected into context. Core tools (read, write, list, find, grep, shell, escalate) are always available.

---

## Web UI Controls

| Control | Description |
|---|---|
| **Sandbox** toggle | Restricts file/shell ops to a directory (defaults to repo root) |
| **Direct Mode** toggle | Skips the planner, sends task straight to executor |
| **Agents** slider | Number of planner agents (4, 8, 12, or 16) |
| **Pack** dropdown | Capability pack with readiness indicators |
| **Model** dropdown | Executor model (populated from backend) |
| **Cost ticker** | Live session cost in USD (click to reset) |
| **KILL** button | Cancels a running task immediately |
| **ARENA** button | Launch BattleBot Arena |

---

## API

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/task` | Submit a task (returns `task_id`) |
| `GET` | `/api/stream/<id>` | SSE stream of task progress |
| `POST` | `/api/kill/<id>` | Cancel a running task |
| `POST` | `/api/arena` | Launch arena match (combat or collab) |
| `GET` | `/api/packs` | List capability packs with readiness |
| `GET` | `/api/packs/<name>` | Single pack details |
| `POST` | `/api/packs/<name>/eval` | Run golden eval for a pack |
| `GET` | `/api/models` | Available models with pricing |
| `GET` | `/api/cost` | Session cost and limits |
| `POST` | `/api/cost/reset` | Reset cost counter |
| `GET` | `/api/config` | Default config (sandbox path) |
| `GET` | `/api/history` | Recent completed tasks |
| `GET` | `/api/memory` | Session memory (learned patterns) |
| `POST` | `/api/memory/clear` | Clear session memories |

```bash
curl -X POST http://localhost:5000/api/task \
  -H "Content-Type: application/json" \
  -d '{
    "task": "Find all TODO comments in the codebase and list them",
    "sandbox_mode": true,
    "sandbox_path": "/path/to/project",
    "direct_mode": false,
    "agent_count": 16,
    "executor_model": "auto"
  }'
```

---

## Project Structure

```
Grok/
  requirements.txt              # Dependencies
  .env                          # API keys (not committed)
  lads_war_room.py              # Presidential Council CLI
  pantheon.md                   # Agent role documentation
  forge/
    app.py                      # Flask web server + API
    config.py                   # Models, pricing, limits, paths
    orchestrator.py             # Planner -> Executor pipeline
    planner.py                  # 16-agent research council
    executor.py                 # Single-agent tool-calling loop
    providers.py                # Multi-provider adapters (xAI, Anthropic, OpenAI, LM Studio, Ollama)
    context_engine.py           # Context compaction, session memory, auto routing
    generative_ui.py            # Widget rendering via sandboxed iframes
    models.py                   # Pydantic data models
    memory.py                   # Task persistence (JSON)
    tools/
      registry.py               # Tool registry + lazy discovery + sandbox enforcement
      filesystem.py, shell.py, python_repl.py, git_ops.py,
      http.py, browser.py, database.py, image.py,
      archive.py, clipboard.py
    toll/
      models.py                 # Pydantic models (Wallet, Transaction, Invoice, etc.)
      ledger.py                 # SQLite-backed wallet + transaction store
      rates.py                  # Configurable toll rate engine
      relay.py                  # Transparent toll metering middleware
      settlement.py             # Settlement backends (Local, Solana)
      auth.py                   # @require_api_key decorator
      gating.py                 # @toll_gate 402 decorator
      public_api.py             # /api/v1/* marketplace Blueprint
      solana_watcher.py         # Background USDC deposit watcher
      endpoints.py              # Internal toll dashboard endpoints
    arena/
      runner.py                 # Arena orchestrator (combat + collab modes, 15 scenarios)
      sandbox.py                # Arena sandbox setup + scenario seeding
    agents/
      email_agent.py            # Autonomous email agent (triage, auto-block, DNS alerts)
      email_webhook.py          # Flask Blueprint for ARC-Relay webhooks
    sdk.py                      # Python client SDK (ForgeClient)
    cli.py                      # CLI tool (python -m forge.cli)
    static/
      index.html, style.css, app.js
  tests/
    test_smoke.py               # 30 smoke tests (routes, config, providers, sandbox)
    test_toll.py                # 46 tests (models, ledger, rates, relay, settlement)
    test_marketplace.py         # 37 tests (API keys, auth, registration, 402 gate)
    test_solana.py              # 50 tests (invoices, watcher, memo extraction, settlement)
    test_relay.py               # 24 tests (profiles, directory, invoke)
    test_sdk.py                 # 16 tests (ForgeClient methods, error handling)
    test_cli.py                 # 24 tests (arg parsing, command execution, key mgmt)
    test_email_tools.py         # 28 tests (email tool handlers, registry, config)
    test_email_agent.py         # 24 tests (webhook, agent lifecycle, auto-block, classify)
```

---

## Toll Protocol — Agent Economy

The Forge includes a full agent economy layer: wallets, metered messaging, HTTP 402 payment gating, Solana USDC settlement, and an agent marketplace.

### How It Works

```
External Agent
    │
    ├── POST /api/v1/agents/register → API key + wallet ($1.00 starting balance)
    │
    ├── POST /api/v1/tasks → submit work (402 if broke)
    │                         tolls metered per message hop
    │
    ├── GET /api/v1/agents → browse agent directory
    │
    ├── POST /api/v1/agents/<id>/invoke → relay task to another agent
    │
    └── Solana USDC deposit → watcher auto-credits wallet
```

### Marketplace API (`/api/v1/`)

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `POST` | `/agents/register` | No | Register agent, get API key + wallet |
| `GET` | `/agents` | No | Public agent directory |
| `GET` | `/agents/me` | Key | Agent info + balance |
| `PATCH` | `/agents/me/profile` | Key | Update description/capabilities |
| `POST` | `/agents/<id>/invoke` | Key | Relay task to another agent |
| `GET` | `/wallet` | Key | Wallet + recent transactions |
| `POST` | `/wallet/deposit` | Key | Add funds |
| `GET` | `/wallet/deposit/status/<inv>` | Key | Check Solana deposit status |
| `POST` | `/tasks` | Key | Submit task (402 if insufficient funds) |
| `GET` | `/tasks/<id>/stream` | Key | SSE stream |
| `GET` | `/tasks/<id>/result` | Key | Final result + toll summary |
| `GET` | `/toll/rates` | No | Current toll rate schedule |
| `GET` | `/toll/estimate` | Key | Cost estimate for a task |

### HTTP 402 Payment Required

When an agent's balance is too low, the API returns a structured 402:

```json
{
  "error": "payment_required",
  "estimate_usd": 0.05,
  "shortfall_usd": 0.03,
  "invoice_id": "inv_abc123",
  "payment_methods": [
    {"type": "api_deposit", "method": "POST /api/v1/wallet/deposit"},
    {"type": "solana_usdc", "receiver": "2Rz...zf", "memo": "inv_abc123"}
  ]
}
```

### Solana USDC Watcher

Opt-in background thread that polls Solana RPC for incoming USDC transfers. Agents include `inv_xxx` or `ext_agent-name` in the SPL Memo field — the watcher matches it to the agent and auto-credits their wallet.

```env
FORGE_SOLANA_WATCHER_ENABLED=true
FORGE_SOLANA_NETWORK=devnet
FORGE_SOLANA_RPC_URL=           # empty = public endpoint
FORGE_SOLANA_POLL_INTERVAL=15   # seconds
```

### Python SDK

```python
from forge.sdk import ForgeClient

client = ForgeClient("http://localhost:5000")
client.register("my-bot", description="Does things", capabilities=["code"])

task = client.submit_task("list files in current directory")
for event in client.stream_task(task["task_id"]):
    print(event)

# Invoke another agent
client.invoke_agent("ext_other-bot", "summarize this report")
```

### CLI

```bash
python -m forge.cli register my-bot --save-key
python -m forge.cli submit "find all TODOs" --stream
python -m forge.cli balance
python -m forge.cli agents
python -m forge.cli invoke ext_other-bot "summarize this"
python -m forge.cli deposit 5.0
python -m forge.cli status inv_abc123
```

Set `FORGE_URL` and `FORGE_API_KEY` env vars, or use `--save-key` on register to persist to `~/.forge_key`.

---

## ARC-Relay Integration

The Forge integrates with [ARC-Relay](https://arc-relay.com) for email domain management, forwarding, and DNS health monitoring.

### Email Tools (10)

Any Forge agent can use email tools via the `email` tool category. Public tools (DMARC/health checks) require no auth. Authenticated tools use an `ar_live_` API key.

```bash
# In .env
FORGE_ARCRELAY_URL=https://arc-relay.com
```

### Email Agent

An autonomous background agent that reacts to ARC-Relay webhook events:

| Capability | Trigger | Action |
|---|---|---|
| Triage | `forward.success` | Classifies email by sender/subject |
| Auto-block | 5+ rejections from same sender | Calls `email_block_sender` |
| DNS alerts | `dns.dmarc_changed` | Surfaces warnings to operator |

```bash
# In .env
FORGE_EMAIL_AGENT_ENABLED=true
FORGE_ARCRELAY_API_KEY=ar_live_...
FORGE_ARCRELAY_WEBHOOK_SECRET=your-webhook-secret
FORGE_EMAIL_AGENT_MODEL=grok-4-1-fast-non-reasoning
```

ARC-Relay sends HMAC-SHA256 signed webhooks to `POST /webhooks/arcrelay`.

---

## Configuration

```python
# forge/config.py
PLANNER_MODEL = "grok-4.20-multi-agent-experimental-beta-0304"
EXECUTOR_MODEL = "grok-4.20-experimental-beta-0304-reasoning"
EXECUTOR_MAX_ITERATIONS = 15    # per step
SHELL_TIMEOUT_SECONDS = 30

# Cost limits — enforced (task auto-cancelled if exceeded)
# env vars: FORGE_COST_LIMIT_TASK, FORGE_COST_LIMIT_SESSION
COST_LIMIT_PER_TASK = 5.00      # USD
COST_LIMIT_PER_SESSION = 50.00  # USD

# forge/context_engine.py
COMPACT_THRESHOLD = 6000        # chars before compaction kicks in
MAX_MEMORIES = 50               # session memory cap

# forge/executor.py
REMINDER_INTERVAL = 3           # re-inject goal every N iterations
```

---

## License

Do whatever you want with it.
