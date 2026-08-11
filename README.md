# Challenge

```
.
├── README.md              ← you are here: how to run it, and where everything is
├── DESIGN.md              ← authoritative implementation spec
├── EVAL_PLAN.md           ← eval plan (deliverable 5)
├── WRITEUP.md             ← ≤1-page design write-up (deliverable 4)
├── answers.json           ← recorded human answers, replayed into escalations
├── .env.example           ← copy to .env and fill in CHALLENGE_API_KEY
│
├── fixtures/              ← inputs, as given
│   ├── seed_ontology.json
│   ├── 1_vendors.csv
│   ├── 2_product_catalog.csv
│   └── 3_crm_export.csv
│
├── ontology_agent/        ← the harness (deliverable 1) — stdlib only
│   ├── run.py             ← CLI entrypoint / per-CSV orchestration
│   ├── models.py          ← typed ops, dispositions, report shapes
│   ├── ontology.py        ← in-memory ontology + patch application
│   ├── profiler.py        ← per-column datatype / uniqueness / null-rate / samples
│   ├── retrieval.py       ← hybrid BM25 + embedding concept index
│   ├── llm.py             ← provider client, caching, structured output
│   ├── decide.py          ← deterministic gates over the LLM's proposal
│   ├── escalate.py        ← batched human round-trips (stdin or answers file)
│   ├── patch.py           ← patch assembly + validation
│   └── report.py          ← mapping report (JSON + Markdown)
│
└── out/                   ← committed output of the live run (deliverables 2 & 3)
    ├── <n>_<name>.patch.json / .report.json / .report.md   (×3 CSVs)
    ├── final_ontology.json
    ├── run_summary.json
    └── transcript.md      ← full run, 4 escalation round-trips
```

## What is it

For each CSV in order, the harness profiles every column (datatype, uniqueness,
null rate, sample values), filters out obvious export artifacts, and retrieves the
most relevant ontology concepts for that column from a hybrid BM25 plus embedding
index. An LLM then *proposes* a disposition for each column (reuse, new attribute,
new relationship, exclude, or escalate), and a table of **deterministic gates**
reviews that proposal and can override it by forcing an escalation, forcing a safer
disposition, or falling back to the model's own best guess once the escalation
budget is spent. Escalations are batched into one human round trip per CSV, and the
run produces a patch of typed ops, a mapping report in JSON and Markdown, and a
full transcript.

## Quickstart

Zero runtime dependencies — Python 3.11+ stdlib only (`pytest` only if you run
tests).

```bash
# Offline: no network, no API key, heuristic decider instead of an LLM.
python3 -m ontology_agent.run \
  --ontology fixtures/seed_ontology.json \
  --csv fixtures/1_vendors.csv fixtures/2_product_catalog.csv fixtures/3_crm_export.csv \
  --out out/ --no-llm --approve auto
```

```bash
# Live, LLM-backed run (see "Model(s) used" below for exactly what this needs).
cp .env.example .env   # then fill in CHALLENGE_API_KEY
python3 -m ontology_agent.run \
  --ontology fixtures/seed_ontology.json \
  --csv fixtures/1_vendors.csv fixtures/2_product_catalog.csv fixtures/3_crm_export.csv \
  --out out/ --approve auto --answers answers.json
```

This is the exact command that produced the committed `out/`. `answers.json`
replays one real human answer (`csv1.q2`, `employee_count` vs.
`Organization.size`); any other escalation falls back to a documented per-gate
default (see "Escalation defaults") instead of blocking on stdin.

## Model(s) used

| Purpose | Model |
|---|---|
| Subject-type + per-column decisions, escalation re-decide | `gemini-3.1-flash-lite` |
| Retrieval embeddings | `gemini-embedding-001` |

## Setup

1. Python 3.11+. No venv, no install step, and no `pyproject.toml`/`requirements.txt`
   needed — the package has zero runtime dependencies and runs directly via
   `python3 -m ontology_agent.run ...` from the repo root. `pip install pytest`
   only if you want to run tests.
2. `cp .env.example .env` and fill in `CHALLENGE_API_KEY` with an OpenAI-compatible
   key (Google AI Studio, OpenRouter, etc.). Never commit `.env` — it's gitignored.
3. Run the quickstart command above. `--no-llm` needs no key at all.

## CLI flags

```
python -m ontology_agent.run
  --ontology PATH          seed ontology JSON (required)
  --csv PATH [PATH ...]    CSV files to ingest, in order (required)
  --out DIR                output directory for patches/reports/transcript (required)
  --answers PATH           optional JSON file of {question_id: answer}, e.g. answers.json
  --escalation-budget N    max escalations kept per CSV (default 2)
  --approve {auto,interactive,none}
                           patch application mode (default none — emit-only,
                           never mutates the ontology unless you opt in)
  --no-llm                 run the deterministic heuristic decider; no network, no key
  --cache-dir DIR          LLM/embedding disk cache directory (default .cache)
```

`--approve auto` applies every validated op non-interactively (used for the
committed `out/`); `--approve interactive` confirms each op; `--approve none`
(the default) only ever emits the patch/report — nothing is mutated. `--no-llm`
swaps the LLM proposal step for a documented lexical heuristic (top retrieval
candidate ≥ 0.62 → reuse, else → new attribute); every deterministic layer
downstream — prefilter, retrieval, gates, escalation, patch validation, reports —
runs identically either way, so `--no-llm` is a free (no network, no spend) way to
exercise and test the whole pipeline except the LLM's own judgment calls.

### Escalation defaults

If a question raised during a run isn't answered (not in the `--answers` file, and
the process isn't attached to an interactive TTY), each gate has a documented,
safe fallback rather than blocking:

| Gate | Default if unanswered | Why |
|---|---|---|
| `datatype_conflict`, `unknown_target` | `exclude` | the proposed target is actively unsafe (wrong datatype / doesn't exist); silently keeping it would apply a broken mapping |
| `near_duplicate` (normal — score-only match) | `reuse:<top retrieval candidate>` | the gate fired *because* the model's "new" proposal looked like a duplicate — trusting the retrieval evidence is safer than keeping a flagged-risky guess, and a `reuse` op never mutates the ontology so it can't fail validation |
| `near_duplicate` (fired via its lowered datatype-conflict bar) | keep the model's original proposal | here the near-duplicate candidate's own datatype conflicts with the column's (e.g. an integer column vs. a string attribute) — reusing it by default would be exactly the silently-wrong mapping the gate exists to prevent, so the fallback is the model's own "new" guess instead, same as `low_confidence` below |
| `low_confidence`, `score_margin_ambiguous`, `llm_escalate` | keep the model's original proposal | genuinely ambiguous with no strong signal either way; the model's own guess is structurally valid, just uncertain |

## Where each deliverable lives

| # | Deliverable | Location |
|---|---|---|
| 1 | Code + this README | `ontology_agent/` (10 modules) + `README.md` |
| 2 | Transcript of a full run, ≥1 escalation round-trip | `out/transcript.md` (this run has **4** escalation round-trips) |
| 3 | Patch + mapping report per CSV | `out/<n>_<name>.patch.json`, `out/<n>_<name>.report.json`, `out/<n>_<name>.report.md` (×3 CSVs) — plus `out/final_ontology.json` and `out/run_summary.json` |
| 4 | Write-up (≤1 page) | `WRITEUP.md` |
| 5 | Eval plan | `EVAL_PLAN.md` |

## Architecture

Five stages, run per column (stages 1–2 run once per CSV):

1. **Profile** (`profiler.py`) — per-column datatype inference, uniqueness, null
   rate, value shape, sample values. Also runs the deterministic **junk prefilter**
   here: surrogate keys, sync-metadata fields (constant/near-constant columns like
   `updated_at`), and empty columns are excluded before any retrieval or LLM call —
   no model ever sees `_id` or `Unnamed: 8`.
2. **Hybrid retrieval** (`retrieval.py`, `ontology.py`) — the ontology is compiled
   into one **card per concept** (type / attribute / relationship), each a short
   text blob (name, description, datatype, owning type). Cards are indexed by a
   hand-rolled BM25 (with stopword filtering and a small synonym table) plus
   embedding cosine similarity, combined with datatype-compatibility and
   value-shape priors. A startup hygiene audit detects near-duplicate types (e.g.
   `Organization`/`Company`); the retrieval layer resolves the non-canonical twin's
   cards onto the canonical type at query time, so a `Company.url` hit surfaces as
   an `Organization.website` candidate instead of competing with it.
3. **LLM decide** (`decide.py`, step B) — one call proposes a disposition (reuse /
   new attribute / new relationship / exclude / escalate) for the column, given its
   profile and the top-k retrieved candidates. `--no-llm` swaps this for a
   documented lexical heuristic; every other stage is unchanged.
4. **Deterministic gates** (`decide.py`, step C) — the proposal is checked against
   a fixed table: `near_duplicate` (a "new" proposal against a strong existing
   candidate), `datatype_conflict` (a `reuse` proposal whose target has an
   incompatible datatype), `vacuous_target` (silently redirect away from a
   placeholder concept like `Person.data`), `low_confidence`, `unknown_target`
   (retry once, then escalate), `score_margin_ambiguous` (top-2 retrieval
   candidates too close to call). These gates are what actually decide whether a
   column's mapping is trusted — see `WRITEUP.md` for why they no longer trust the
   model's self-reported confidence to do that job.
5. **Escalate** (`escalate.py`) — every column a gate flagged is ranked by
   `(gate priority, retrieval score margin, column position)` and the top
   `--escalation-budget` (default 2) per CSV survive as real questions; the rest
   are downgraded back to their own best proposal. Survivors are batched into one
   human round-trip per CSV — answered from `--answers`, interactive stdin, or a
   documented default (table above) — then re-decided with the answer as
   authoritative context before the patch is assembled.
