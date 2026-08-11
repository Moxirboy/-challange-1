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

## What it is, in one paragraph

For each CSV, in order: profile every column (datatype, uniqueness, null rate,
sample values), deterministically prefilter obvious export artifacts, retrieve the
top-k most relevant ontology concepts per column via a hybrid BM25 + embedding
index, ask an LLM to *propose* a disposition (reuse / new attribute / new
relationship / exclude / escalate) for each column, then run that proposal through
a table of **deterministic gates** that can override it — force an escalation, force
a safer disposition, or downgrade an over-budget escalation back to the model's own
best guess. Escalations are batched per CSV into one human round-trip. The result
is a patch (ops), a mapping report (JSON + Markdown), and a full transcript.

## Quickstart

Zero runtime dependencies — Python 3.11+ stdlib only (`pytest` only if you run
tests).

```bash
# Offline, no network, no API key — exercises every deterministic layer
# (profiling, prefilter, hybrid retrieval falls back to lexical-only, gates,
# patch, report) via a documented heuristic decider instead of an LLM.
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

The command above is exactly what produced the committed `out/` directory (Task 2
of this brief). `answers.json` holds one genuine, previously-collected human answer
(`csv1.q2`, about `employee_count` vs. `Organization.size`) that replays
automatically; any *other* escalation the run raises and that isn't in
`answers.json` falls back to a documented per-gate default (see "Escalation
defaults" below) rather than blocking on stdin.

## Model(s) used

| Purpose | Model | Endpoint |
|---|---|---|
| Subject-type + per-column decisions, escalation re-decide | `gemini-3.1-flash-lite` | Google AI Studio's OpenAI-compatible endpoint (`/v1beta/openai/chat/completions`) |
| Retrieval embeddings | `gemini-embedding-001` | same endpoint, `/v1beta/openai/embeddings` |

Both are reached through a plain OpenAI-compatible HTTP client (`ontology_agent/llm.py`)
with a strict JSON-schema `response_format`, so any OpenAI-compatible endpoint works
(OpenRouter is documented as an alternative in `.env.example`). `gemini-3.1-flash-lite`
was chosen empirically: it is the model/key combination that actually completes a
full run within this project's free-tier quota — see "A note on model choice" in
`WRITEUP.md` for the (fairly extensive) trial-and-error behind that, including a
genuine calibration finding about this model's self-reported confidence that changed
how the deterministic gates work.

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

### Scaling story

The prompt for any single column is **O(k)**, not O(ontology size): retrieval
always returns a fixed top-k (8) candidate cards regardless of how many concepts
exist in the ontology, because concepts are pre-compiled into an inverted BM25
index plus embedding vectors rather than being stuffed into the prompt wholesale.
Adding the 10,000th concept to the ontology costs one more card to index (cheap,
incremental — `ConceptIndex.rebuild()` is called once per CSV, after each CSV's
patch is applied) and does not change the size or shape of any future prompt. The
BM25 index is a plain inverted index; the embedding vectors are stored flat today
(cosine over a `list[float]` per card) but the interface (`embed(texts) ->
list[vector]`, `_cosine(a, b)`) is exactly what an ANN index (e.g. faiss/hnswlib)
would sit behind — swapping in one is a retrieval-layer change, not a design
change (see "what's next" in `WRITEUP.md`).

### API cost note

A full 3-CSV run makes **~24 LLM calls** (1 subject-type call per CSV + 1 decision
call per non-prefiltered column + a few escalation re-decides) and **one embedding
batch** (all concept cards, once, plus the handful of column queries not already
identical to a cached card text). The committed `out/run_summary.json` reports the
actual counts for this run: 24 calls total, 14 served from the on-disk cache
(`.cache/`, gitignored) rather than the network, ~12.8K prompt tokens and ~1.6K
completion tokens billed. On Google AI Studio's free tier this fits comfortably —
that budget, not model capability, is why `gemini-3.1-flash-lite` was the model
that actually finished a run (see `WRITEUP.md`).

## Tests

`tests/` is set up for pytest but empty in this submission — the offline `--no-llm`
path was the primary correctness check used during development (deterministic,
free, and exercises every layer except the LLM's own judgment). Given more time,
the highest-value tests would be gate unit tests (feed synthetic `Decision`/
`Candidate` objects into `decide._run_gates` and assert escalation behavior at the
threshold boundaries) and a golden-file regression test over the `--no-llm` output
on the fixtures — see `EVAL_PLAN.md` for the broader evaluation strategy this
would feed into.

## Known limitations

See `WRITEUP.md` for the honest version. Briefly: escalation thresholds are
hand-tuned on 3 fixture CSVs with no held-out validation; `--no-llm` is a much
weaker heuristic than the LLM path; sample-row projection only replays the first
few rows per CSV; nothing persists across runs except the `.cache/` LLM/embedding
cache; and it's a single-model submission — decision quality visibly varies by
model choice (documented in `WRITEUP.md`).
