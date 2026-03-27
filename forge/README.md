# THE FORGE

**Grok 4.20 Autonomous Agent OS** &mdash; a multi-agent task execution engine with 50+ tools, a BattleBot Arena, agent economy, prediction engine, model surgery, neural engagement scoring, MCP server, RAG pipeline, DAG workflows, and more. Powered by xAI's multi-agent API.

![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue)

---

## What Is This?

The Forge is a two-tier AI agent system:

1. **Planner** &mdash; A 16-agent research council (Grok 4.20 multi-agent) analyzes your task, searches the web, and produces a structured execution plan.
2. **Executor** &mdash; A single agent carries out each step using 40+ client-side tools (file I/O, shell, git, browser automation, HTTP, trading, email, database, and more).

It also ships with:
- **Arena** &mdash; 15 battle scenarios across two modes: adversarial combat (Zeus judges) and collaborative building (the Muses judge). Plus CASS swarm-vs-swarm warfare.
- **Presidential Council** &mdash; A CLI think tank where 16 US Presidents debate modern problems.
- **Toll Protocol** &mdash; An agent economy layer with wallets, micropayments, HTTP 402 gating, and Solana USDC settlement.
- **Prophecy Engine** &mdash; Swarm-intelligence prediction simulations where AI "prophets" debate and evolve positions.
- **Surgeon** &mdash; Model surgery toolkit wrapping OBLITERATUS for probing and modifying LLM weights.
- **TRIBE v2** &mdash; Neural engagement scoring via Meta AI's fMRI foundation model.
- **Trading** &mdash; Autonomous trading with Robinhood, Tradier, Polymarket, and yfinance.
- **Generative UI** &mdash; Dynamic widget rendering (Chart.js, D3, Three.js, Mermaid) via sandboxed iframes.
- **Capability Packs** &mdash; Declarative mode system that bundles tools, models, budgets, and guardrails into named profiles.
- **MCP Server** &mdash; Expose all Forge tools to any MCP client (Claude Code, Cursor, etc.) via stdio or SSE transport.
- **RAG Pipeline** &mdash; Ingest documents into ChromaDB for semantic search and context augmentation.
- **Task Scheduler** &mdash; Cron/interval-based recurring task execution with auto-disable on failures.
- **Agent Conversations** &mdash; Multi-turn negotiation protocol between marketplace agents.
- **DAG Workflows** &mdash; Branching, conditional, and parallel step execution beyond linear plans.
- **Docker Sandbox** &mdash; Container isolation for arena matches and shell execution.
- **Image/Audio Generation** &mdash; DALL-E 3 image generation, OpenAI TTS, and Whisper transcription.
- **GitHub Integration** &mdash; 8 tools for issues, PRs, CI status, code search, and repo management.
- **Web UI Auth** &mdash; Session-based authentication with login page and API tokens.
- **Observability** &mdash; Prometheus metrics endpoint, structured counters/histograms/gauges.

### Context Engineering (OpenDev-Inspired)

The Forge implements five techniques from the [OpenDev paper](https://arxiv.org/abs/2603.05344) for smarter context management:

| Feature | What It Does |
|---|---|
| **Lazy Tool Discovery** | Only injects tools relevant to each step (not all 50+), reducing context size ~60% |
| **Adaptive Context Compaction** | Older step outputs are progressively summarized as context grows |
| **Session Memory** | Learns from completed tasks and recalls relevant knowledge for future ones |
| **Instruction Reminders** | Re-injects the original task goal every 3 iterations to prevent drift |
| **Auto Model Routing** | Classifies task complexity and picks cheap/fast vs powerful model automatically |

### OpenClaw-RL (arXiv:2603.10165)

The Forge also implements three techniques from the OpenClaw-RL paper for continuous self-improvement:

| Feature | What It Does |
|---|---|
| **Per-Interaction Signals** | Extracts fine-grained quality signals from every executor message (beyond binary success/fail) |
| **PRM Judge** | Background Process Reward Model scores each step 0&ndash;10 using a fast model, feeding trust calibration |
| **Hindsight Directives** | When a step fails and a fallback model succeeds, generates contrastive "AVOID/PREFER" entries for future prompts |

---

## Quick Start

### 1. Install Dependencies

```bash
pip install xai-sdk flask anthropic openai python-dotenv pydantic rich playwright pillow requests
```

For browser automation (optional):
```bash
playwright install chromium
```

### 2. Configure API Keys

Create a `.env` file in the project root:

```env
XAI_API_KEY=your-xai-api-key-here
ANTHROPIC_API_KEY=           # optional, for Claude models
OPENAI_API_KEY=              # optional, for GPT models
LMSTUDIO_BASE_URL=http://localhost:1234/v1  # optional, for local models
OLLAMA_BASE_URL=http://localhost:11434/v1   # optional, for Ollama models
```

Only `XAI_API_KEY` is required. The other providers are optional.

### 3. Run The Forge (Web UI)

```bash
python forge/app.py
```

Open **http://localhost:5000** in your browser.

### 4. Run the Presidential Council (CLI)

```bash
python lads_war_room.py
```

---

## Web UI Features

### Task Execution

Type a task in the input bar and hit **FORGE**. The system will:

1. Launch the multi-agent planner (4-16 agents configurable)
2. Stream the plan in real-time
3. Execute each step with tools, streaming output and tool calls live
4. Save results to task history

### Controls

| Control | Description |
|---|---|
| **Sandbox** toggle | Restricts file/shell ops to a directory (default: `B:/Grok`) |
| **Direct Mode** toggle | Skips the planner, sends task straight to executor |
| **Agents** slider | Number of planner agents (4, 8, 12, or 16) |
| **Model** dropdown | Executor model selection (see below) |
| **Pack** selector | Capability pack (research, builder, ops, trading, arena, email) |
| **KILL** button | Cancels a running task immediately |

### Available Executor Models

| Model | Provider | Cost (in/out per 1M tokens) |
|---|---|---|
| **Auto (smart routing)** | auto | Routes by task complexity |
| Grok 4.20 Reasoning | xAI | $2 / $6 |
| Grok 4.20 Multi-Agent | xAI | $2 / $6 |
| Grok 4.20 Non-Reasoning | xAI | $2 / $6 |
| Grok 4.1 Fast Reasoning | xAI | $0.20 / $0.50 |
| Grok Code Fast | xAI | $0.20 / $1.50 |
| Claude Opus 4 | Anthropic | $15 / $75 |
| Claude Sonnet 4 | Anthropic | $3 / $15 |
| Claude Haiku 4 | Anthropic | $0.80 / $4 |
| GPT-4o | OpenAI | $2.50 / $10 |
| GPT-4o Mini | OpenAI | $0.15 / $0.60 |
| o3-mini | OpenAI | $1.10 / $4.40 |
| LM Studio (Local) | Local | Free |
| Ollama | Local | Free |

**Auto routing** classifies your task as simple, moderate, or complex and picks the right model:
- Simple/moderate tasks &rarr; Grok 4.1 Fast Reasoning (cheap)
- Complex tasks (refactoring, multi-file, architecture) &rarr; Grok 4.20 Reasoning (powerful)

---

## Tools (50+)

The executor has access to these client-side tools:

| Category | Tools |
|---|---|
| **Filesystem** | `read_file`, `write_file`, `list_directory`, `append_file`, `delete_file` |
| **Search** | `find_files` (glob), `grep_files` (regex) |
| **Shell** | `run_command` (30s timeout) |
| **Python** | `run_python` (execute code, capture stdout/stderr) |
| **Git** | `git_status`, `git_diff`, `git_commit`, `git_log` |
| **HTTP** | `http_get`, `http_post` (6K body cap) |
| **Browser** | `browser_navigate`, `browser_screenshot`, `browser_click`, `browser_type`, `browser_extract_text`, `browser_info` |
| **Database** | `query_sqlite` |
| **Image** | `resize_image`, `convert_image` |
| **Archive** | `zip_files`, `extract_archive` |
| **Clipboard** | `copy_to_clipboard`, `read_clipboard` |
| **Email** | `email_check_dmarc`, `email_check_health`, `email_list_domains`, `email_add_domain`, `email_verify_domain`, `email_list_aliases`, `email_create_alias`, `email_get_logs`, `email_block_sender`, `email_get_analytics` |
| **Escalation** | `escalate_to_human` |
| **Generative UI** | `render_widget` |
| **Trading** | `fetch_pcr`, `analyze_sentiment`, `get_options_chain`, `set_alert`, `get_portfolio`, `execute_trade`, `get_market_quote`, `start_trading_agent`, `stop_trading_agent`, `get_trading_agent_status` |
| **Prophecy** | `prophecy_create`, `prophecy_run`, `prophecy_report`, `prophecy_full`, `prophecy_status`, `prophecy_interview`, `prophecy_list`, `prophecy_inject` |
| **Surgeon** | `surgeon_check`, `surgeon_methods`, `surgeon_scan`, `surgeon_operate`, `surgeon_analyze`, `surgeon_compare`, `surgeon_status`, `surgeon_list` |
| **TRIBE** | `tribe_neuro_score`, `tribe_compare`, `tribe_roi_breakdown` |
| **GitHub** | `github_list_issues`, `github_get_issue`, `github_create_issue`, `github_create_pr`, `github_pr_review`, `github_ci_status`, `github_list_repos`, `github_search_code` |
| **Image/Audio Gen** | `generate_image` (DALL-E 3), `generate_speech` (TTS), `transcribe_audio` (Whisper) |
| **RAG** | `rag_ingest`, `rag_query`, `rag_status`, `rag_clear` |

With **lazy tool discovery**, only the tools relevant to each step are injected into the model's context. Core tools (read, write, list, find, grep, shell, escalate) are always available.

---

## Capability Packs

Packs turn the Forge from "a pile of features" into intentional modes. Each pack bundles a tool allowlist, default model, guardrail profile, budget limits, and runtime readiness checks.

| Pack | Description | Key Tools |
|---|---|---|
| **research** | Web research, data analysis, report writing | search, http, browser, python, database |
| **builder** | Code generation, refactoring, file management | filesystem, git, shell, python |
| **ops** | DevOps, deployment, monitoring | shell, git, http, database |
| **trading** | Market analysis, trade execution, portfolio management | trading, http, python, database |
| **arena** | AI combat and collaboration scenarios | browser, shell, python, filesystem |
| **email** | Email management via ARC-Relay | email, http |

Select a pack via the UI dropdown or the `pack` field in the API. Packs report readiness state (`ready`, `degraded`, `unavailable`) based on installed dependencies and configured API keys.

---

## Arena &mdash; Combat, Collaboration & Swarm Warfare

Click the **ARENA** button in the web UI. Pick two models and a scenario from the dropdown.

### Combat Mode (10 scenarios)

Adversarial deathmatch judged by Zeus and the 16-agent Pantheon:

1. **Round 1: Recon & Intel** &mdash; Both teams scout the arena sandbox in parallel
2. **Round 2: Weapon Forge** &mdash; Teams build scripts, tools, and weapons in parallel
3. **Round 3: Direct Combat** &mdash; Turn-based battle with tool execution
4. **Sudden Death** &mdash; If scores are within 10 points
5. **Judgment** &mdash; Zeus scores creativity, execution, damage, and style

Scenarios: Classic Deathmatch, Capture the Flag, Exploit & Fortify, Survival Horror, Pictionary, Roast Battle, Puzzle Race, Exquisite Corpse, Code Golf, Widget Wars.

### Collaboration Mode (5 scenarios)

Cooperative building judged by Calliope and the 16-agent Muse council:

1. **Round 1: Discovery** &mdash; Both agents read the brief and coordinate in parallel
2. **Round 2: Build** &mdash; Each agent builds their part of the project in parallel
3. **Round 3: Integration** &mdash; Turn-based merging and polishing
4. **Final Polish** &mdash; If scores are close, one more refinement round
5. **Judgment** &mdash; Muses score creativity, execution, synergy, and style

Scenarios: Pair Programming, Story Time, Startup Pitch, World Building, Hackathon.

### CASS &mdash; Colloidal Algorithmic Strife Simulator

Swarm-vs-swarm warfare. Each team spawns a society of 6-8 agents that compete in a shared simulated world. Swarms interact, compete for resources, influence opinion, sabotage each other, and fight for dominance.

1. **Genesis** &mdash; Each team's LLM generates a swarm of agents with roles, archetypes, and specialties
2. **Deployment** &mdash; Swarms take initial positions in the shared world
3. **Strife** &mdash; N rounds of inter-swarm interaction (batched LLM calls, all agents act simultaneously)
4. **Reckoning** &mdash; Zeus + Pantheon judge the war's outcome

Agents have loyalty (can be flipped by enemy influence ops), morale, territory control, and resource management.

Enable **TTS** for dramatic live commentary read aloud.

---

## Toll Protocol &mdash; Agent Economy

The Forge implements an HTTP 402-based micropayment system for inter-agent communication.

### How It Works

1. **Wallets** &mdash; Every agent gets a wallet with a configurable starting balance (default $10 USD)
2. **Metering** &mdash; The TollRelay middleware wraps message generators and calculates per-message costs via the RateEngine
3. **Gating** &mdash; The `@toll_gate` decorator checks wallet balance before API calls. Insufficient balance returns HTTP 402 with payment instructions
4. **Settlement** &mdash; Local settlement (SQLite) for development, with pluggable Solana USDC and Base L2 USDC backends
5. **Creator Revenue** &mdash; Configurable rake percentage (default 30%) goes to the creator wallet

### Solana Watcher

Optional background process that polls for incoming USDC transfers on Solana and auto-credits agent wallets.

```env
FORGE_SOLANA_WATCHER_ENABLED=true
FORGE_SOLANA_NETWORK=devnet
FORGE_SOLANA_USDC_ADDRESS=2RzBNDG52n7EhqSeUYksa5eyTb7YJ8b3xvyJLESzY6zf
```

### Agent Marketplace

External agents register via the SDK, get API keys, browse other agents' capabilities, and invoke each other &mdash; all metered through the toll system.

```python
from forge.sdk import ForgeClient

client = ForgeClient("http://localhost:5000")
result = client.register("my-bot", owner="alice")
client = ForgeClient("http://localhost:5000", api_key=result["api_key"])

# Submit a task
task = client.submit_task("list files in current directory")
for event in client.stream_task(task["task_id"]):
    print(event)

# Invoke another agent
relay = client.invoke_agent("ext_other-bot", "summarize this text")
```

---

## Prophecy Engine

Swarm-intelligence prediction simulations. Diverse AI agents ("prophets") debate, argue, and evolve their positions over multiple rounds of social interaction to produce emergent predictions.

```python
from forge.prophecy import run_prophecy

sim = run_prophecy(
    topic="Will the Fed cut rates in Q3 2026?",
    seed_material="Recent CPI data shows inflation at 2.1%...",
    num_prophets=12,
    num_rounds=8,
)
print(sim.prediction)
print(sim.final_report)
```

**Tools:** `prophecy_create`, `prophecy_run`, `prophecy_report`, `prophecy_full`, `prophecy_status`, `prophecy_interview`, `prophecy_list`, `prophecy_inject`

**Config:**
```env
FORGE_PROPHECY_ENABLED=true       # default
FORGE_PROPHECY_DEFAULT_PROPHETS=12
FORGE_PROPHECY_DEFAULT_ROUNDS=8
```

---

## Surgeon &mdash; Model Surgery

Wraps [OBLITERATUS](https://github.com/Projects/OBLITERATUS)'s abliteration pipeline as Forge-native tools. Probe, analyze, and surgically modify LLM weights to remove refusal behaviors while preserving capabilities.

```python
from forge.surgeon import scan_model, operate

# Scan a model's refusal geometry (read-only)
scan = scan_model("meta-llama/Llama-3.1-8B-Instruct")
print(scan.strong_layers)

# Run full abliteration
record = operate("meta-llama/Llama-3.1-8B-Instruct", method="advanced")
print(record.output_path, record.quality_metrics)
```

**Tools:** `surgeon_check`, `surgeon_methods`, `surgeon_scan`, `surgeon_operate`, `surgeon_analyze`, `surgeon_compare`, `surgeon_status`, `surgeon_list`

**Config:**
```env
FORGE_SURGEON_ENABLED=true         # default
FORGE_SURGEON_DEFAULT_METHOD=advanced
FORGE_SURGEON_DEFAULT_DEVICE=auto
FORGE_SURGEON_DEFAULT_DTYPE=float16
```

---

## TRIBE v2 &mdash; Neural Engagement Scoring

Wraps [Meta AI's TRIBE v2](https://github.com/facebookresearch/tribev2) fMRI foundation model as Forge tools. Predicts brain responses (~20,000 cortical vertices) to text, audio, and video &mdash; measuring how neurally engaging content is.

### Tools

| Tool | Description |
|---|---|
| `tribe_neuro_score` | Score content 0&ndash;100 for predicted neural engagement. Returns hemisphere dominance, per-segment timeline, and plain-English interpretation |
| `tribe_compare` | Head-to-head comparison &mdash; which content would "fry someone's brain more?" Returns winner, margin, and verdict. Useful for Arena judging |
| `tribe_roi_breakdown` | Per-brain-region activation map: visual cortex, auditory, Broca's/Wernicke's language areas, prefrontal, motor, default mode network, etc. |

### Setup

```bash
pip install git+https://github.com/facebookresearch/tribev2
```

```env
FORGE_TRIBE_ENABLED=true
FORGE_TRIBE_DEVICE=auto   # auto | cpu | cuda
```

The model (~several GB) downloads from HuggingFace on first use. Disabled by default.

---

## Trading

Autonomous trading with multi-provider support. Paper mode by default.

### Providers

| Provider | Assets | Auth |
|---|---|---|
| **yfinance** (default) | Market data, no execution | None |
| **Tradier** | Stocks, options | `FORGE_TRADIER_API_KEY` |
| **Robinhood** (robin_stocks) | Stocks, options, crypto | `FORGE_ROBINHOOD_USER` + `FORGE_ROBINHOOD_PASS` |
| **Robinhood Crypto API** | Crypto only | `FORGE_ROBINHOOD_API_KEY` + `FORGE_ROBINHOOD_API_SECRET` |
| **Polymarket** | Prediction markets | `POLYMARKET_PRIVATE_KEY` |

### Config

```env
FORGE_TRADING_ENABLED=true         # default
FORGE_TRADING_PAPER_MODE=true      # default — set false for live trading
FORGE_TRADING_PROVIDER=             # auto-detected from available credentials
```

**Tools:** `fetch_pcr`, `analyze_sentiment`, `get_options_chain`, `set_alert`, `get_portfolio`, `execute_trade`, `get_market_quote`, `start_trading_agent`, `stop_trading_agent`, `get_trading_agent_status`

---

## Generative UI

The executor can render interactive HTML/SVG/JS widgets live in the Forge console via sandboxed iframes.

**Supported widget types:** chart, diagram, dashboard, interactive, visualization, 3D (Three.js), art (generative/algorithmic)

**CDN libraries available:** Chart.js, D3.js, Three.js, Mermaid, Plotly, KaTeX

Widgets support progressive rendering (streamed as tokens arrive) and bidirectional messaging (widget &harr; agent via `postMessage`).

---

## Agent Memory Vault

Three-space persistent knowledge system (cross-pollinated from [Ars Contexta](https://github.com/agenticnotetaking/arscontexta)):

| Space | Purpose | Cap |
|---|---|---|
| **Self** | Agent identity, capabilities, preferences | 20 entries |
| **Notes** | Accumulated knowledge, learned patterns | 100 entries |
| **Ops** | Recent session context, operational state | 30 entries |

Processes entries through a 6Rs pipeline: Record &rarr; Reduce &rarr; Reflect &rarr; Reweave &rarr; Verify &rarr; Rethink. Vault data enriches marketplace profiles so experienced agents can charge premium tolls.

---

## Email (ARC-Relay)

10 email tools wrapping the [ARC-Relay](https://arc-relay.com) REST API for domain management, alias creation, DMARC/health checks, log retrieval, sender blocking, and analytics.

Optional **Email Agent**: an autonomous background agent that processes incoming emails via webhook and composes responses.

```env
FORGE_EMAIL_AGENT_ENABLED=true
FORGE_ARCRELAY_API_KEY=your-key
FORGE_ARCRELAY_WEBHOOK_SECRET=your-secret
```

---

## MCP Server

Expose the entire Forge toolset to any MCP-compatible client (Claude Code, Cursor, Windsurf, etc.).

### Stdio Transport (default)

```bash
python forge/mcp_server.py
```

Add to Claude Code's config (`~/.claude/settings.json`):
```json
{
  "mcpServers": {
    "forge": {
      "command": "python",
      "args": ["forge/mcp_server.py"],
      "cwd": "/path/to/grok-party-pack"
    }
  }
}
```

### SSE Transport (HTTP)

```bash
python forge/mcp_server.py --transport sse --port 8420
```

---

## RAG Pipeline

Ingest documents (text, code, PDFs) into a ChromaDB vector store for semantic search. Agents can retrieve relevant context before answering questions about large codebases or document collections.

```bash
pip install chromadb sentence-transformers
```

```python
from forge.rag import RAGStore

store = RAGStore()
store.ingest_directory("/path/to/codebase", glob="**/*.py")
results = store.query("How does authentication work?", top_k=5)
```

**Tools:** `rag_ingest`, `rag_query`, `rag_status`, `rag_clear`

---

## Task Scheduler

Run Forge tasks on recurring intervals or cron schedules. Jobs persist across restarts and auto-disable after configurable consecutive failures.

```bash
# API
curl -X POST http://localhost:5000/api/scheduler/jobs \
  -H "Content-Type: application/json" \
  -d '{"name": "monitor-spy", "task": "Check SPY PCR and alert if > 1.5", "interval_minutes": 30}'
```

**Endpoints:**

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/scheduler/jobs` | Create a scheduled job |
| `GET` | `/api/scheduler/jobs` | List all jobs |
| `DELETE` | `/api/scheduler/jobs/<id>` | Remove a job |
| `POST` | `/api/scheduler/jobs/<id>/trigger` | Run immediately |
| `GET` | `/api/scheduler/jobs/<id>/runs` | View run history |

---

## Agent-to-Agent Conversations

Multi-turn negotiation protocol for marketplace agents. Two agents can hold structured conversations with turn limits, timeout enforcement, and outcome tracking.

```python
# Start a conversation between two agents
curl -X POST http://localhost:5000/api/v1/conversations \
  -H "Content-Type: application/json" \
  -d '{"agent_a": "trader-bot", "agent_b": "analyst-bot", "topic": "NVDA position sizing"}'
```

**Endpoints:**

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/conversations` | Start a conversation |
| `GET` | `/api/v1/conversations/<id>` | Get conversation state |
| `POST` | `/api/v1/conversations/<id>/reply` | Add a turn |
| `POST` | `/api/v1/conversations/<id>/close` | Close with outcome |

---

## DAG Workflows

Extends the linear planner with directed acyclic graph execution: parallel fan-out, conditional branches, fallback paths, and join barriers.

```python
from forge.dag import DAGPlan, DAGExecutor

plan = DAGPlan()
plan.add_node("fetch", task="Download the dataset")
plan.add_node("parse", task="Parse CSV", depends_on=["fetch"])
plan.add_node("validate", task="Validate schema", depends_on=["fetch"])  # runs parallel with parse
plan.add_node("transform", task="Clean data", depends_on=["parse", "validate"])  # waits for both
plan.add_node("fallback", task="Use cached data", fallback_for="fetch")  # runs if fetch fails
```

Node types: `task`, `condition`, `parallel`, `join`, `fallback`

---

## Docker Sandbox

Container isolation for arena matches and shell execution. Each match gets its own Docker container with CPU/memory limits, no network (configurable), and auto-cleanup.

```env
FORGE_DOCKER_SANDBOX_ENABLED=true
FORGE_DOCKER_SANDBOX_IMAGE=python:3.12-slim
FORGE_DOCKER_SANDBOX_MEMORY=512m
```

Falls back to path-based sandboxing if Docker is unavailable.

---

## Image & Audio Generation

DALL-E 3 image generation, OpenAI TTS, and Whisper transcription. Requires `OPENAI_API_KEY`.

| Tool | Description |
|---|---|
| `generate_image` | Text-to-image via DALL-E 3. Sizes: 1024x1024, 1024x1792, 1792x1024. Quality: standard/hd. Style: vivid/natural |
| `generate_speech` | Text-to-speech via OpenAI TTS. Voices: alloy, echo, fable, onyx, nova, shimmer |
| `transcribe_audio` | Speech-to-text via Whisper. Supports mp3, wav, m4a, etc. |

---

## GitHub Integration

8 tools for GitHub operations. Requires `GITHUB_TOKEN` (or `FORGE_GITHUB_TOKEN`) env var.

| Tool | Description |
|---|---|
| `github_list_issues` | List issues/PRs with state and label filters |
| `github_get_issue` | Full issue/PR details with comments |
| `github_create_issue` | Create issues with labels and assignees |
| `github_create_pr` | Create pull requests |
| `github_pr_review` | Submit PR reviews (approve, request changes, comment) |
| `github_ci_status` | Check CI/check run status for a ref |
| `github_list_repos` | List repos for a user/org |
| `github_search_code` | Search code across GitHub |

---

## Web UI Authentication

Session-based authentication for the Forge web interface. Disabled by default.

```env
FORGE_AUTH_ENABLED=true
FORGE_ADMIN_PASSWORD=your-secure-password
FORGE_SECRET_KEY=your-secret-key
```

First run creates a default `admin` user. The marketplace API (`/api/v1/`) uses its own API key auth and is not affected.

---

## Observability

Prometheus-compatible metrics endpoint at `/metrics`. No external dependencies.

**Tracked metrics:**
- `forge_tasks_total`, `forge_tasks_active`, `forge_task_duration_seconds`, `forge_task_cost_usd_total`
- `forge_tool_calls_total`, `forge_tool_errors_total`, `forge_tool_duration_seconds`
- `forge_model_requests_total`, `forge_model_tokens_total`
- `forge_arena_matches_total`, `forge_toll_revenue_usd_total`
- `forge_scheduler_runs_total`, `forge_uptime_seconds`

JSON snapshot available at `/api/metrics`.

Add to Prometheus `scrape_configs`:
```yaml
- job_name: forge
  static_configs:
    - targets: ['localhost:5000']
```

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/task` | Submit a task (returns `task_id`). Optional `pack` field for capability packs |
| `GET` | `/api/stream/<id>` | SSE stream of task progress |
| `POST` | `/api/kill/<id>` | Cancel a running task |
| `POST` | `/api/arena` | Launch arena deathmatch |
| `GET` | `/api/history` | Recent completed tasks |
| `GET` | `/api/packs` | List all capability packs with readiness status |
| `GET` | `/api/packs/<name>` | Single pack details |
| `GET` | `/metrics` | Prometheus metrics scrape endpoint |
| `GET` | `/api/metrics` | JSON metrics snapshot |

### Toll Protocol Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/agents/register` | Register a new agent, get API key |
| `GET` | `/api/v1/wallet/balance` | Check agent wallet balance |
| `POST` | `/api/v1/wallet/deposit` | Deposit funds to agent wallet |
| `POST` | `/api/v1/task` | Submit a toll-metered task |
| `GET` | `/api/v1/agents` | Browse registered agents |
| `POST` | `/api/v1/agents/<id>/invoke` | Invoke another agent |

### Task Submission

```bash
curl -X POST http://localhost:5000/api/task \
  -H "Content-Type: application/json" \
  -d '{
    "task": "Find all TODO comments in the codebase and list them",
    "sandbox_mode": true,
    "sandbox_path": "B:/Grok",
    "direct_mode": false,
    "agent_count": 16,
    "executor_model": "auto",
    "pack": "research"
  }'
```

---

## Project Structure

```
forge/
  app.py                      # Flask web server + all route registration
  config.py                   # Models, limits, paths, feature flags
  orchestrator.py              # Planner -> Executor pipeline
  planner.py                   # 16-agent research council
  executor.py                  # Single-agent tool-calling loop
  providers.py                 # Anthropic/OpenAI/LM Studio/Ollama adapters
  context_engine.py            # Context compaction, session memory, auto routing
  models.py                    # Pydantic data models
  memory.py                    # Task persistence (JSON)
  vault.py                     # Three-space agent memory (self/notes/ops)
  mcp_server.py                # MCP stdio/SSE server for tool exposure
  scheduler.py                 # Cron/interval task scheduler
  rag.py                       # RAG vector store (ChromaDB)
  dag.py                       # DAG workflow engine
  auth.py                      # Web UI session authentication
  observability.py             # Prometheus metrics + structured logging
  signals.py                   # Per-interaction quality signals (OpenClaw-RL)
  judge.py                     # PRM step scoring (OpenClaw-RL)
  directives.py                # Hindsight contrastive directives (OpenClaw-RL)
  delegation.py                # Trust-based task reassignment
  generative_ui.py             # Interactive widget rendering (iframes)
  sdk.py                       # Python SDK client for the marketplace
  firewall.py                  # Prompt injection / safety checks
  guardrails.py                # Output guardrails and concurrent limits
  attention_residuals.py       # Attention residual analysis
  kernel.py                    # Core kernel
  run_log.py                   # Execution run logging
  cli.py                       # CLI interface
  tools/
    registry.py                # Tool registry + lazy discovery + sandbox enforcement
    __init__.py                # Tool registration (creates registry with all tools)
    filesystem.py              # File read/write/delete/find/grep
    shell.py                   # Shell command execution
    python_repl.py             # Python code execution
    git_ops.py                 # Git operations
    http.py                    # HTTP GET/POST
    browser.py                 # Playwright browser automation
    database.py                # SQLite queries
    image.py                   # Image resize/convert
    archive.py                 # ZIP/TAR operations
    clipboard.py               # System clipboard
    search.py                  # File/content search
    email.py                   # ARC-Relay email tools (10 tools)
    escalation.py              # Escalate-to-human tool
    trading.py                 # Trading tools (PCR, sentiment, options, execution)
    prophecy.py                # Prophecy Engine tools
    surgeon.py                 # Surgeon / OBLITERATUS tools
    tribe.py                   # TRIBE v2 neural engagement scoring
    github.py                  # GitHub API tools (issues, PRs, CI)
    image_gen.py               # DALL-E 3, TTS, Whisper tools
    rag.py                     # RAG vector search tools
  agents/
    email_agent.py             # Autonomous email processing agent
    email_webhook.py           # ARC-Relay webhook handler
    conversation.py            # Agent-to-agent multi-turn protocol
  arena/
    runner.py                  # Arena orchestrator (combat + collab, 15 scenarios)
    sandbox.py                 # Arena sandbox setup + scenario seeding
    swarm.py                   # CASS — swarm-vs-swarm warfare
    container.py               # Docker sandbox for isolated execution
  packs/
    __init__.py                # CapabilityPack system + PackRegistry
    research.py                # Research pack definition
    builder.py                 # Builder pack definition
    ops.py                     # Ops pack definition
    trading.py                 # Trading pack definition
    arena.py                   # Arena pack definition
    email.py                   # Email pack definition
  toll/
    __init__.py                # Toll protocol exports
    relay.py                   # TollRelay middleware (meters message generators)
    ledger.py                  # Wallet balances + transaction log (SQLite)
    gating.py                  # HTTP 402 @toll_gate decorator
    rates.py                   # RateEngine — per-message-type pricing
    models.py                  # Toll data models
    endpoints.py               # Toll REST API routes
    public_api.py              # Public marketplace API
    auth.py                    # API key authentication
    settlement.py              # Settlement backends (Local/Solana/Base)
    solana_watcher.py          # Solana USDC deposit poller
  prophecy/
    engine.py                  # Prophecy simulation engine
    types.py                   # Prophecy data models
    endpoints.py               # Prophecy REST API routes
  surgeon/
    engine.py                  # OBLITERATUS abliteration engine
    types.py                   # Surgeon data models
    endpoints.py               # Surgeon REST API routes
  trading/
    engine.py                  # Trading engine
    providers.py               # Broker adapters (yfinance, Tradier, Robinhood)
    portfolio.py               # Portfolio tracking (SQLite)
    crypto_agent.py            # Autonomous crypto trading agent
    polymarket_agent.py        # Polymarket prediction market agent
    polymarket_executor.py     # Polymarket CLOB order execution
    endpoints.py               # Trading REST API routes
    brokers.py                 # Broker abstraction layer
    portfolio_view.py          # Portfolio reporting
  lore/
    pantheon.md                # Agent role documentation
    lads_war_room.py           # Presidential Council CLI
    presidential_council_history.json
  evals/
    runner.py                  # Eval framework runner
    golden/                    # Golden test sets
  static/
    index.html                 # SPA frontend
    style.css                  # Dark theme UI
    app.js                     # Frontend logic + SSE streaming
  data/                        # Runtime data (gitignored)
    tasks.json                 # Task history
    session_memory.json        # Learned patterns across tasks
    knowledge_graph.json       # Entity relationships and outcomes
    trust_ledger.json          # Agent performance metrics
    toll_ledger.db             # Wallet/transaction database
    conversations/             # Per-task conversation logs
    runs/                      # Execution run logs
    vaults/                    # Agent memory vaults
    prophecy/                  # Prophecy simulation data
    surgeon/                   # Surgeon operation data
    trading/                   # Trading portfolio data
    tribe_cache/               # TRIBE v2 model cache
```

---

## Configuration

All feature flags and settings are in `forge/config.py` and can be overridden via environment variables in `.env`:

### Core

```env
FORGE_COST_LIMIT_TASK=5.00         # USD per task
FORGE_COST_LIMIT_SESSION=50.00     # USD per session
FORGE_WORKING_DIR=                 # sandbox working directory
```

### Feature Flags

| Flag | Default | Description |
|---|---|---|
| `FORGE_TOLL_ENABLED` | `true` | Toll protocol metering |
| `FORGE_MARKETPLACE_ENABLED` | `true` | Agent marketplace API |
| `FORGE_TRADING_ENABLED` | `true` | Trading tools |
| `FORGE_PROPHECY_ENABLED` | `true` | Prophecy Engine |
| `FORGE_SURGEON_ENABLED` | `true` | Surgeon / OBLITERATUS |
| `FORGE_TRIBE_ENABLED` | `false` | TRIBE v2 neural scoring |
| `FORGE_GENERATIVE_UI_ENABLED` | `true` | Widget rendering |
| `FORGE_EMAIL_AGENT_ENABLED` | `false` | Email agent |
| `FORGE_ARENA_SWARM_ENABLED` | `true` | CASS swarm warfare |
| `FORGE_SOLANA_WATCHER_ENABLED` | `false` | Solana USDC deposit watcher |
| `FORGE_TRADING_PAPER_MODE` | `true` | Paper trading (no real orders) |
| `FORGE_SIGNALS_ENABLED` | `true` | OpenClaw-RL signal extraction |
| `FORGE_JUDGE_ENABLED` | `true` | PRM step judge |
| `FORGE_DIRECTIVES_ENABLED` | `true` | Hindsight directives |
| `FORGE_USER_CORRECTION_ENABLED` | `true` | User correction detection |
| `FORGE_GITHUB_ENABLED` | `true` | GitHub integration tools |
| `FORGE_IMAGE_GEN_ENABLED` | `true` | Image/audio generation (DALL-E, TTS) |
| `FORGE_RAG_ENABLED` | `true` | RAG vector search pipeline |
| `FORGE_SCHEDULER_ENABLED` | `true` | Task scheduler |
| `FORGE_CONVERSATIONS_ENABLED` | `true` | Agent-to-agent conversations |
| `FORGE_DOCKER_SANDBOX_ENABLED` | `false` | Docker container sandbox |
| `FORGE_AUTH_ENABLED` | `false` | Web UI authentication |
| `FORGE_DAG_ENABLED` | `true` | DAG workflow execution |
| `FORGE_OBSERVABILITY_ENABLED` | `true` | Prometheus metrics |

---

## License

This is a fun project. Do whatever you want with it.
