# Costwise: Architecture Separation — Core Routing vs. Companion Tools

## Context

Costwise is an intelligent model routing layer for AI coding agents. During development, four external open-source tools were integrated as companion optimizations. This document clarifies the separation between Costwise's core mission and these companion tools, to guide prioritization and planning.

## Core Mission

**Costwise's core job:** Classify task complexity → route to the cheapest adequate model → save money.

This is entirely self-contained within Costwise. No external tools are required.

### Core Routing Pipeline

```
Request arrives → Extract signals → Classify complexity (SIMPLE/MEDIUM/COMPLEX)
    → Select cheapest adequate model → Forward to provider
```

### Core Files (routing/classification)

| File | Purpose |
|------|---------|
| `src/costwise/core/signals.py` | Extracts signals from incoming requests (token count, keyword patterns, etc.) |
| `src/costwise/core/router.py` | Classifies complexity tier, selects model, applies budget constraints |
| `src/costwise/core/pricing.py` | Provider pricing data for cost comparison and arbitrage |
| `src/costwise/core/models.py` | Data models (SignalBundle, RoutingDecision, etc.) |
| `src/costwise/proxy/server.py` | FastAPI proxy that intercepts requests and applies routing |
| `src/costwise/config/schema.py` | Configuration schema (thresholds, providers, budget limits) |
| `src/costwise/tracking/store.py` | SQLite tracking of routing decisions and costs |

### Core Features (no external dependencies)

- **Complexity classification** — score-based tier assignment (simple/medium/complex)
- **Model routing** — map tiers to cheapest adequate model per provider
- **Provider arbitrage** — compare pricing across Anthropic, OpenAI, Google
- **Budget enforcement** — hourly/session spend limits with auto-downgrade
- **Quality feedback loop** — retry detection, auto-tuning of thresholds, false-downgrade tracking

---

## Companion Tools (separate concern)

These are **token-saving and observability layers**. They reduce cost by shrinking input/output before/after the LLM call, or by reporting on savings. They do NOT perform routing or classification.

### Tool-by-Role Matrix

| Tool | Does it route/classify? | Actual role | How Costwise uses it |
|------|------------------------|-------------|---------------------|
| **Graphify** | Minor — 0.15 weight signal in classifier | Context pruning (token saving) | Reads `graph.json` to score file relevance, prune low-value context before LLM call |
| **Headroom** | No | Message compression (token saving) | Compresses messages after pruning, before sending to provider (60-95% reduction) |
| **RTK** | No | Dashboard reporting (observability) | Reads RTK's SQLite DB (read-only) to show CLI-level savings in unified dashboard |
| **Ponytail** | Barely — adjusts output token estimate | Output reduction (token saving) | Reads Ponytail's config to lower estimated output tokens, slightly influencing tier selection |

### Key Insight

**Costwise routes just fine without any of these tools installed.** All four integrations degrade gracefully — if missing, Costwise logs a debug message and continues with full functionality. The routing engine is self-contained.

### Companion Tool Integration Files

| File | Tool | Integration type |
|------|------|-----------------|
| `src/costwise/integrations/graphify.py` | Graphify | MCP subprocess client |
| `src/costwise/graph/loader.py` | Graphify | JSON graph parser |
| `src/costwise/graph/relevance.py` | Graphify | BFS relevance scoring |
| `src/costwise/graph/pruner.py` | Graphify | Context pruning |
| `src/costwise/integrations/headroom.py` | Headroom | Compression wrapper with graph-aware hooks |
| `src/costwise/integrations/rtk.py` | RTK | Read-only SQLite reader |
| `src/costwise/integrations/ponytail.py` | Ponytail | Read-only config/state reader |

### Companion Tool Install Details

| Tool | Language | Install method | Bundled via pip? |
|------|----------|---------------|-----------------|
| Graphify | Python | `pip install graphifyy` | Yes — in `costwise[graph]` extra |
| Headroom | Python | `pip install headroom-ai` | Yes — in `costwise[headroom]` extra |
| RTK | Rust | `brew install rtk` | No — standalone binary |
| Ponytail | Node.js | `npm install -g @dietrichgebert/ponytail` | No — npm package |

---

## The Two Workstreams

### Workstream 1: Core Routing Engine (priority)

Get the classification → routing → budget enforcement pipeline solid. This is Costwise's value proposition: "send this task to a cheaper model when it doesn't need Opus."

Questions to address:
- Are the classification thresholds well-tuned?
- Is the pricing data current and complete?
- How robust is the quality feedback loop (retry detection, auto-tuning)?
- Does the Vertex AI adapter work reliably as the primary provider path?
- Is the proxy server production-ready (error handling, concurrency, health checks)?

### Workstream 2: Companion Optimizations (secondary)

Extra savings layers that squeeze out more cost reduction on top of routing. Can be developed independently, enabled/disabled per user preference.

Questions to address:
- Is the Graphify context pruning worth its complexity?
- Should Headroom compression be on by default?
- Is RTK reporting useful enough to justify the integration?
- Does Ponytail's output estimate adjustment meaningfully affect routing?

---

## Repo

https://github.com/guyoron1/costwise

## Current State

- 286 tests passing
- All companion integrations degrade gracefully when tools are absent
- `costwise doctor` shows status of all components
- `costwise setup` / `install.sh` can install everything in one shot
- Vertex AI adapter is the primary provider path (Guy's only Claude access)
