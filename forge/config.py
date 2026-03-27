import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# ── API Keys ───────────────────────────────────────────────────────────────
XAI_API_KEY = os.getenv("XAI_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
LMSTUDIO_BASE_URL = os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234/v1")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")

# ── Polymarket ────────────────────────────────────────────────────────────
POLYMARKET_RELAYER_API_KEY = os.getenv("POLYMARKET_RELAYER_API_KEY", "")
POLYMARKET_RELAYER_ADDRESS = os.getenv("POLYMARKET_RELAYER_ADDRESS", "")
POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")

# ── Models ──────────────────────────────────────────────────────────────────
PLANNER_MODEL = "grok-4.20-multi-agent-beta-0309"
EXECUTOR_MODEL = "grok-4.20-beta-0309-reasoning"
PLANNER_AGENT_COUNT = 16

# Available executor models with pricing (cost per 1M tokens)
# Format: id → {"label": str, "provider": str, "cost_in": float, "cost_out": float}
EXECUTOR_MODELS = {
    "auto": {
        "label": "Auto (smart routing)", "provider": "auto",
        "cost_in": 0, "cost_out": 0,  # varies by routed model
    },
    # xAI
    "grok-4-1-fast-reasoning": {
        "label": "Grok 4.1 Fast Reasoning", "provider": "xAI",
        "cost_in": 0.20, "cost_out": 0.50,
    },
    "grok-4.20-multi-agent-beta-0309": {
        "label": "Grok 4.20 Multi-Agent", "provider": "xAI",
        "cost_in": 2.00, "cost_out": 6.00,
    },
    "grok-4.20-beta-0309-reasoning": {
        "label": "Grok 4.20 Reasoning", "provider": "xAI",
        "cost_in": 2.00, "cost_out": 6.00,
    },
    "grok-4.20-beta-0309-non-reasoning": {
        "label": "Grok 4.20 Non-Reasoning", "provider": "xAI",
        "cost_in": 2.00, "cost_out": 6.00,
    },
    "grok-code-fast-1": {
        "label": "Grok Code Fast", "provider": "xAI",
        "cost_in": 0.20, "cost_out": 1.50,
    },
    "grok-4.20-multi-agent-experimental-beta-0304": {
        "label": "Grok 4.20 Multi (Legacy)", "provider": "xAI",
        "cost_in": 2.00, "cost_out": 6.00,
    },
    # Anthropic
    "claude-sonnet-4-20250514": {
        "label": "Claude Sonnet 4", "provider": "Anthropic",
        "cost_in": 3.00, "cost_out": 15.00,
    },
    "claude-opus-4-20250514": {
        "label": "Claude Opus 4", "provider": "Anthropic",
        "cost_in": 15.00, "cost_out": 75.00,
    },
    "claude-haiku-4-20250414": {
        "label": "Claude Haiku 4", "provider": "Anthropic",
        "cost_in": 0.80, "cost_out": 4.00,
    },
    # OpenAI
    "gpt-4o": {
        "label": "GPT-4o", "provider": "OpenAI",
        "cost_in": 2.50, "cost_out": 10.00,
    },
    "gpt-4o-mini": {
        "label": "GPT-4o Mini", "provider": "OpenAI",
        "cost_in": 0.15, "cost_out": 0.60,
    },
    "o3-mini": {
        "label": "o3-mini", "provider": "OpenAI",
        "cost_in": 1.10, "cost_out": 4.40,
    },
    # Local
    "lmstudio:default": {
        "label": "LM Studio (Local)", "provider": "Local",
        "cost_in": 0, "cost_out": 0,
    },
    "ollama:default": {
        "label": "Ollama (Local)", "provider": "Local",
        "cost_in": 0, "cost_out": 0,
    },
}

# Arena-only models (same pool)
ARENA_MODELS = EXECUTOR_MODELS

# ── Cost Limits ───────────────────────────────────────────────────────────
COST_LIMIT_PER_TASK = float(os.getenv("FORGE_COST_LIMIT_TASK", "5.00"))    # USD
COST_LIMIT_PER_SESSION = float(os.getenv("FORGE_COST_LIMIT_SESSION", "50.00"))  # USD

# ── Paths ───────────────────────────────────────────────────────────────────
FORGE_DIR = Path(__file__).resolve().parent
DATA_DIR = FORGE_DIR / "data"
TASKS_FILE = DATA_DIR / "tasks.json"
CONVERSATIONS_DIR = DATA_DIR / "conversations"
RUNS_DIR = DATA_DIR / "runs"

# Ensure data dirs exist
DATA_DIR.mkdir(exist_ok=True)
CONVERSATIONS_DIR.mkdir(exist_ok=True)
RUNS_DIR.mkdir(exist_ok=True)
VAULTS_DIR = DATA_DIR / "vaults"
VAULTS_DIR.mkdir(exist_ok=True)

# ── Limits ──────────────────────────────────────────────────────────────────
EXECUTOR_MAX_ITERATIONS = 15  # raised from 10 — context compaction prevents bloat
SHELL_TIMEOUT_SECONDS = 30
SHELL_WORKING_DIR = Path(os.getenv("FORGE_WORKING_DIR", str(Path(__file__).resolve().parent.parent)))  # defaults to repo root

# ── ARC-Relay ─────────────────────────────────────────────────────────────
ARCRELAY_API_URL = os.getenv("FORGE_ARCRELAY_URL", "https://arc-relay.com")
ARCRELAY_WEBHOOK_SECRET = os.getenv("FORGE_ARCRELAY_WEBHOOK_SECRET", "")
ARCRELAY_API_KEY = os.getenv("FORGE_ARCRELAY_API_KEY", "")

# ── Email Agent ──────────────────────────────────────────────────────────
EMAIL_AGENT_ENABLED = os.getenv("FORGE_EMAIL_AGENT_ENABLED", "false").lower() == "true"
EMAIL_AGENT_MODEL = os.getenv("FORGE_EMAIL_AGENT_MODEL", "grok-4-1-fast-non-reasoning")

# ── Toll Protocol ─────────────────────────────────────────────────────────
TOLL_ENABLED = os.getenv("FORGE_TOLL_ENABLED", "true").lower() == "true"
TOLL_DB_PATH = DATA_DIR / "toll_ledger.db"
TOLL_DEFAULT_BALANCE = float(os.getenv("FORGE_TOLL_DEFAULT_BALANCE", "10.0"))  # USD
TOLL_CREATOR_WALLET = os.getenv("FORGE_TOLL_CREATOR_WALLET", "creator")
TOLL_CREATOR_RAKE_PCT = float(os.getenv("FORGE_TOLL_CREATOR_RAKE", "30.0"))   # %

# ── Marketplace (Beat 3) ─────────────────────────────────────────────────
MARKETPLACE_ENABLED = os.getenv("FORGE_MARKETPLACE_ENABLED", "true").lower() == "true"

# ── Generative UI ────────────────────────────────────────────────────────
GENERATIVE_UI_ENABLED = os.getenv("FORGE_GENERATIVE_UI_ENABLED", "true").lower() == "true"
MARKETPLACE_DEFAULT_BALANCE = float(os.getenv("FORGE_MARKETPLACE_DEFAULT_BALANCE", "1.0"))  # USD for new agents
MARKETPLACE_TASK_ESTIMATE = float(os.getenv("FORGE_MARKETPLACE_TASK_ESTIMATE", "0.05"))     # USD per task estimate
MARKETPLACE_BASE_USDC_ADDRESS = os.getenv("FORGE_BASE_USDC_ADDRESS", "")    # Base L2 USDC receiver
MARKETPLACE_SOLANA_USDC_ADDRESS = os.getenv("FORGE_SOLANA_USDC_ADDRESS", "2RzBNDG52n7EhqSeUYksa5eyTb7YJ8b3xvyJLESzY6zf")  # Solana USDC receiver

# ── Solana Watcher (Beat 4) ──────────────────────────────────────────────
SOLANA_WATCHER_ENABLED = os.getenv("FORGE_SOLANA_WATCHER_ENABLED", "false").lower() == "true"
SOLANA_NETWORK = os.getenv("FORGE_SOLANA_NETWORK", "devnet")  # "devnet" | "mainnet-beta"
SOLANA_RPC_URL = os.getenv("FORGE_SOLANA_RPC_URL", "")  # custom RPC; empty = public endpoint
SOLANA_POLL_INTERVAL = int(os.getenv("FORGE_SOLANA_POLL_INTERVAL", "15"))  # seconds
SOLANA_USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # mainnet USDC mint
SOLANA_USDC_DECIMALS = 6

# ── Trading ─────────────────────────────────────────────────────────────────
TRADING_ENABLED = os.getenv("FORGE_TRADING_ENABLED", "true").lower() == "true"

# Tradier
TRADING_TRADIER_API_KEY = os.getenv("FORGE_TRADIER_API_KEY", "")
TRADING_TRADIER_ACCOUNT_ID = os.getenv("FORGE_TRADIER_ACCOUNT_ID", "")
TRADING_TRADIER_SANDBOX = os.getenv("FORGE_TRADIER_SANDBOX", "true").lower() == "true"

# Robinhood Legacy (robin_stocks — stocks, options, crypto)
TRADING_ROBINHOOD_USER = os.getenv("FORGE_ROBINHOOD_USER", "")
TRADING_ROBINHOOD_PASS = os.getenv("FORGE_ROBINHOOD_PASS", "")

# Robinhood Crypto API (API key — crypto only, no stocks/options)
TRADING_ROBINHOOD_API_KEY = os.getenv("FORGE_ROBINHOOD_API_KEY", "")
TRADING_ROBINHOOD_API_SECRET = os.getenv("FORGE_ROBINHOOD_API_SECRET", "")

# Polymarket CLOB (prediction markets)
POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", os.getenv("FORGE_POLYMARKET_PRIVATE_KEY", ""))
POLYMARKET_FUNDER_ADDRESS = os.getenv("POLYMARKET_RELAYER_ADDRESS", os.getenv("FORGE_POLYMARKET_FUNDER_ADDRESS", ""))
POLYMARKET_RELAYER_API_KEY = os.getenv("POLYMARKET_RELAYER_API_KEY", "")
POLYMARKET_SIGNATURE_TYPE = int(os.getenv("FORGE_POLYMARKET_SIGNATURE_TYPE", "1"))  # 1=Magic/email, 2=browser, 0=EOA

# Auto-detect best provider
_trading_provider_env = os.getenv("FORGE_TRADING_PROVIDER", "")
if _trading_provider_env:
    TRADING_DEFAULT_PROVIDER = _trading_provider_env
elif TRADING_ROBINHOOD_USER and TRADING_ROBINHOOD_PASS:
    TRADING_DEFAULT_PROVIDER = "robinhood"        # full: stocks + options + crypto
elif TRADING_ROBINHOOD_API_KEY:
    TRADING_DEFAULT_PROVIDER = "robinhood-crypto"  # crypto-only via API key
elif TRADING_TRADIER_API_KEY:
    TRADING_DEFAULT_PROVIDER = "tradier"
else:
    TRADING_DEFAULT_PROVIDER = "yfinance"

TRADING_DATA_DIR = DATA_DIR / "trading"
TRADING_DATA_DIR.mkdir(exist_ok=True)
TRADING_PAPER_MODE = os.getenv("FORGE_TRADING_PAPER_MODE", "true").lower() == "true"

# ── Prophecy Engine ────────────────────────────────────────────────────────
PROPHECY_ENABLED = os.getenv("FORGE_PROPHECY_ENABLED", "true").lower() == "true"
PROPHECY_DEFAULT_PROPHETS = int(os.getenv("FORGE_PROPHECY_DEFAULT_PROPHETS", "12"))
PROPHECY_DEFAULT_ROUNDS = int(os.getenv("FORGE_PROPHECY_DEFAULT_ROUNDS", "8"))
PROPHECY_MAX_PROPHETS = int(os.getenv("FORGE_PROPHECY_MAX_PROPHETS", "24"))
PROPHECY_MAX_ROUNDS = int(os.getenv("FORGE_PROPHECY_MAX_ROUNDS", "20"))
PROPHECY_DATA_DIR = DATA_DIR / "prophecy"
PROPHECY_DATA_DIR.mkdir(exist_ok=True)

# ── TRIBE v2 (Neural Engagement Scoring) ──────────────────────────────────
TRIBE_ENABLED = os.getenv("FORGE_TRIBE_ENABLED", "false").lower() == "true"
TRIBE_DEVICE = os.getenv("FORGE_TRIBE_DEVICE", "auto")
TRIBE_CACHE_DIR = DATA_DIR / "tribe_cache"
TRIBE_CACHE_DIR.mkdir(exist_ok=True)

# ── Surgeon (OBLITERATUS Integration) ─────────────────────────────────────
SURGEON_ENABLED = os.getenv("FORGE_SURGEON_ENABLED", "true").lower() == "true"
SURGEON_DATA_DIR = DATA_DIR / "surgeon"
SURGEON_DATA_DIR.mkdir(exist_ok=True)
SURGEON_MODELS_DIR = SURGEON_DATA_DIR / "models"
SURGEON_MODELS_DIR.mkdir(exist_ok=True)
SURGEON_DEFAULT_METHOD = os.getenv("FORGE_SURGEON_DEFAULT_METHOD", "advanced")
SURGEON_DEFAULT_DEVICE = os.getenv("FORGE_SURGEON_DEFAULT_DEVICE", "auto")
SURGEON_DEFAULT_DTYPE = os.getenv("FORGE_SURGEON_DEFAULT_DTYPE", "float16")

# ── Arena ──────────────────────────────────────────────────────────────────
ARENA_MASTER_MODEL = PLANNER_MODEL       # 16-agent Pantheon for commentary/judging
ARENA_DEFAULT_FIGHTER_MODEL = "grok-4.20-beta-0309-reasoning"
ARENA_FIGHTER_AGENT_COUNT = 4
ARENA_RECON_ITERATIONS = 3               # tool iterations for recon round
ARENA_FORGE_ITERATIONS = 5               # tool iterations for weapon forge round
ARENA_COMBAT_TURNS = 6                   # total turns in combat (3 per team)

# ── Arena: CASS (Colloidal Algorithmic Strife Simulator) ──────────────────
ARENA_SWARM_ENABLED = os.getenv("FORGE_ARENA_SWARM_ENABLED", "true").lower() == "true"

# ── OpenClaw-RL (arXiv:2603.10165) ───────────────────────────────────────
SIGNALS_ENABLED = os.getenv("FORGE_SIGNALS_ENABLED", "true").lower() == "true"
JUDGE_ENABLED = os.getenv("FORGE_JUDGE_ENABLED", "true").lower() == "true"
JUDGE_MODEL = os.getenv("FORGE_JUDGE_MODEL", "grok-4.20-beta-0309-reasoning")
JUDGE_TIMEOUT_SECONDS = float(os.getenv("FORGE_JUDGE_TIMEOUT", "30.0"))
DIRECTIVES_ENABLED = os.getenv("FORGE_DIRECTIVES_ENABLED", "true").lower() == "true"
USER_CORRECTION_ENABLED = os.getenv("FORGE_USER_CORRECTION_ENABLED", "true").lower() == "true"
CORRECTION_SIMILARITY_THRESHOLD = float(os.getenv("FORGE_CORRECTION_SIMILARITY", "0.6"))
