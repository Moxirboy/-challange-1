# Challenge

```
.
├── README.md              ← you are here: how to run it, and where everything is
├── DESIGN.md              ← implementation spec
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
├── ontology_agent/        ← the harness (deliverable 1), stdlib only
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
null rate, sample values) and filters out obvious export artifacts. It retrieves
the most relevant ontology concepts for that column from a hybrid BM25 plus
embedding index. An LLM proposes a disposition per column (reuse, new attribute,
new relationship, exclude, or escalate). Deterministic gates then check that
proposal and can override it: force an escalation, force a safer disposition, or
fall back to the model's guess once the escalation budget is spent. Escalations
are batched into one human round trip per CSV. The run emits a patch of typed ops,
a mapping report in JSON and Markdown, and a transcript.

## Quickstart

Zero runtime dependencies. Python 3.11+ stdlib only (`pytest` only if you run
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
`Organization.size`). Any other escalation falls back to a documented per-gate
default (see "Escalation defaults") instead of blocking on stdin.

## Model(s) used

| Purpose | Model |
|---|---|
| Subject-type + per-column decisions, escalation re-decide | `gemini-3.1-flash-lite` |
| Retrieval embeddings | `gemini-embedding-001` |

## Setup

1. Python 3.11+. No venv, no install step, no `pyproject.toml` or
   `requirements.txt`. Run it via `python3 -m ontology_agent.run ...` from the repo
   root. `pip install pytest` only if you want to run tests.
2. `cp .env.example .env` and fill in `CHALLENGE_API_KEY` with an OpenAI-compatible
   key (Google AI Studio, OpenRouter, etc.). Never commit `.env`. It is gitignored.
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
                           patch application mode (default auto)
  --interactive            ask escalation questions on stdin instead of taking
                           gate defaults (default off)
  --no-llm                 run the deterministic heuristic decider; no network, no key
  --cache-dir DIR          LLM/embedding disk cache directory (default .cache)
```

`--approve auto` is the default and applies every validated op
non-interactively, which is what produced the committed `out/`. It is the default
because the brief requires accepted patches to reach the in-memory ontology before
the next CSV, and `--approve none` silently breaks that for a multi-CSV run: CSV 2
and CSV 3 then retrieve against the untouched seed ontology and decide
differently. `--approve interactive` confirms each op on stdin. `--approve none`
emits the patch and report and mutates nothing.

`--interactive` controls a separate thing: whether a human is asked the
escalation questions. It is off by default, so a run's questions never depend on
invisible TTY state and the committed output stays reproducible. With it off, or
on but with no TTY attached, each question takes its documented gate default and
the run prints which of those two cases applied.

`--no-llm` swaps the LLM proposal step for a documented lexical heuristic
(top retrieval candidate ≥ 0.62 → reuse, else → new attribute). Every layer
downstream runs identically either way: prefilter, retrieval, gates, escalation,
patch validation, reports. So it exercises the whole pipeline except the LLM's
judgment calls, with no network and no spend.

### Escalation defaults

A question can go unanswered: not in the `--answers` file, and no human to ask
(`--interactive` off, or on with no TTY). Each gate then has a documented, safe
fallback rather than blocking:

| Gate | Default if unanswered | Why |
|---|---|---|
| `datatype_conflict`, `unknown_target` | `exclude` | the proposed target is unsafe (wrong datatype / doesn't exist); silently keeping it would apply a broken mapping |
| `near_duplicate` (normal, score-only match) | `reuse:<top retrieval candidate>` | the gate fired because the model's "new" proposal looked like a duplicate. Trusting the retrieval evidence beats keeping a flagged-risky guess, and a `reuse` op never mutates the ontology so it can't fail validation |
| `near_duplicate` (fired via its lowered datatype-conflict bar) | keep the model's original proposal | here the near-duplicate candidate's own datatype conflicts with the column's (e.g. an integer column vs. a string attribute). Reusing it by default would be the silently-wrong mapping the gate exists to prevent, so the fallback is the model's own "new" guess, same as `low_confidence` below |
| `low_confidence`, `score_margin_ambiguous`, `llm_escalate` | keep the model's original proposal | ambiguous with no strong signal either way; the model's own guess is structurally valid, just uncertain |

## Where each deliverable lives

| # | Deliverable | Location |
|---|---|---|
| 1 | Code + this README | `ontology_agent/` (10 modules) + `README.md` |
| 2 | Transcript of a full run, ≥1 escalation round-trip | `out/transcript.md` (this run has 4 escalation round-trips) |
| 3 | Patch + mapping report per CSV | `out/<n>_<name>.patch.json`, `out/<n>_<name>.report.json`, `out/<n>_<name>.report.md` (×3 CSVs), plus `out/final_ontology.json` and `out/run_summary.json` |
| 4 | Write-up (≤1 page) | `WRITEUP.md` |
| 5 | Eval plan | `EVAL_PLAN.md` |

## Architecture

Five stages, run per column (stages 1–2 run once per CSV):

1. **Profile** (`profiler.py`). Per-column datatype inference, uniqueness, null
   rate, value shape, sample values. The deterministic junk prefilter runs here
   too. Surrogate keys, sync-metadata fields (constant or near-constant columns
   like `updated_at`) and empty columns are excluded before any retrieval or LLM
   call. No model ever sees `_id` or `Unnamed: 8`.
2. **Hybrid retrieval** (`retrieval.py`, `ontology.py`). The ontology is compiled
   into one card per concept (type / attribute / relationship), each a short text
   blob (name, description, datatype, owning type). Cards are indexed by a
   hand-rolled BM25 (stopword filtering, small synonym table) plus embedding cosine
   similarity, combined with datatype-compatibility and value-shape priors. A
   startup hygiene audit detects near-duplicate types (e.g.
   `Organization`/`Company`), and retrieval aliases the non-canonical twin's cards
   onto the canonical type at query time. A `Company.url` hit surfaces as an
   `Organization.website` candidate.
3. **LLM decide** (`decide.py`, step B). One call proposes a disposition (reuse /
   new attribute / new relationship / exclude / escalate), given the column profile
   and the top-k retrieved candidates. `--no-llm` swaps this for a documented
   lexical heuristic. Every other stage is unchanged.
4. **Deterministic gates** (`decide.py`, step C). The proposal is checked against
   a fixed table: `near_duplicate` (a "new" proposal against a strong existing
   candidate), `datatype_conflict` (a `reuse` proposal whose target has an
   incompatible datatype), `vacuous_target` (redirect away from a placeholder
   concept like `Person.data`), `low_confidence`, `unknown_target` (retry once,
   then escalate), `score_margin_ambiguous` (top-2 retrieval candidates too close
   to call). See `WRITEUP.md` for why these gates no longer trust the model's
   self-reported confidence.
5. **Escalate** (`escalate.py`). Every flagged column is ranked by `(gate
   priority, retrieval score margin, column position)`. The top
   `--escalation-budget` (default 2) per CSV survive as questions. The rest are
   downgraded back to their own best proposal. Survivors are batched into one human
   round trip per CSV, answered from `--answers`, interactive stdin, or a
   documented default (table above), then re-decided with the answer as context.
