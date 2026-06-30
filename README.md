# Costwise

**Intelligent model routing, graph-guided context budgeting, and provider arbitrage for AI coding agents.**

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://python.org)
[![Tests](https://img.shields.io/badge/Tests-256_passing-green.svg)](tests/)

---

## The Problem

AI coding agents like Claude Code burn through $50-200/day when every request hits a frontier model. But most requests don't *need* Opus — simple file reads, formatting questions, and boilerplate generation work fine on cheaper models. Existing optimization tools focus on input tokens, but **output costs are 2-5x more expensive per token** and go unaddressed.

## The Solution

Costwise is a local proxy that sits between your coding agent and the LLM API. It classifies each request by complexity, routes to the cheapest model that can handle it, and prunes irrelevant context — all transparently, with no changes to your workflow.

```
┌─────────────┐     ┌──────────────────────────────────┐     ┌──────────────┐
│ Claude Code  │────▶│         Costwise Proxy            │────▶│ Anthropic    │
│ (or any      │     │                                    │     │ OpenAI       │
│  LLM client) │◀────│  Classify → Route → Prune → Track  │◀────│ Google       │
└─────────────┘     └──────────────────────────────────┘     └──────────────┘
```

When combined with the full optimization stack (RTK + Ponytail + Costwise + Headroom), projected savings reach **95-97%** of baseline Opus-only costs.

## Features

- **3-tier complexity classification** — rule-based signal analysis (tools, code, errors, conversation depth, graph complexity) maps each request to SIMPLE / MEDIUM / COMPLEX
- **Multi-provider arbitrage** — routes to the cheapest model across Anthropic, OpenAI, and Google that matches the required tier and capabilities
- **Graph-guided context pruning** — uses your codebase's dependency graph (via Graphify) to score file relevance and prune low-value context
- **Budget enforcement** — hourly and session spend limits with automatic downgrade when approaching thresholds
- **Circuit breaker health tracking** — monitors provider latency and error rates, excludes unhealthy providers from routing
- **Quality feedback loop** — detects retry patterns that indicate false downgrades, auto-tunes classification thresholds
- **Real-time dashboard** — HTMX-powered dashboard with 6 panels: costs, savings, model distribution, budget, requests, feedback
- **MCP server** — 5 tools for direct integration with Claude Code
- **CLI** — `proxy`, `dashboard`, `gain`, `doctor`, `wrap`, `mcp` commands
- **Zero-config start** — sensible defaults, works out of the box with just `costwise proxy`

## Quick Start

### Install

```bash
pip install costwise[proxy]
# or with all features:
pip install costwise[all]
```

### Start the proxy

```bash
costwise proxy
# Proxy starts on 127.0.0.1:8788
```

### Point Claude Code at it

```bash
costwise wrap claude
# Auto-configures ~/.claude/settings.json with proxy URL + MCP server
```

Or manually set the environment variable:
```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8788
```

### Check your savings

```bash
costwise gain
# ╭─ Costwise Gain ──────────────────╮
# │  Requests:  1,247                │
# │  Tokens:    2.3M in / 890K out   │
# │  Cost:      $12.34               │
# │  Saved:     $87.66 (87.6%)       │
# │  Period:    2026-06-28 – 06-30   │
# ╰──────────────────────────────────╯
```

## How It Works

Every request flows through this pipeline:

```
Request → Signal Extraction → Complexity Classification → Budget Check
    → Provider Arbitrage → (optional) Context Pruning → Upstream API
    → Response → Tracking → (async) Feedback Detection → Auto-Tuning
```

### Signal Extraction

The classifier examines each request for 9 weighted signals:

| Signal | Weight | What it detects |
|--------|--------|----------------|
| Error context | 0.18 | Stack traces, error messages → needs smart model |
| Retry context | 0.18 | Follow-up to a failed attempt → upgrade |
| Code + tools | 0.15 | Code editing with tool use → at least MEDIUM |
| Graph complexity | 0.15 | High-centrality files → harder task |
| Tools | 0.12 | Tool calls present → more capable model |
| Code blocks | 0.12 | Code in context → generation/editing task |
| Token count | 0.10 | Large context → complex task |
| Conversation depth | 0.08 | Deep conversation → ongoing complex work |
| Images | 0.07 | Vision tasks → capable model required |

### Tier Mapping

The weighted score maps to three tiers:

| Score | Tier | Example Models |
|-------|------|---------------|
| < 0.20 | SIMPLE | Haiku 4.5, GPT-4.1-mini, Gemini 2.5 Flash |
| 0.20 – 0.55 | MEDIUM | Sonnet 4.6, GPT-4.1, Gemini 2.5 Pro |
| > 0.55 | COMPLEX | Opus 4.7, GPT-5, Gemini 2.5 Pro |

### Auto-Tuning

The feedback loop watches for retry patterns — when a cheaper model produces a response that gets immediately retried, that's a **false downgrade**. The tuner nudges the classification thresholds to reduce these over time, targeting a < 3% false downgrade rate.

## Dashboard

Start the dashboard to monitor cost optimization in real time:

```bash
costwise dashboard
# Opens at http://127.0.0.1:8789
```

**6 panels**, auto-refreshing via HTMX:

| Panel | Shows |
|-------|-------|
| Costs | Hourly spend chart (SVG) |
| Savings | Cumulative savings over time |
| Models | Distribution across models (pie chart) |
| Budget | Current spend vs. limits, warnings |
| Requests | Recent routing decisions with tier/model/cost |
| Feedback | Retry rate, false downgrade rate, quality grade (A-F) |

## MCP Tools

Register Costwise as an MCP server in Claude Code for direct access to routing and stats:

```bash
costwise wrap claude --mcp
```

| Tool | Description |
|------|-------------|
| `costwise_route` | Classify a prompt and get the recommended model with cost estimate |
| `costwise_budget` | Graph-guided context budget — rank files by relevance, suggest what to prune |
| `costwise_stats` | Session cost, savings, and model distribution |
| `costwise_gain` | Cumulative savings summary across all optimization layers |
| `costwise_feedback` | Routing quality metrics — retry rate, false downgrades, quality grade |

## CLI Commands

| Command | Description |
|---------|-------------|
| `costwise proxy` | Start the routing proxy (default: 127.0.0.1:8788) |
| `costwise dashboard` | Start the cost dashboard (default: 127.0.0.1:8789) |
| `costwise gain` | Show token usage and cost savings summary |
| `costwise doctor` | Health checks for all integration points (9 checks) |
| `costwise wrap claude` | Auto-configure Claude Code to use proxy + MCP |
| `costwise mcp` | Start the MCP server (stdio transport) |

## Configuration

Copy the example config and customize:

```bash
cp costwise.example.toml costwise.toml
```

Costwise looks for config in this order:
1. `./costwise.toml`
2. `~/.config/costwise/costwise.toml`
3. Built-in defaults

See [`costwise.example.toml`](costwise.example.toml) for all options with comments.

### Key Config Sections

```toml
[costwise.proxy]          # Host, port, upstream URL, timeout
[costwise.routing]        # Thresholds, enabled providers, confidence
[costwise.budget]         # Hourly/session limits, auto-downgrade
[costwise.tracking]       # SQLite DB path, retention
[costwise.graph]          # Graph path, relevance threshold, BFS hops
[costwise.feedback]       # Auto-tune, nudge step, target rates
[costwise.integrations]   # RTK, Ponytail, Headroom, Graphify toggles
[[costwise.providers]]    # Multi-provider config (name, API base, key)
```

## Integration with the Optimization Stack

Costwise is the routing layer in a 4-tool optimization stack:

```
┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│   RTK    │   │ Ponytail │   │ Costwise │   │ Headroom │
│ (input)  │   │ (output) │   │ (routing)│   │ (compress│
│          │   │          │   │          │   │          │
│ Filters  │   │ Shapes   │   │ Routes   │   │ Compresses│
│ CLI      │   │ output   │   │ requests │   │ messages │
│ output   │   │ behavior │   │ to cheap │   │ before   │
│ before   │   │ to reduce│   │ models   │   │ they hit │
│ context  │   │ tokens   │   │          │   │ the API  │
└──────────┘   └──────────┘   └──────────┘   └──────────┘
```

| Tool | What it optimizes | Enable in Costwise |
|------|-------------------|--------------------|
| **RTK** | Input tokens — filters CLI output (git log, test runs) before it enters context | `costwise.integrations.rtk_enabled = true` |
| **Ponytail** | Output tokens — shapes LLM behavior to produce shorter responses | `costwise.integrations.ponytail_enabled = true` |
| **Costwise** | Routing — sends requests to the cheapest adequate model | Core (always on) |
| **Headroom** | Compression — token-level message compression before API call | `costwise.integrations.headroom_enabled = true` |
| **Graphify** | Context — provides the code dependency graph for relevance scoring | `costwise.integrations.graphify_mcp = true` |

## Architecture

```
src/costwise/
├── core/          # Models, classifier, router, arbitrage, pricing, budget, health
├── proxy/         # FastAPI proxy server, request translator
├── graph/         # Code graph loader, BFS relevance scorer, context pruner, cache
├── feedback/      # Retry detector, fingerprinting, metrics, auto-tuner
├── dashboard/     # HTMX app, SVG chart generator, data queries
├── mcp/           # MCP server (5 tools, stdio transport)
├── integrations/  # RTK, Ponytail, Headroom, Graphify, LiteLLM adapters
├── tracking/      # SQLite store, schema, async queries
├── config/        # TOML loader, Pydantic schema
└── cli/           # Click CLI (proxy, dashboard, gain, doctor, wrap, mcp)
```

**Data flow:** Request → `proxy/server.py` → `core/signals.py` (extract) → `core/classifier.py` (tier) → `core/budget.py` (check limits) → `core/arbitrage.py` (cheapest model) → `core/health.py` (provider ok?) → upstream API → `tracking/store.py` (record) → `feedback/detector.py` (async, check for retries)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing, and PR guidelines.

## License

[Apache 2.0](LICENSE)
