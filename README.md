# Costwise

**Intelligent model routing, graph-guided context budgeting, and provider arbitrage for AI coding agents.**

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://python.org)
[![Tests](https://img.shields.io/badge/Tests-415_passing-green.svg)](tests/)

---

## The Problem

AI coding agents like Claude Code burn through $50-200/day when every request hits a frontier model. But most requests don't *need* Opus — simple file reads, formatting questions, and boilerplate generation work fine on cheaper models. Existing optimization tools focus on input tokens, but **output costs are 2-5x more expensive per token** and go unaddressed.

## The Solution

Costwise is a local proxy that sits between your coding agent and the LLM API. It classifies each request by complexity, routes to the cheapest model that can handle it, and prunes irrelevant context — all transparently, with no changes to your workflow.

```
┌─────────────┐     ┌──────────────────────────────────┐     ┌──────────────┐
│ Claude Code  │────>│         Costwise Proxy            │────>│ Anthropic    │
│ (or any      │     │                                    │     │ OpenAI       │
│  LLM client) │<────│  Classify -> Route -> Prune -> Track│<────│ Google       │
└─────────────┘     └──────────────────────────────────┘     └──────────────┘
```

When combined with the full optimization stack (RTK + Ponytail + Costwise + Headroom), projected savings reach **95-97%** of baseline Opus-only costs.

## Features

- **3-tier complexity classification** — 16 signals (structural + semantic) with 11 adaptive weights map each request to SIMPLE / MEDIUM / COMPLEX
- **Expected cost optimization** — accounts for retry risk when selecting models, not just base price
- **Semantic signal enrichment** — intent detection (8 categories), graduated error severity, multi-file scope detection
- **Soft tier boundaries** — borderline cases compare expected cost across adjacent tiers instead of hard cutoffs
- **Adaptive weight learning** — signal weights auto-adjust based on which signals actually predict retries
- **Multi-provider arbitrage** — routes to the cheapest model across Anthropic, OpenAI, and Google that matches the required tier and capabilities
- **Graph-guided context pruning** — uses your codebase's dependency graph (via Graphify) to score file relevance and prune low-value context
- **Budget enforcement** — hourly and session spend limits with automatic downgrade when approaching thresholds
- **Circuit breaker health tracking** — monitors provider latency and error rates, excludes unhealthy providers from routing
- **Quality feedback loop** — detects retry patterns that indicate false downgrades, auto-tunes classification thresholds and signal weights
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
# Auto-configures ~/.claude/settings.json with proxy URL + MCP server + Ponytail hooks
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
Request
  -> Fingerprint + Retry Detection
  -> Signal Extraction (16 signals)
  -> Complexity Classification (11 adaptive weights)
  -> Borderline Resolution (cost-compare adjacent tiers)
  -> Budget Check (hourly/session limits)
  -> Expected Cost Optimization (retry-risk-aware model selection)
  -> Provider Arbitrage (cheapest healthy model)
  -> Context Pruning (graph-guided, optional)
  -> Headroom Compression (optional)
  -> Forward to Provider
  -> Track (SQLite: decision + signal snapshot)
  -> Feedback Loop (retry detection, threshold tuning, weight learning)
```

### Signal Extraction

The classifier examines each request for 16 signals across two categories:

**Structural signals** (original foundation):

| Signal | What it detects |
|--------|----------------|
| `has_tools` / `tool_count` | Tool definitions in the request |
| `token_count` | Total tokens across all messages |
| `has_code` / `code_block_count` | Code blocks in context |
| `conversation_depth` | Number of messages (ongoing complex work) |
| `has_error_context` | Error keywords anywhere |
| `has_retry_context` | Retry-related keywords |
| `image_count` | Vision content requiring capable models |
| `graph_complexity` | File centrality from code dependency graph |

**Semantic signals** (enriched):

| Signal | What it detects |
|--------|----------------|
| `intent` | Task intent: generate, refactor, explain, fix, debug, test, review, chat |
| `error_severity` | Graduated: 0.0=none, 0.3=warning, 0.6=runtime error, 1.0=critical/crash |
| `multi_file_scope` | References multiple distinct file paths |
| `referenced_file_count` | How many files are referenced |

### Classification Weights

The 11 weights are applied to signal scores and summed to produce a complexity score (0.0-1.0):

| Weight | Default | Signal | Notes |
|--------|---------|--------|-------|
| `w_error` | 0.14 | Error severity | Graduated, not binary |
| `w_retry` | 0.14 | Retry context | Previous model failed |
| `w_code_tools_compound` | 0.12 | Code + tools together | Code editing work |
| `w_intent` | 0.10 | Intent complexity | "chat"=0.0 ... "refactor"=0.7 |
| `w_tools` | 0.09 | Tool presence | |
| `w_code` | 0.09 | Code blocks | |
| `w_graph_complexity` | 0.09 | Graph centrality | High-centrality files = harder |
| `w_token_count` | 0.07 | Token count | |
| `w_multi_file` | 0.06 | Multi-file scope | Cross-file work = complex |
| `w_depth` | 0.05 | Conversation depth | |
| `w_images` | 0.05 | Image content | |

These weights are **adaptive** — the weight learner adjusts them within +/-30% of defaults based on which signals actually predict retries in your usage.

### Tier Mapping

| Score | Tier | Example Models |
|-------|------|---------------|
| < 0.20 | SIMPLE | Haiku 4.5, GPT-4.1-mini, Gemini 2.5 Flash Lite |
| 0.20 - 0.55 | MEDIUM | Sonnet 4.6, GPT-4.1, Gemini 2.5 Flash |
| >= 0.55 | COMPLEX | Opus 4.7, GPT-5, Gemini 2.5 Pro |

### Expected Cost Optimization

Rather than picking the cheapest model in a tier, Costwise minimizes **expected total cost** including retry risk:

```
expected_cost(model) = base_cost + P(retry) * (base_cost + cheapest_upgrade_cost)
```

A $0.10/MTok model with 15% retry probability costs more in expectation than a $0.30/MTok model with 2% retry probability — because retries waste the original attempt plus require a more expensive model.

### Borderline Handling

Requests scoring within +/-0.05 of a tier threshold get special treatment: instead of a hard cutoff, Costwise compares the expected total cost for both adjacent tiers and picks whichever is cheaper. This prevents oscillation at boundaries.

### Adaptive Weight Learning

Every request's signal values are stored alongside its routing outcome. Periodically (hourly, after 100+ requests), the weight learner computes correlations: `mean(signal | retry) - mean(signal | no retry)`. Signals that predict retries get their weight increased; signals that anti-predict retries get decreased. Bounded to +/-30% of defaults to prevent drift.

### Auto-Tuning

The feedback loop watches for retry patterns — when a cheaper model produces a response that gets immediately retried, that's a **false downgrade**. The tuner nudges the classification thresholds to reduce these over time, targeting a < 3% false downgrade rate.

### Model Pricing

11 models across 3 providers (prices in USD per million tokens):

| Model | Tier | Input | Output | Provider |
|-------|------|-------|--------|----------|
| claude-opus-4-7 | COMPLEX | $5.00 | $25.00 | Anthropic |
| claude-sonnet-4-6 | MEDIUM | $3.00 | $15.00 | Anthropic |
| claude-haiku-4-5 | SIMPLE | $1.00 | $5.00 | Anthropic |
| gpt-5 | COMPLEX | $1.25 | $10.00 | OpenAI |
| gpt-4.1 | MEDIUM | $2.00 | $8.00 | OpenAI |
| gpt-4.1-mini | SIMPLE | $0.40 | $1.60 | OpenAI |
| gpt-4.1-nano | SIMPLE | $0.10 | $0.40 | OpenAI |
| gemini-2.5-pro | COMPLEX | $1.25 | $10.00 | Google |
| gemini-2.5-flash | MEDIUM | $0.30 | $2.50 | Google |
| gemini-2.5-flash-lite | SIMPLE | $0.10 | $0.40 | Google |

The cheapest SIMPLE model is **250x cheaper** per output token than Opus.

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
| `costwise wrap claude` | Auto-configure Claude Code to use proxy + MCP + Ponytail |
| `costwise mcp` | Start the MCP server (stdio transport) |
| `costwise setup` | First-time setup wizard |

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
[costwise.proxy.vertex]   # Vertex AI: project_id, region (auto-detects env vars)
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
├── core/          # Models, classifier, router, arbitrage, pricing, budget, health, expected cost
├── proxy/         # FastAPI proxy server, request translator, Vertex AI adapter
├── graph/         # Code graph loader, BFS relevance scorer, context pruner, cache
├── feedback/      # Retry detector, fingerprinting, metrics, auto-tuner, adaptive weight learner
├── dashboard/     # HTMX app, SVG chart generator, data queries
├── mcp/           # MCP server (5 tools, stdio transport)
├── integrations/  # RTK, Ponytail, Headroom, Graphify, LiteLLM adapters
├── tracking/      # SQLite store (routing decisions, signal snapshots, retry events), schema
├── config/        # TOML loader, Pydantic schema
└── cli/           # Click CLI (proxy, dashboard, gain, doctor, wrap, mcp, setup)
```

**Data flow:**

```
Request
  -> proxy/server.py (intercept)
  -> core/signals.py (extract 16 signals)
  -> core/classifier.py (11 adaptive weights -> tier)
  -> core/budget.py (check limits)
  -> core/expected_cost.py (retry-risk-aware cost)
  -> core/arbitrage.py (cheapest healthy model)
  -> core/health.py (circuit breaker)
  -> graph/pruner.py (context pruning, optional)
  -> upstream API
  -> tracking/store.py (record decision + signal snapshot)
  -> feedback/detector.py (retry detection)
  -> feedback/tuner.py (threshold adjustment)
  -> feedback/weight_learner.py (adaptive weight adjustment)
```

### Database Schema

SQLite with 6 tables:

| Table | Purpose |
|-------|---------|
| `routing_decisions` | Every routing decision with model, tier, cost, latency |
| `signal_snapshots` | Signal values for each request (feeds weight learner) |
| `retry_events` | Detected retries with original/retry request linkage |
| `threshold_adjustments` | History of auto-tuned threshold changes |
| `provider_health` | Provider latency, errors, rate limits |
| `budget_alerts` | Budget warnings and actions taken |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing, and PR guidelines.

## License

[Apache 2.0](LICENSE)
