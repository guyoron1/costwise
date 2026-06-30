# Costwise: Ship as Open-Source — Execution Plan

> **Load this document at the start of a fresh session to execute.**
> Project directory: `/Users/goron/Desktop/the_next_big_thing`
> Python venv: `.venv/` (Python 3.14, `uv pip install -e ".[proxy,dashboard,mcp,dev]"`)
> Current state: 256 tests passing, all 6 development phases complete, no git repo yet.

---

## What Already Exists

- `LICENSE` — Apache 2.0 (full text, correct)
- `README.md` — bare-bones stub (23 lines, just install + `costwise proxy` + `costwise gain`)
- `pyproject.toml` — well-structured with hatchling, extras, scripts, ruff, pytest config
- `costwise.example.toml` — example config (proxy + budget + tracking + providers, missing graph/feedback/integrations sections)
- `src/costwise/` — complete codebase (7 packages, 45 Python files)
- `tests/` — 256 tests across 21 test files + conftest.py
- `src/costwise/dashboard/static/` — htmx.min.js + style.css

## What's Missing

| Item | Impact | Effort |
|------|--------|--------|
| Git repo initialization | Can't version, share, or publish without it | Small |
| `.gitignore` | Will commit `.venv/`, `__pycache__/`, `.db` files without it | Small |
| README.md rewrite | Current stub doesn't explain what Costwise does or why | Medium |
| `costwise.example.toml` update | Missing `[costwise.routing]`, `[costwise.graph]`, `[costwise.feedback]`, `[costwise.integrations]` sections | Small |
| Architecture docs | No explanation of the 4-layer stack, data flow, or how modules connect | Medium |
| Contributing guide | No contribution guidelines for an open-source project | Small |
| CI workflow | No GitHub Actions for test + lint on PR | Small |
| PyPI readiness check | Verify `hatch build` produces a clean wheel | Small |
| Real-world validation | The 95-97% savings claim is unproven — need replay harness | Large |
| Pricing JSON currency | 10 models bundled — verify prices match current provider pages | Small |

---

## Execution Steps

### Step 1: Git + .gitignore

Create `.gitignore` covering Python, venv, IDE, SQLite, OS files. Then `git init` + initial commit with the entire codebase.

**Files to create:**
- `.gitignore`

**Commands:**
```bash
git init
git add -A
git commit -m "Initial commit: Costwise v0.1.0 — all 6 phases complete"
```

---

### Step 2: README.md Rewrite

Replace the 23-line stub with a proper open-source README. Structure:

1. **Header** — name, one-line description, badges (license, Python version, tests)
2. **The Problem** — LLM costs for coding agents ($50-200/day on Opus), existing tools only optimize input tokens, output costs 2-5x more
3. **The Solution** — 4-layer stack diagram (RTK → Ponytail → Costwise → Headroom), predicted 95-97% savings
4. **Features** — bullet list of what Costwise does (routing, pruning, arbitrage, budget, feedback, dashboard, MCP)
5. **Quick Start** — install, configure, run proxy, point Claude Code at it
6. **How It Works** — request flow diagram (text/ASCII), classification tiers, auto-tuning
7. **Dashboard** — screenshot placeholder or description of the 6 panels
8. **MCP Tools** — table of 5 tools with descriptions
9. **CLI Commands** — table of all commands
10. **Configuration** — reference to `costwise.example.toml`, key config sections explained
11. **Integration with Other Tools** — RTK, Headroom, Ponytail, Graphify — what each does, how to enable
12. **Architecture** — package diagram showing the 7 packages and their dependencies
13. **Contributing** — link to CONTRIBUTING.md
14. **License** — Apache 2.0

**Files to modify:**
- `README.md` (full rewrite)

---

### Step 3: Example Config Update

Update `costwise.example.toml` to include ALL config sections with comments:

```toml
[costwise.proxy]         # ✓ exists
[costwise.routing]       # ✗ missing — thresholds, providers, confidence
[costwise.budget]        # ✓ exists
[costwise.tracking]      # ✓ exists
[costwise.graph]         # ✗ missing — graph path, relevance threshold, max hops
[costwise.feedback]      # ✗ missing — auto_tune, nudge_step, target rate
[costwise.integrations]  # ✗ missing — rtk, ponytail, headroom, graphify
[[costwise.providers]]   # ✓ exists (commented)
```

**Files to modify:**
- `costwise.example.toml`

---

### Step 4: CONTRIBUTING.md

Standard open-source contribution guide:
- How to set up dev environment (`uv pip install -e ".[all]"`)
- How to run tests (`pytest tests/ -v`)
- How to run linting (`ruff check src/ tests/`)
- Code style (ruff config, 100-char lines, no comments unless non-obvious WHY)
- PR process (fork, branch, test, PR)
- Architecture overview (which package owns what)

**Files to create:**
- `CONTRIBUTING.md`

---

### Step 5: CI Workflow (GitHub Actions)

Single workflow file: test on Python 3.10, 3.11, 3.12, 3.13, 3.14. Steps: install deps, lint with ruff, run pytest.

**Files to create:**
- `.github/workflows/ci.yml`

---

### Step 6: Pricing Verification

Read `src/costwise/core/pricing.py` and verify the 10 bundled model prices against current provider pricing pages. Update any that are stale. The file header says "Last updated: 2026-06-30" — confirm this is accurate.

Models to verify:
- Anthropic: claude-opus-4-7, claude-sonnet-4-6, claude-haiku-4-5
- OpenAI: gpt-5, gpt-4.1, gpt-4.1-mini, gpt-4.1-nano
- Google: gemini-2.5-pro, gemini-2.5-flash, gemini-2.0-flash

**Files to potentially modify:**
- `src/costwise/core/pricing.py`

---

### Step 7: Build Verification

Run `hatch build` and verify it produces a clean sdist + wheel. Check that the wheel includes all packages, templates, static files, and schema.sql.

**Commands:**
```bash
hatch build
# Inspect wheel contents
unzip -l dist/costwise-0.1.0-py3-none-any.whl | head -50
```

**Potential issues:**
- `schema.sql` might not be included (needs `package-data` config in pyproject.toml)
- `templates/` and `static/` might not be included
- If missing, add `[tool.hatch.build.targets.wheel.force-include]` or use package data config

**Files to potentially modify:**
- `pyproject.toml` (add package data includes for non-Python files)

---

### Step 8: Validation Harness (Optional but Valuable)

Build a replay harness that proves the savings claim. This is the most complex step but also the most impactful for credibility.

**Design:**
- `scripts/validate.py` — CLI script that:
  1. Reads recorded routing decisions from SQLite
  2. Groups by session
  3. For each session, calculates cost under each config:
     - Baseline (all requests → Opus)
     - Costwise routing only
     - Costwise + pruning
     - Full stack estimate
  4. Outputs a comparison table

**Files to create:**
- `scripts/validate.py`

This is a stretch goal — skip if time-constrained. The core product ships without it.

---

### Step 9: Final Commit + Tag

After all changes:
```bash
git add -A
git commit -m "Prepare for open-source release: README, docs, CI, packaging"
git tag v0.1.0
```

---

## Execution Order

| Step | Depends On | Parallelizable |
|------|-----------|----------------|
| 1. Git + .gitignore | — | — |
| 2. README rewrite | — | Yes (with 3, 4, 5, 6) |
| 3. Example config update | — | Yes |
| 4. CONTRIBUTING.md | — | Yes |
| 5. CI workflow | — | Yes |
| 6. Pricing verification | — | Yes |
| 7. Build verification | 1 | No (needs git for clean build) |
| 8. Validation harness | 1 | Yes (optional) |
| 9. Final commit + tag | All above | — |

Steps 2-6 are independent and can be done in parallel. Step 7 needs step 1 done first. Step 9 is last.

---

## Success Criteria

- [ ] Git repo initialized with clean history
- [ ] `.gitignore` prevents committing venv, pycache, db files
- [ ] `README.md` is comprehensive (>200 lines) and explains the full product
- [ ] `costwise.example.toml` documents ALL config options
- [ ] `CONTRIBUTING.md` exists with dev setup instructions
- [ ] `.github/workflows/ci.yml` runs tests + lint on multiple Python versions
- [ ] `hatch build` produces a wheel that includes all non-Python files (SQL, HTML, CSS, JS)
- [ ] All 256 tests still pass after all changes
- [ ] `v0.1.0` tag created
