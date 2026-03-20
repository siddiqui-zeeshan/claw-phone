# spare-paw — Complete Specification

## Overview

A 24/7 personal AI agent accessible through Telegram (or a lightweight HTTP webhook). Features configurable multi-model routing via OpenRouter, automation tools, scheduled tasks, voice transcription, and full-text search over conversation history.

Runs on macOS, Linux, Windows, Docker, and Android/Termux. The same codebase adapts its defaults, tool descriptions, and prompt files to the detected platform at startup.

**Language:** Python 3.11+
**Interface:** Telegram (owner-only) or HTTP webhook
**LLM backend:** OpenRouter API (any model)

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Backend                                │
│  Telegram: python-telegram-bot · owner-only auth          │
│  Webhook:  aiohttp HTTP server · Bearer token auth        │
│  Voice messages → Groq Whisper transcription              │
│  Message queue with backpressure + "thinking..." indicator│
└──────────────┬──────────────────────┬──────────────────┘
               │ user messages        │ cron results
               ▼                      ▲
┌──────────────────────────┐  ┌───────────────────────────┐
│   Context Manager         │  │    Cron Scheduler          │
│  (SQLite · sliding window │  │  (APScheduler · SQLite     │
│   · FTS5 full-text search │  │   persisted · per-cron     │
│   · token budget)         │  │   model · semaphore-gated) │
└──────────┬───────────────┘  └───────────┬───────────────┘
           │ assembled context             │ prompt + model
           ▼                               ▼
┌─────────────────────────────────────────────────────────┐
│                   Model Router                            │
│  (OpenRouter API · model slots · retry with backoff       │
│   · asyncio.Semaphore to serialize concurrent calls)      │
└──────────────────────┬────────────────────────────────┘
                       │ tool calls
                       ▼
┌─────────────────────────────────────────────────────────┐
│               Tools (ProcessPoolExecutor)                  │
│  shell · files · tavily_search · web_scrape · cron_mgr    │
│  Blocking tools (shell, web_scrape) run in process pool   │
└─────────────────────────────────────────────────────────┘
```

### Concurrency Model

The application uses a **single async event loop** with a **ProcessPoolExecutor** for CPU/IO-bound tool operations:

- **asyncio event loop** — drives the backend (Telegram or webhook), cron scheduling, and model API calls
- **ProcessPoolExecutor** (workers = 4) — shell commands and web scraping run in separate processes to prevent event loop starvation
- **asyncio.Semaphore** (permits = 1) — serializes model router calls so user messages and cron executions don't race on shared API state
- **Message queue** — when the bot is busy processing a message, incoming messages are queued and processed sequentially. A "thinking..." chat action is sent as backpressure signal
- **Health heartbeat** — main loop touches a heartbeat file every 30s; watchdog checks file freshness, not just process liveness

---

## Components

### 1. Platform Detection

**Module:** `spare_paw.platform`

Detects the runtime environment and provides platform-appropriate defaults used by config loading, tool registration, and the setup wizard.

**Functions:**
- `detect_platform()` — returns `'termux'`, `'mac'`, `'linux'`, or `'windows'`
  - Termux: detected via presence of `/data/data/com.termux`
  - macOS: `sys.platform == 'darwin'`
  - Windows: `sys.platform == 'win32'`
  - Linux: fallback
- `platform_label()` — human-readable string for prompts, e.g. `"Android (Termux)"`, `"macOS"`, `"Linux"`, `"Windows"`
- `default_allowed_paths()` — list of paths the files tool is allowed to access by default
  - Termux: `["/sdcard", "/data/data/com.termux/files/home"]`
  - all others: `[str(Path.home())]`
- `default_shell_description()` — tool description string for the shell tool, mentioning platform-specific utilities (termux-api commands on Termux, brew/osascript on macOS, PowerShell on Windows)
- `default_shell_executable()` — returns `["bash"]`, or `["sh"]` / `["cmd.exe", "/c"]` as fallbacks on Windows when bash is unavailable

### 2. Backend

spare-paw supports two message backends, selected via `backend:` in config.yaml. Both implement the same `MessageBackend` protocol (`spare_paw.backend`).

#### 2a. Telegram Backend (`spare_paw.bot.backend.TelegramBackend`)

**Library:** python-telegram-bot (async)
**Auth:** Owner-only via `owner_id` in config. All messages from non-owner are silently ignored.

**Commands:**
- `/cron list` — list all crons with ID, name, schedule, next run, model, status
- `/cron remove <id>` — delete a cron
- `/cron pause <id>` — pause without deleting
- `/cron resume <id>` — resume paused cron
- `/cron info <id>` — details + last run result + recent failures
- `/config show` — show current runtime config
- `/config model <name>` — override default model for this session
- `/config reset` — reset overrides to config.yaml defaults
- `/status` — uptime, memory usage, DB size, active crons, last error
- `/search <query>` — full-text search over conversation history via FTS5
- `/forget` — start a new conversation (old one stays in DB, just starts fresh context)
- `/model <name>` — shortcut for `/config model <name>`

**Regular messages:** Stored in DB → sliding window context assembled → model router → response stored → sent back.

**Voice messages:** Downloaded → sent to Groq Whisper for transcription → transcribed text processed as a normal message. If Groq is not configured, reply with "Voice messages require Groq API key."

**Message backpressure:** When the bot is already processing a message, new messages are queued. A typing indicator (`chat_action = typing`) is sent immediately to signal the bot is busy. Messages are processed sequentially in FIFO order.

**Cron result delivery:**
- Fire-and-forget: result appears as a normal message from the bot
- On failure: always notify with error details (warning prefix)
- Cron outputs do NOT enter conversation memory
- If user replies to a cron result, the original cron output is included as one-off context for that turn only

#### 2b. Webhook Backend (`spare_paw.webhook.backend.WebhookBackend`)

Minimal HTTP server backed by aiohttp. Intended for headless deployments (Docker, CI, custom integrations) where Telegram is not available or desired.

**Endpoints:**
- `POST /message` — submit an incoming message. Accepts JSON with `text`, `image` (base64), and/or `voice` (base64) fields.
- `GET /poll?timeout=30` — long-poll for outgoing messages. Returns a JSON list of response objects queued since the last poll.
- `GET /health` — returns `{"status": "ok"}`

**Auth:** Optional Bearer token via `webhook.secret` in config. If `secret` is empty, auth is skipped.

**Activation:** Set `backend: webhook` in config.yaml. Configure `webhook.port` (default `8080`).

### 3. Model Router

**Backend:** OpenRouter API (https://openrouter.ai/api/v1/chat/completions)

**Model slots (in config.yaml):**
- `default` — used for normal chat (e.g., `google/gemini-2.0-flash`)
- `smart` — used when user says `/model smart` or for complex tasks (e.g., `anthropic/claude-sonnet-4`)
- `cron_default` — default model for cron jobs (e.g., `google/gemini-2.0-flash`)

**Per-cron model:** Each cron can specify a model. Falls back to `cron_default`, then `default`.

**Tool-use:** Implements OpenAI-compatible function calling format via OpenRouter. Tools are registered as JSON schemas. Model responses with `tool_calls` are executed and results fed back in a loop until the model produces a final text response.

**Max tool iterations:** Configurable (default 20) to prevent runaway loops.

**Retry with exponential backoff:** All OpenRouter API calls use exponential backoff (base 1s, max 30s, 3 retries) for transient failures (429, 500, 502, 503, 504). Non-retryable errors (400, 401, 403) fail immediately.

**Serialization:** An `asyncio.Semaphore(1)` ensures only one model call runs at a time, preventing races between user messages and concurrent cron executions.

### 4. Context Manager (Sliding Window)

A simple, reliable context strategy that stores all messages and assembles a sliding window for each model call. Designed as a clean interface so LCM can replace it later without touching other components.

**Database:** SQLite at `~/.spare-paw/spare-paw.db`

#### Schema

```sql
-- Schema version tracking
PRAGMA user_version = 1;

-- Every message ever sent or received, verbatim
CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,  -- 'user', 'assistant', 'system', 'tool'
    content TEXT NOT NULL,
    token_count INTEGER NOT NULL,
    created_at TEXT NOT NULL,  -- ISO timestamp
    metadata TEXT  -- JSON: tool_call_id, tool_calls, model used, etc.
);

CREATE INDEX idx_messages_conversation ON messages(conversation_id, created_at);

-- Conversations
CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    last_message_at TEXT,
    metadata TEXT  -- JSON
);

-- Cron jobs
CREATE TABLE cron_jobs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    schedule TEXT NOT NULL,  -- cron expression
    prompt TEXT NOT NULL,
    model TEXT,  -- NULL = use cron_default
    tools_allowed TEXT,  -- JSON list of tool names, NULL = all
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_run_at TEXT,
    last_result TEXT,
    last_error TEXT,
    metadata TEXT  -- JSON
);

-- FTS5 for full-text search with proper sync triggers
CREATE VIRTUAL TABLE messages_fts USING fts5(content, content=messages, content_rowid=rowid);

CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
END;
CREATE TRIGGER messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.rowid, old.content);
END;
CREATE TRIGGER messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.rowid, old.content);
    INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
END;
```

#### Context Assembly (each turn)

1. Fetch the last `max_messages` (default 64) messages for the active conversation, ordered by `created_at`
2. Walk backwards from newest, accumulating token counts
3. Stop when `token_budget * safety_margin` is exceeded — drop remaining oldest messages
4. Prepend system prompt (with `{current_time}` interpolated)
5. Return as OpenAI-format message list

**Token counting:** tiktoken with `cl100k_base` encoding as a rough estimator. A configurable `safety_margin` (default 0.85) accounts for tokenizer drift across non-OpenAI models.

#### Configuration

```yaml
context:
  max_messages: 64        # sliding window size
  token_budget: 120000    # max tokens for context
  safety_margin: 0.85     # budget multiplier for tokenizer safety
```

#### Future: LCM Replacement

The context manager exposes a simple interface:
- `ingest(conversation_id, role, content, metadata)` → stores message
- `assemble(conversation_id)` → returns message list for model
- `search(query)` → FTS5 search

LCM can replace this module by implementing the same interface with DAG-based summarization, compaction, and deep recall. No other components need to change.

### 5. Tools

All tools are exposed to the LLM as callable functions (OpenAI function-calling format). Blocking tools run in a `ProcessPoolExecutor` to avoid starving the event loop. Tool descriptions are resolved at registration time using `spare_paw.platform` so the LLM receives platform-appropriate guidance.

#### shell
Execute a shell command on the host system.
- Parameters: `command` (string), `timeout` (int, default 30s)
- Returns: stdout, stderr, exit code
- Safety: configurable timeout, output truncated at 10K chars
- **Description:** platform-aware — mentions termux-api commands on Termux, brew/osascript on macOS, PowerShell on Windows, generic on Linux
- **Shell executable:** `bash` by default; falls back to `sh` or `cmd.exe /c` on Windows without bash
- **Execution:** Runs in ProcessPoolExecutor

#### files
Read, write, list files on the host filesystem.
- Actions: `read`, `write`, `append`, `list`, `delete`, `exists`
- Parameters: `path`, `content` (for write/append)
- Safety: restricted to `allowed_paths` in config (path traversal prevention via `os.path.realpath`)
- **Default allowed paths:** platform-aware (home directory on macOS/Linux/Windows; `/sdcard` and Termux home on Android)
- Returns: file content or directory listing or success/error

#### tavily_search
Search the web using Tavily Search API.
- Parameters: `query` (string), `count` (int, default 5)
- Returns: list of {title, url, description}
- Requires: Tavily API key
- If not configured: returns error suggesting configuration

#### web_scrape
Fetch and extract content from a specific URL.
- Parameters: `url` (string), `selector` (optional CSS selector)
- Returns: extracted text content (truncated at 20K chars)
- Uses: aiohttp + BeautifulSoup
- Timeout: 15s
- **Execution:** Runs in ProcessPoolExecutor (BeautifulSoup parsing is CPU-bound)

#### cron_create
Create a new scheduled task.
- Parameters: `name`, `schedule` (cron expression), `prompt`, `model` (optional), `tools_allowed` (optional list)
- Returns: cron ID and next run time
- Called by the LLM when user describes a scheduled task in natural language

#### cron_delete
Delete a scheduled task.
- Parameters: `cron_id`
- Returns: success/error

#### cron_list
List all scheduled tasks.
- Returns: list of {id, name, schedule, next_run, model, enabled, last_run_at}

### 6. Cron Scheduler

**Library:** APScheduler (AsyncIOScheduler)
**Persistence:** SQLite (cron_jobs table in spare-paw.db)
**Startup:** Loads all enabled crons from DB and schedules them.

**Execution flow:**
1. Cron fires at scheduled time
2. Scheduler acquires the model semaphore
3. Calls model router with the cron's prompt + allowed tools + specified model
4. Model executes (may call tools in a loop)
5. Final text response sent to owner via the active backend
6. On failure: send error notification with warning prefix
7. Update `last_run_at`, `last_result`, `last_error` in DB
8. Release semaphore

**Cron results are NOT stored in conversation memory.**
**If user replies to a cron result:** include original cron output as one-off context for that turn.

### 7. Voice Message Support

**Library:** Groq API (Whisper)
**Flow:**
1. User sends voice note (Telegram only)
2. Bot downloads the .ogg file
3. Sends to Groq Whisper endpoint for transcription
4. Transcribed text is processed as a normal message
5. Bot replies with transcription prefix, then the response

**If Groq not configured:** Reply with "Voice messages require a Groq API key in config."

### 8. Process Management

**Logging:**
- Python `logging` module
- File handler with rotation (10MB max, 3 backups)
- Log location: `~/.spare-paw/logs/`

**Health heartbeat:**
- Main event loop touches `~/.spare-paw/heartbeat` every 30s
- Watchdog checks: if heartbeat file is older than 90s, kill and restart
- Catches event loop starvation and deadlocks, not just crashes

**Health command:**
- `/status` command shows: uptime, RAM usage, DB size, active crons, last error, current model config

**Graceful shutdown:**
- On SIGINT/SIGTERM: stops backend, drains scheduler, closes MCP connections, shuts down process pool, closes database
- On Windows: signal handlers use `signal.signal` (loop-level handlers are not available on Windows)

**Termux-specific:**
- `termux-wake-lock` to prevent Android from killing the process
- Watchdog script (`scripts/watchdog.sh`) monitors heartbeat file freshness and restarts on stall

### 9. Setup / Onboarding

**Command:** `spare-paw setup` (or `python -m spare_paw setup`)

Interactive wizard that:
1. Detects the current platform (`detect_platform()`)
2. Creates `~/.spare-paw/` directory structure (including `logs/`, `skills/`, `custom_tools/`)
3. Copies platform-appropriate default prompt files:
   - `IDENTITY.md` — `IDENTITY.termux.md` (ClawPhone persona) for Termux; `IDENTITY.md` (SparePaw persona) for all other platforms
   - `SYSTEM.md` — `SYSTEM.md` for Termux, `SYSTEM.mac.md` for macOS, `SYSTEM.linux.md` for Linux
   - `USER.md` — shared across all platforms
4. Generates `config.yaml` from template with platform-aware `allowed_paths` and system prompt `Device:` label
5. Prompts for required API keys (OpenRouter, Telegram bot token, Telegram owner ID)
6. Prompts for optional API keys (Tavily, Groq)
7. Initializes SQLite database
8. Prints next steps (platform-aware: includes watchdog hint on Termux)

---

## Config File

Location: `~/.spare-paw/config.yaml`

Config defaults are computed dynamically at startup using the detected platform (via `spare_paw.platform`). The values below reflect a generic Linux/macOS deployment; Termux deployments get Android-specific paths and tool descriptions.

```yaml
# Backend selection: "telegram" (default) or "webhook"
backend: "telegram"

telegram:
  bot_token: "YOUR_TELEGRAM_BOT_TOKEN"
  owner_id: 123456789

# Only required when backend: webhook
webhook:
  port: 8080
  secret: ""  # optional Bearer token for auth

openrouter:
  api_key: "YOUR_OPENROUTER_API_KEY"

models:
  default: "google/gemini-2.0-flash"
  smart: "anthropic/claude-sonnet-4"
  cron_default: "google/gemini-2.0-flash"

tavily:
  api_key: ""  # optional, for web search

groq:
  api_key: ""  # optional, for voice transcription

context:
  max_messages: 64        # sliding window size
  token_budget: 120000    # max tokens for context
  safety_margin: 0.85     # budget multiplier for tokenizer safety

tools:
  shell:
    enabled: true
    timeout_seconds: 30
    max_output_chars: 10000
  files:
    enabled: true
    allowed_paths:
      - "~"            # platform default: home dir (or Termux paths on Android)
  web_search:
    enabled: true
    max_results: 5
  web_scrape:
    enabled: true
    timeout_seconds: 15
    max_content_chars: 20000
  cron:
    enabled: true

agent:
  max_tool_iterations: 20
  system_prompt: |
    You are a personal AI assistant running 24/7.
    You have access to the local filesystem, shell, web search, and web scraping.
    You can manage scheduled tasks (crons) for the user.
    Be concise.
    Current time: {current_time}
    Device: macOS        # replaced by platform_label() at startup

mcp:
  servers: []            # list of MCP server configs

logging:
  level: "INFO"
  max_bytes: 10485760  # 10MB
  backup_count: 3
```

---

## Project Structure

```
spare-paw/
├── pyproject.toml       # [project.scripts] spare-paw = spare_paw.__main__:main
├── Dockerfile
├── SPEC.md
├── defaults/
│   ├── IDENTITY.md          # Generic SparePaw persona (macOS, Linux, Windows, Docker)
│   ├── IDENTITY.termux.md   # ClawPhone persona for Android/Termux
│   ├── SYSTEM.md            # Termux system context (device capabilities, root)
│   ├── SYSTEM.mac.md        # macOS system context
│   ├── SYSTEM.linux.md      # Linux system context
│   └── USER.md              # User preferences (shared)
├── scripts/
│   ├── watchdog.sh          # Heartbeat-aware restart script (Termux/Linux)
│   └── install-termux.sh    # Termux dependency installer
├── src/
│   └── spare_paw/
│       ├── __init__.py
│       ├── __main__.py      # Entry point: setup / gateway
│       ├── platform.py      # Platform detection and platform-aware defaults
│       ├── backend.py       # MessageBackend protocol + IncomingMessage
│       ├── config.py        # Config loading (YAML + runtime overrides, platform-aware defaults)
│       ├── db.py            # SQLite connection, schema, migrations
│       ├── context.py       # Sliding window context assembly + FTS5 search
│       ├── gateway.py       # Main async loop: backend + scheduler + heartbeat
│       ├── setup_wizard.py  # Interactive onboarding wizard (platform-aware)
│       │
│       ├── bot/
│       │   ├── __init__.py
│       │   ├── backend.py   # TelegramBackend (implements MessageBackend)
│       │   ├── handler.py   # Message handler with queue + backpressure
│       │   ├── commands.py  # /cron, /config, /status, /search, /forget, /model
│       │   └── voice.py     # Voice message transcription (Groq Whisper)
│       │
│       ├── webhook/
│       │   ├── __init__.py
│       │   └── backend.py   # WebhookBackend: HTTP POST/poll (implements MessageBackend)
│       │
│       ├── router/
│       │   ├── __init__.py
│       │   ├── openrouter.py  # OpenRouter API client with retry/backoff
│       │   └── tool_loop.py   # Tool-use execution loop
│       │
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── registry.py    # Tool registration & JSON schema generation
│       │   ├── shell.py       # Shell command execution (platform-aware description)
│       │   ├── files.py       # File read/write/list (path-restricted, platform-aware defaults)
│       │   ├── tavily_search.py # Tavily Search API
│       │   ├── web_scrape.py  # URL fetching + BeautifulSoup (ProcessPoolExecutor)
│       │   └── cron_tools.py  # cron_create, cron_delete, cron_list
│       │
│       ├── core/
│       │   ├── __init__.py
│       │   ├── engine.py      # process_message — backend-agnostic message handling
│       │   ├── prompt.py      # System prompt assembly
│       │   ├── commands.py    # Command dispatch (shared across backends)
│       │   └── voice.py       # Voice transcription (Groq Whisper)
│       │
│       ├── cron/
│       │   ├── __init__.py
│       │   ├── scheduler.py   # APScheduler setup & job management
│       │   └── executor.py    # Cron job execution (semaphore-gated)
│       │
│       ├── mcp/
│       │   ├── __init__.py
│       │   ├── client.py      # MCP client manager
│       │   └── schema.py      # MCP schema utilities
│       │
│       └── util/
│           ├── __init__.py
│           └── redact.py      # Secret redaction for logging
│
└── config.example.yaml
```

---

## Installation

### macOS / Linux

```bash
git clone <repo>
cd spare-paw
pip install -e .

# Setup (interactive wizard — detects platform automatically)
spare-paw setup

# Run
spare-paw gateway
```

### Windows

```bash
git clone <repo>
cd spare-paw
pip install -e .

spare-paw setup
spare-paw gateway
```

Shell commands default to `bash` if available (e.g. Git Bash, WSL), falling back to `sh` then `cmd.exe /c`.

### Docker

```bash
docker build -t spare-paw .

# Mount config dir so credentials persist across restarts
docker run -d \
  -v $HOME/.spare-paw:/root/.spare-paw \
  -p 8080:8080 \
  spare-paw
```

The Dockerfile sets `ENTRYPOINT ["python", "-m", "spare_paw", "gateway"]` and exposes port 8080. Set `backend: webhook` in config to use the HTTP interface instead of Telegram.

### Termux (Android)

```bash
# Install base packages
pkg update && pkg upgrade
pkg install python python-pip git

# Install spare-paw
git clone <repo>
cd spare-paw
pip install --break-system-packages -e .

# Setup (interactive wizard)
spare-paw setup

# Run
spare-paw gateway

# With watchdog (recommended)
bash scripts/watchdog.sh
```

---

## Dependencies

```
python-telegram-bot>=21.0        # Telegram bot (async)
aiohttp>=3.9                     # Async HTTP (OpenRouter, Brave, Groq, webhook server)
apscheduler>=3.10,<4             # Cron scheduling
beautifulsoup4>=4.12             # Web scraping (HTML parsing)
tiktoken>=0.7                    # Token counting (approximate)
pyyaml>=6.0                      # Config file parsing
aiosqlite>=0.20                  # Async SQLite
mcp>=1.26.0                      # MCP client protocol
```

---

## Key Design Decisions

1. **Platform-independent by default** — `spare_paw.platform` provides a single detection point used by config defaults, tool descriptions, and the setup wizard. Adding a new platform means updating one module.

2. **Two backends, one engine** — `TelegramBackend` and `WebhookBackend` both implement the `MessageBackend` protocol. The core engine (`spare_paw.core.engine`) is backend-agnostic; no Telegram-specific code leaks into processing logic.

3. **Python over Node.js** — Better cross-platform support for SQLite, stronger ecosystem for web scraping and Telegram bots, native async.

4. **OpenRouter API** — Any model, no CLI overhead, configurable per-slot and per-cron.

5. **Sliding window context (v1) with LCM interface** — Ship fast with simple last-N-messages context. The context manager exposes `ingest/assemble/search` — LCM replaces this module later without touching bot, router, or tools.

6. **ProcessPoolExecutor for blocking tools** — Shell commands and web scraping run in separate processes. The async event loop stays responsive even during long-running tool calls.

7. **Semaphore-serialized model calls** — Prevents races between user messages and concurrent cron executions hitting the model API simultaneously. One model call at a time.

8. **Message queue with backpressure** — Incoming messages queue while the bot is processing. Typing indicator shows the bot is busy. No message loss, no cascading delays.

9. **Heartbeat-based watchdog** — Watchdog checks file freshness (not just PID). Catches event loop starvation and deadlocks that a simple process monitor would miss.

10. **FTS5 with sync triggers** — Full-text search stays current via AFTER INSERT/DELETE/UPDATE triggers. No stale index.

11. **Exponential backoff on all external APIs** — All HTTP calls (OpenRouter, Tavily, Groq) retry with backoff for transient errors.

12. **Crons separate from conversation** — Cron outputs don't pollute context. Replies to cron results get one-off context inclusion.

13. **Schema versioning** — `PRAGMA user_version` tracks schema version. Future migrations check version on startup and apply incremental changes.

14. **Token safety margin** — tiktoken estimates are approximate for non-OpenAI models. An 0.85 multiplier on the budget prevents context overflows from tokenizer drift.

15. **Platform-specific identity** — On Termux, the default persona is ClawPhone (phone-aware, references battery, camera, etc.). On all other platforms, the default persona is SparePaw (generic). Both are user-editable via `~/.spare-paw/IDENTITY.md`.

16. **Windows signal compatibility** — `loop.add_signal_handler` is not available on Windows. The gateway uses `sys.platform` to select between asyncio-level and stdlib signal handlers at startup.
