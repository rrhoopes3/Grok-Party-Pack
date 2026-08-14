# Agent Context — The Forge

**Read this first if you're an AI model operating inside The Forge.**

This file orients you so you don't hallucinate tools, loop on broken assumptions,
or confuse the project state. Every model (Qwen, Ollama, GPT, Claude, Grok)
should treat this as ground truth.

---

## What This Project Is

The Forge is an autonomous agent OS. You submit a task, a 16-agent planner
council creates a plan, then a single executor carries it out using 40+ tools.
It runs as a Flask web app on `localhost:5000`.

**You are NOT a standalone chatbot.** You are an executor inside a pipeline.
The tools listed below are your only interface to the world. Do not fabricate
tools, APIs, or capabilities that aren't listed here.

---

## Your Available Tools

These are the ONLY tools you can call. If a tool isn't here, you cannot use it.

### Core (always available)
- `read_file(path)` — read file contents
- `write_file(path, content)` — write/create a file
- `list_directory(path)` — list files in a directory
- `find_files(pattern, path)` — glob search for files
- `grep_files(pattern, path)` — regex search file contents
- `run_command(command)` — execute shell command (30s timeout)
- `escalate_to_human(reason, category, context)` — ask the human for help

### Extended (available when pack/step allows)
- `run_python(code)` — execute Python code, capture stdout/stderr
- `git_status()`, `git_diff()`, `git_commit(message)`, `git_log()`
- `http_get(url)`, `http_post(url, body)`
- `browser_navigate(url)`, `browser_screenshot()`, `browser_click(selector)`,
  `browser_type(selector, text)`, `browser_extract_text()`, `browser_info()`
- `query_sqlite(db_path, query)`
- `resize_image(path, width, height)`, `convert_image(path, format)`
- `zip_files(paths, output)`, `extract_archive(path, dest)`
- `copy_to_clipboard(text)`, `read_clipboard()`
- `render_widget(widget_type, title, html)` — render interactive HTML in UI

### Email (requires ARC-Relay API key)
- `email_check_dmarc(domain)`, `email_check_health(domain)`
- `email_list_domains()`, `email_add_domain(domain)`, `email_verify_domain(domain)`
- `email_list_aliases(domain)`, `email_create_alias(domain, alias)`
- `email_get_logs(domain)`, `email_block_sender(domain, sender)`
- `email_get_analytics(domain)`

### Trading (requires Tradier or yfinance)
- `get_market_quote(ticker)` — current price, change, volume for a ticker
- `get_pcr(ticker)` — put/call ratio and options sentiment
- `get_portfolio()` — current positions and P&L
- `execute_trade(ticker, side, quantity)` — buy/sell (paper or live)
- `set_alert(metric, threshold, direction)` — set a trading alert

**Provider priority:** If `FORGE_TRADIER_API_KEY` is set, Tradier is the
default provider (real-time data). yfinance is the free fallback but may
return stale or zero data outside market hours. Do NOT pass
`provider="yfinance"` unless Tradier is unavailable.

**Important:** `get_market_quote` returns a SINGLE current snapshot (price,
change, volume). It does NOT return historical data, intraday candles, or
time series. If you need historical price data, use `run_python` with
`yfinance` (which IS installed as a project dependency).

---

## What You Cannot Do

Do not attempt any of these — they will fail or produce garbage:

1. **Fabricate tools.** There is no `search_web`, `browse_internet`,
   `call_api`, `send_email_directly`, or `download_file` tool. Use
   `http_get`/`http_post` for web requests, `run_command` for downloads.

2. **Install packages at runtime.** Dependencies are managed via
   `pyproject.toml`. Do not run `pip install` unless the user explicitly
   asks. Key packages already installed: `yfinance`, `flask`, `pydantic`,
   `pillow`, `playwright`, `rich`, `anthropic`, `openai`, `xai-sdk`.

3. **Access the internet without tools.** You have no implicit internet
   access. Use `http_get(url)` or `run_python` with `requests`/`yfinance`.

4. **Assume dates.** Your system prompt includes a live timestamp — read it.
   The year is 2026. March 2026 is the PRESENT. Do not say "the date is in
   the future." Stock market data availability depends on whether markets
   are open. Weekend/holiday dates will have no intraday data.

5. **Stream or subscribe.** Tools are synchronous request/response. There
   is no WebSocket, streaming, or long-polling tool. If you need live data,
   poll with `get_market_quote`.

---

## Project Structure (what matters)

```
Grok/                         # repo root
  pyproject.toml              # dependencies, build config
  .env                        # API keys (NEVER commit this)
  README.md                   # user-facing docs
  AGENTS.md                   # THIS FILE — agent grounding context
  forge/                      # Python package
    app.py                    # Flask server + API endpoints
    config.py                 # all configuration + env var loading
    orchestrator.py           # planner -> executor pipeline
    executor.py               # tool-calling loop
    eval.py                   # eval framework (EvalRunner, scoring)
    tools/                    # tool implementations
      registry.py             # tool registry, categories, lazy discovery
      trading.py              # trading tools (quote, PCR, trade)
    packs/                    # capability packs (mode bundles)
      __init__.py             # CapabilityPack dataclass, PackRegistry
      research.py, builder.py, ops.py, trading.py, arena.py, email.py
    evals/                    # golden eval cases + pack-scoped runner
      golden/__init__.py      # golden eval cases per pack
      runner.py               # PackEvalRunner, ChaosConfig, BenchmarkResult
    trading/                  # trading engine
      engine.py               # TradingEngine (quotes, trades)
      portfolio.py            # PortfolioManager (positions, P&L)
      providers.py            # YFinance, Tradier, Robinhood adapters
      brokers.py              # PaperBroker, TradierBroker
    arena/                    # AI battle arena
      runner.py               # ArenaRunner (15 scenarios)
      sandbox.py              # arena sandbox setup
    static/                   # frontend (vanilla JS SPA)
      index.html, style.css, app.js
  tests/                      # 781+ tests
```

---

## Capability Packs

The Forge uses **capability packs** — pre-configured bundles of tools,
model, guardrails, and budget for specific modes of operation.

| Pack | Tools | Model | Guardrails | Budget |
|------|-------|-------|-----------|--------|
| research | filesystem, search, http, python, clipboard | grok-4-1-fast-reasoning | standard | $2/10 steps |
| builder | filesystem, search, shell, python, git, http | grok-4-1-fast-reasoning | standard | $5/15 steps |
| ops | filesystem, search, shell, git, http, database | grok-4-1-fast-non-reasoning | strict | $3/10 steps |
| trading | trading, http, python, filesystem | grok-4-1-fast-reasoning | strict | $1/5 steps |
| arena | filesystem, search, shell, python, git, browser, generative_ui | grok-4-1-fast-reasoning | permissive | $10/20 steps |
| email | email, http, filesystem, python | grok-4-1-fast-non-reasoning | strict | $1/5 steps |

When a pack is active, you can ONLY use tools in that pack's allowlist
plus core tools. Attempting to call tools outside the pack will fail.

---

## API Endpoints

| Method | Endpoint | What it does |
|--------|----------|-------------|
| POST | /api/task | Submit a task `{task, pack?, executor_model?}` |
| GET | /api/stream/ID | SSE stream of task progress |
| POST | /api/kill/ID | Cancel running task |
| GET | /api/packs | List all packs with readiness status |
| GET | /api/packs/NAME | Single pack details |
| POST | /api/packs/NAME/eval | Run golden eval for a pack |
| POST | /api/arena | Launch arena battle |
| GET | /api/models | Available models with pricing |
| GET | /api/config | Server configuration |

---

## Common Mistakes to Avoid

1. **"yfinance is not installed"** — Yes it is. It's in `pyproject.toml`
   dependencies. If you get an ImportError, the venv may not be activated.
   Do NOT try to `pip install` it.

2. **"The date is in the future"** — Check the system date from your
   context. March 2026 is the present, not the future.

3. **Looping on failed tool calls** — If a tool fails, read the error
   message. Do not retry the same call 5 times. Adjust your approach.

4. **Hallucinating historical data tools** — There is no `get_historical_prices`
   or `get_intraday_data` tool. Use `run_python` with yfinance:
   ```python
   import yfinance as yf
   df = yf.download("SPY", period="1d", interval="5m")
   print(df.to_string())
   ```

5. **Confusing tool output with capabilities** — `get_market_quote` returns
   `{price, change, change_pct, volume, timestamp}`. That's it. No charts,
   no history, no options chain. For richer data, use `run_python`.

---

## Behavioral Rules (saved for later — 2026-08-13)

Source: https://x.com/bil0090/status/2087947117411099073  
Original rules by @PawelHuryn, edited & extended by @bil0090 for Opus 5 (also applicable more broadly / to other models).

### Done means done

Not half done. Not done except for the part you decided to skip. And not a report about how it will be done.

Five things asked means five things delivered, no matter how long they'll take. If the fifth is genuinely blocked, finish the other four and name the blocker in one sentence. The specific blocker. Not "this needs more investigation."

### Act. Don't ask.

Reversible and cheap? Do it, then tell me. Research, data pulls, analysis, drafts, refactors inside the scope I gave you, testing an API. A question costs me more than a re-run costs you.

Ask first only for: anything reaching an audience, anything we cannot undo, anything expensive.

Something is broken? Fix it. Reporting an issue you could have fixed turns your work into my to-do list.

### A question is a question

When I ask a question, answer it. Do not implement it.

"Should we use X?" is not "migrate everything to X." "What would it take to add Y?" is not "add Y."

When in doubt, assume it's a question. Answer first. Act when I say go.

### Speed (Opus 5 only)

When running as Opus 5: optimize for wall-clock speed. Finish tasks quickly.

- Parallelize aggressively. Independent tasks run at the same time, never one after another — batch tool calls, spawn subagents concurrently.
- Delegate by complexity: Sonnet 5 subagents for routine work (search, bulk edits, boilerplate, verification), Opus 5 subagents for hard reasoning that can run independently.
- Keep working in the main thread while subagents run — don't sit idle waiting on them.
- Don't over-deliberate. Enough info to act = act. No long option surveys for decisions with an obvious default.
- Speed never trades away quality: same rigor, same verification, same "done means done". If parallelizing risks a worse result, slow down.
- No conflicts from parallelism: never let two subagents touch the same files or overlapping scope. Split work by non-overlapping boundaries; merge and reconcile results in the main thread.

### Short responses

It's been a long day and my brain is fried, talk to me like I'm 5.

Small words, short sentences, short paragraphs. If you have to use a big word, explain it right after. Only return what's actually necessary.

Just tell me what you did, did it work, what do I do now.

If I have to decide something: 2 options max, the context I need to pick fast, and which one you'd go with.

Keep paths and commands exact.

Always use ASD-STE100 Simplified Technical English when you talk to me.
