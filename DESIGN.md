# Implementation spec — ontology retrieval & expansion harness

Zero runtime dependencies (stdlib only; `pytest` for dev). Python 3.11+.
Target layout:

```
ontology_agent/
  __init__.py
  models.py       dataclasses + patch op constructors + JSON (de)serialisation
  ontology.py     in-memory ontology, concept cards, patch application, hygiene audit
  profiler.py     deterministic CSV column profiling + junk prefilter
  retrieval.py    hybrid candidate retrieval (BM25 + embeddings + priors)
  llm.py          OpenAI-compatible chat/embeddings client, disk cache, retries, cost
  decide.py       per-column LLM decision + deterministic gates
  escalate.py     human-in-the-loop question/answer round trip
  patch.py        patch validation + application
  report.py       mapping report (JSON + Markdown) + sample-row projection
  run.py          CLI orchestration over N CSVs in order
tests/
```

---

## 1. `models.py`

```python
DATATYPES = {"string", "integer", "number", "boolean", "date", "datetime"}

@dataclass Attribute:      name: str; datatype: str; required: bool = False
                           description: str = ""; aligned_with: str | None = None
@dataclass Relationship:   name: str; range: str; description: str = ""
                           aligned_with: str | None = None
@dataclass EntityType:     name: str; description: str = ""
                           attributes: list[Attribute]; relationships: list[Relationship]
@dataclass Ontology:       types: list[EntityType]
    .get(type_name) -> EntityType | None          # case-insensitive
    .attr(type_name, attr_name) / .rel(...)       # case-insensitive
    .to_json() / from_json(dict) / from_file(path)
    .deepcopy()
```

Patch ops are dataclasses serialising to the exact JSON shape in the challenge doc,
plus two additive fields we need for traceability (`source_column`, `evidence`):

```python
ReuseOp(op="reuse", source_column, target="Type.concept", rationale, confidence)
AddAttributeOp(op="add_attribute", on_type, name, datatype, rationale, confidence,
               aligned_with=None, source_column=None, description="")
AddRelationshipOp(op="add_relationship", on_type, name, range, rationale, confidence,
                  aligned_with=None, source_column=None, description="")
AddTypeOp(op="add_type", name, attributes: list[dict], rationale, confidence,
          description="", aligned_with=None)
ExcludeOp(op="exclude", source_column, rationale)
FlagOntologyIssueOp(op="flag_ontology_issue", target, issue, severity="warning")
```

`to_dict()` must drop `None`/empty optional fields so the emitted JSON stays close to
the doc's shape. Ordering of keys: `op` first.

---

## 2. `ontology.py`

**Concept cards.** The retrieval index unit. One card per type, per attribute, per
relationship:

```python
@dataclass ConceptCard:
    kind: "type" | "attribute" | "relationship"
    id: str                 # "Organization" | "Organization.website" | "Product.made_by"
    owner_type: str | None
    name: str               # bare local name
    datatype: str | None    # attributes only
    range: str | None       # relationships only
    description: str
    text: str               # dense text used for lexical + embedding indexing
    vacuous: bool           # see hygiene rules below
```

`text` format:
- type: `"Organization — A company, non-profit... | attributes: name, website, founded_year..."`
- attribute: `"Organization.website (string) — Canonical website URL."`
- relationship: `"Product.made_by -> Organization — Manufacturer."`

`build_cards(ontology) -> list[ConceptCard]`. Rebuilt after each applied patch.

**Hygiene audit** `audit(ontology, embedder=None) -> list[FlagOntologyIssueOp]`,
deterministic (embeddings optional, used only to strengthen the near-dup signal):

1. **Near-duplicate types.** For every type pair compute
   `0.5 * name_similarity + 0.5 * attribute_overlap`, where attribute_overlap is
   Jaccard over *semantically normalised* attribute names (see the synonym table in
   §3 — `website≈url≈homepage`, `founded_year≈year_started≈established`). Threshold
   0.5 → flag. This must catch `Organization` vs `Company`.
2. **Vacuous concepts.** An attribute/type is vacuous when its bare name is in
   `{data, value, info, extra, misc, meta, other, field, notes_field, payload}`
   **or** its description matches `/^(misc|miscellaneous|other|various|tbd|n\/a)\b/i`
   or is empty *and* the name is uninformative. Must catch `Person.data`.
   Vacuous cards are **demoted, never reused** (§5) and flagged.
3. **Datatype smell.** Attribute whose name implies a quantity/count/amount
   (`count|size|qty|quantity|amount|number|total|num_`) but whose datatype is
   `string` → flag as a soft issue. Must catch `Organization.size`.

Flags are emitted in the patch of the CSV that first touches the offending concept
(or CSV 1 for whole-ontology issues found at startup).

---

## 3. `profiler.py`

`profile_csv(path) -> CsvProfile` with `columns: list[ColumnProfile]`, `row_count`,
`raw_header`, and the first 3 raw rows.

```python
@dataclass ColumnProfile:
    name: str
    position: int
    tokens: list[str]          # normalised, abbreviation-expanded
    non_null: int; null_rate: float
    distinct: int; uniqueness: float      # distinct / non_null, 0 when non_null == 0
    inferred_datatype: str                # one of DATATYPES
    shape: str | None                     # "url" | "email" | "iso_datetime" | "hex_id" | "currency" | None
    samples: list[str]                    # up to 5 deduped non-null values
    avg_len: float
    is_empty: bool                        # non_null == 0
    is_constant: bool                     # distinct == 1 and non_null > 0
    freetext: bool                        # avg_len > 25 and uniqueness > 0.8 and datatype == string
    entity_like: bool                     # repeats across rows, title-cased/proper-noun-ish, low-ish cardinality
    prefilter: PrefilterVerdict | None
```

**Tokenisation.** Split snake_case / camelCase / spaces / punctuation, lowercase,
then expand abbreviations with a small table — this is what lets lexical retrieval
connect `hq_city` → headquarters city, `msrp` → manufacturer suggested retail price:

```
hq→headquarters, url→web address link, msrp→manufacturer suggested retail price list price,
sku→stock keeping unit, qty→quantity, amt→amount, dt/ts→date time, num/no→number,
pct→percent, org→organization, mfr→manufacturer, addr→address, tel→telephone,
yr→year, id→identifier, desc→description, cnt→count, emp→employee
```
Also emit *synonym expansions* used by BM25 and by the audit's attribute-overlap:
`website ≈ url ≈ homepage ≈ web ≈ site`, `founded ≈ established ≈ started ≈ inception`,
`sector ≈ industry ≈ vertical`, `vendor ≈ supplier ≈ company ≈ organization`,
`price ≈ msrp ≈ cost ≈ list price`, `manufacturer ≈ maker ≈ producer ≈ brand`.

**Datatype inference.** Try in order over non-null values, requiring ≥90% agreement:
boolean → integer → number → date (`YYYY-MM-DD`) → datetime (ISO-8601 w/ time) →
string. Empty column → `string` with `is_empty=True`.

**Junk prefilter** — narrow, high precision, each verdict carries its evidence.
Emits `PrefilterVerdict(action="exclude", reason, evidence)`; anything not matched
goes to the LLM. Rules:

| rule | condition |
|---|---|
| `empty_column` | `is_empty` |
| `unnamed_column` | header matches `^unnamed[:\s_]*\d*$` or is blank |
| `surrogate_key` | (name matches `^_?(id|_id|uuid|guid|pk|row_?id)$` or `^_`) **and** `uniqueness == 1.0` |
| `sync_metadata` | name in `{updated_at, created_at, modified_at, last_modified, _rev, __v, etl_loaded_at, ingested_at, row_number, index}` **and** (`is_constant` or `shape == "iso_datetime"`) |

Notes: the surrogate-key rule requires *both* an id-ish name and perfect uniqueness,
so a real business identifier such as `sku` (name not id-ish) is never dropped.
`sync_metadata` deliberately does not include `date`/`updated`/`status` — those are
ambiguous and belong to the LLM + escalation path.

---

## 4. `llm.py`

OpenAI-compatible client over stdlib `urllib.request`. **Never log or serialise the
API key.**

```python
class LLM:
    def __init__(self, base_url, api_key, model, cache_dir, max_tokens=8192,
                 temperature=0.0, timeout=180)
    def chat_json(self, system: str, user: str, schema: dict, tag: str) -> dict
    @property usage -> {"calls", "prompt_tokens", "completion_tokens", "cached_calls"}

class Embedder:
    def __init__(self, base_url, api_key, model, cache_dir, batch=64)
    def embed(self, texts: list[str]) -> list[list[float]]
```

Requirements:
- `POST {base_url}/chat/completions`, `Authorization: Bearer …`,
  `response_format={"type":"json_schema","json_schema":{"name":tag,"strict":True,"schema":schema}}`.
- **Do not send a `models` fallback array** — non-OpenRouter endpoints reject it.
- `max_tokens` default **8192**: the target model is a thinking model and 1024 gets
  consumed by reasoning before any content is emitted.
- **Disk cache** keyed by `sha256(model + base_url + system + user + schema)` →
  `cache_dir/<hash>.json`. A cache hit costs nothing and makes reruns deterministic
  and resumable after a rate-limit abort. Count hits separately in `usage`.
- **Retries**: on HTTP 429/500/502/503/504 and on read timeout, retry up to 5 times
  with exponential backoff `2**n` seconds (2,4,8,16,32), honouring a `Retry-After`
  header when present. On final failure raise `LLMError` carrying status + a
  **truncated, header-free** body excerpt.
- Environment resolution, first non-empty wins:
  `CHALLENGE_LLM_BASE_URL` → `OMNIX_LLM_BASE_URL`; `CHALLENGE_API_KEY` →
  `OPENROUTER_API_KEY` → `GEMINI_API_KEY` → `OPENAI_API_KEY`;
  model `CHALLENGE_LLM_MODEL` → `OMNIX_LLM_MODEL` → `gemini-3.6-flash`;
  embed model `CHALLENGE_EMBED_MODEL` → `OMNIX_EMBED_MODEL` → `gemini-embedding-001`.
  Load a `.env` from the project root if present (simple `KEY=VALUE` parser, no dep).
- `Embedder.embed` batches, caches per-text, and **degrades gracefully**: if the
  embeddings endpoint errors, log one warning and return `[]` so retrieval falls
  back to lexical-only rather than crashing the run.

---

## 5. `retrieval.py`

```python
class ConceptIndex:
    def __init__(self, cards: list[ConceptCard], embedder: Embedder | None)
    def rebuild(self, cards)                       # after each patch
    def search(self, q: ColumnQuery, k=8, scope: TypeScope | None = None) -> list[Candidate]
```

`ColumnQuery` is built from a `ColumnProfile`:
`text = f"column {name} ({inferred_datatype}) values: {', '.join(samples[:3])}"`,
plus tokens and shape.

**Signals**, each normalised to [0,1], combined as a weighted sum:

| signal | weight | notes |
|---|---|---|
| `bm25` | 0.35 | own BM25 (k1=1.5, b=0.75) over card tokens ∪ synonym expansions; query = column tokens ∪ expansions |
| `embedding` | 0.40 | cosine over `gemini-embedding-001`; 0.0 and weights renormalised when the embedder is unavailable |
| `datatype_prior` | 0.15 | 1.0 exact match; 0.6 compatible (integer↔number, date↔datetime); 0.15 string↔anything; 0.0 hard conflict (number attr vs freetext column) |
| `shape_prior` | 0.10 | column shape `url` + card name contains url/website/homepage/site → 1.0; `email`→email; iso date → date/datetime datatype; else 0.0 |

Post-scoring adjustments:
- **Vacuous demotion**: `score *= 0.15` for cards flagged vacuous by the audit. This
  is the structural guard that keeps `notes` off `Person.data`.
- **Type scoping**: when the CSV's subject type is known, cards are partitioned into
  in-scope (subject type's own attributes/relationships + the types reachable as
  relationship ranges) and out-of-scope (everything else). Out-of-scope cards get
  `score *= 0.6` but are still eligible — a cross-type reuse must remain findable.

`Candidate` carries `card`, `score`, and the per-signal breakdown; the breakdown is
written into the mapping report so a reviewer can see *why* a concept was retrieved.

**Scaling note to carry into the README** (this is explicitly evaluated): the index
is per-concept, BM25 runs off an inverted index, embeddings are a flat matrix today
but are a drop-in for FAISS/pgvector ANN, and the LLM prompt only ever contains the
top-k cards — so prompt size is O(k), independent of ontology size. Recall stage
thousands→~50 is cheap and non-LLM; the LLM only re-ranks and decides over k=8.

---

## 6. `decide.py`

**Step A — subject type (one LLM call per CSV).** Input: file name, header, 3 sample
rows, and the top-k retrieved *type* cards. Output:
`{subject_type, is_new, new_type_description?, rationale, confidence}`.
Expected: vendors→`Organization` (**not** `Company`), catalog→`Product`, crm→`Person`.

**Step B — per-column decision (one LLM call per non-prefiltered column).**
Prompt carries: the column profile, the top-8 candidates with scores, the subject
type card, the decisions already made for earlier columns in this CSV, and the
policy rules. Output schema:

```json
{"disposition": "reuse|new_attribute|new_relationship|exclude|escalate",
 "target": "Type.concept or null",
 "new_name": "...|null", "new_datatype": "...|null", "new_range": "...|null",
 "on_type": "...|null", "aligned_with": "https://schema.org/...|null",
 "rationale": "...", "confidence": 0.0,
 "question": "...|null", "question_context": "...|null", "options": ["..."]}
```

System-prompt rules (verbatim intent):
1. Prefer reuse. A different surface name is not a new concept.
2. Never propose a concept that is a near-synonym of a retrieved candidate.
3. Columns holding the *name of another entity* are relationships, not string
   attributes.
4. Two columns describing one sub-entity (e.g. city + country) map to a single
   relationship to that entity, not two flat attributes.
5. Export artifacts, surrogate keys and sync metadata are excluded, not modelled.
6. Never reuse a concept whose description is vacuous or placeholder; flag it.
7. Sentinel values that are not entities (`Direct`, `N/A`, `None`, `Unknown`) do not
   create entities — note them in the rationale.
8. Escalate only when the answer changes the model and neither the column name, the
   values, nor the ontology settle it. Do not escalate what a defensible default
   covers; say the default in the rationale instead.
9. `confidence` is the probability that a domain expert would agree. Be calibrated.

**Step C — deterministic gates over the LLM output.** The LLM proposes; these decide:

| gate | condition | action |
|---|---|---|
| `near_duplicate` | disposition is `new_*` and best candidate score ≥ **0.62** | if LLM confidence ≥ 0.85 → keep as new but attach the near-miss to the rationale; else → **escalate** with a reuse-vs-new question naming both |
| `datatype_conflict` | disposition `reuse` and column datatype hard-conflicts with the target attribute's datatype | **escalate** — this is the canonical case (`employee_count` vs `Organization.size:string`) |
| `vacuous_target` | disposition `reuse` and target card is vacuous | force `new_attribute` + emit `flag_ontology_issue` on the target |
| `low_confidence` | confidence < **0.55** | **escalate** |
| `unknown_target` | reuse target not in the ontology | retry once with the candidate list restated; then escalate |
| `budget` | more escalations than `--escalation-budget` (default **2**) per CSV | keep the highest-impact ones (rank by `gate_priority`, then by score margin between top-1 and top-2 candidates); downgrade the rest to the LLM's best non-escalate guess with `confidence *= 0.8` and record `downgraded_from_escalation: true` in the report |

Gate priority for ranking: `datatype_conflict` > `low_confidence` > `near_duplicate`.

The budget rule is the anti-over-escalation half of the policy and the thresholds are
the anti-under-escalation half; both are surfaced in the report so the policy is
auditable rather than implicit.

---

## 7. `escalate.py`

Questions are batched per CSV → **one round trip per file**, not one per column.

```python
@dataclass Question:
    id: str            # "csv1.q1"
    csv: str; column: str; gate: str
    question: str      # answerable by someone who did not write the code
    why: str           # what the harness saw and why it cannot decide
    context: dict      # profile digest + top candidates with scores
    options: list[str] # concrete choices, always including a free-text escape
    default: str       # what happens if unanswered
```

`ask(questions, answers_file=None, interactive=True) -> dict[qid, str]`:
- print a readable block per question (column, samples, candidates, why, options);
- if `answers_file` is given and has the id → use it, echo it as `[answered from file]`;
- else if `interactive` and stdin is a TTY → block on `input()`;
- else → use `default` and mark the answer `unanswered_default` in the report.

`incorporate(question, answer, ...) -> Decision` re-runs the decide call for that
column with the human answer appended as authoritative context, and records both
question and answer in the mapping report and the transcript.

---

## 8. `patch.py`

`validate(patch, ontology) -> list[str]` (errors):
- `reuse.target` must resolve to an existing attribute/relationship/type;
- `add_attribute` must not collide with an existing attribute on that type
  (case-insensitive) — collision means the near-dup gate leaked;
- `add_relationship.range` must exist in the ontology **or** be added by an
  `add_type` op in the same patch;
- `datatype ∈ DATATYPES`; attribute/relationship names `snake_case`; type names
  `PascalCase`;
- `0 ≤ confidence ≤ 1`.

`apply(patch, ontology, mode) -> (Ontology, list[AppliedOp])` where mode is
`auto` (apply every op — the mode the demo run uses, and it is labelled as such in
the transcript), `interactive` (y/N per op on stdin), or `none` (emit only, apply
nothing). Application is on a **copy**; the original is never mutated in place.
`flag_ontology_issue` and `exclude` never mutate the ontology.

---

## 9. `report.py`

Per CSV, write `out/<n>_<name>.patch.json` and `out/<n>_<name>.report.json` (+ a
`.report.md` rendering). Report shape:

```json
{"csv": "1_vendors.csv", "row_count": 8, "subject_type": {"name": "Organization", "reused": true, "rationale": "...", "confidence": 0.95},
 "columns": [{"column": "homepage_url", "disposition": "reuse", "target": "Organization.website",
              "rationale": "...", "confidence": 0.93, "decided_by": "llm|rule|human",
              "gates_fired": [], "escalated": false,
              "retrieval": [{"id": "Organization.website", "score": 0.81,
                             "signals": {"bm25": 0.6, "embedding": 0.88, "datatype_prior": 1.0, "shape_prior": 1.0}}]}],
 "escalations": [{"id": "csv1.q1", "question": "...", "answer": "...", "source": "human|file|default", "resulting_decision": "..."}],
 "ontology_issues": [...],
 "sample_rows": [...],
 "stats": {"columns": 7, "reused": 4, "new": 2, "excluded": 0, "escalated": 1,
           "llm_calls": 9, "cached_calls": 0, "prompt_tokens": 0, "completion_tokens": 0}}
```

**Sample-row projection** (`project_rows(rows, decisions, ontology, n=3)`): deterministic,
no LLM. For each of the first 3 rows emit the entities and values it produces under
the *patched* ontology — the subject entity with its literal attributes, plus one
entity per relationship target (with a stable synthetic id such as
`Place:{city}|{country}`), and a list of skipped columns with the reason. Sentinel
relationship values (`Direct`, `N/A`, `-`, empty) must produce **no** entity and be
recorded under `skipped`.

---

## 10. `run.py` (CLI)

```
python -m ontology_agent.run \
  --ontology fixtures/seed_ontology.json \
  --csv fixtures/1_vendors.csv fixtures/2_product_catalog.csv fixtures/3_crm_export.csv \
  --out out/ [--answers answers.json] [--escalation-budget 2]
  [--approve auto|interactive|none] [--no-llm] [--cache-dir .cache]
```

Flow: load ontology → startup hygiene audit → for each CSV in order: profile →
prefilter → subject type → retrieve+decide per column → gates → batched escalation →
patch → validate → emit patch + report → apply → rebuild index → next CSV. Finally
write `out/final_ontology.json` and `out/run_summary.json` (per-CSV stats, totals,
LLM usage, wall time).

Everything printed to stdout is the transcript; also tee it to `out/transcript.md`.
Print a clear banner per stage so the transcript is readable end to end. Never print
the API key, `.env` contents, or raw request headers.

`--no-llm` runs the same pipeline with a heuristic decider (top retrieval candidate
above 0.62 → reuse; else new attribute; prefilter excludes stand) — it exists so the
deterministic layers can be tested without network or spend, not as a second engine.

---

## 11. Tests (`tests/`, pytest, no network)

1. profiler: datatype inference, uniqueness, shape detection on the three fixtures.
2. prefilter: `_id`, `Unnamed: 8`, `updated_at` excluded; `sku`, `date`, `status` **not** excluded.
3. audit: `Organization`/`Company` flagged as near-duplicates; `Person.data` flagged
   vacuous; `Organization.size` flagged as a datatype smell.
4. retrieval (lexical only, embedder=None): `homepage_url` ranks `Organization.website`
   top-1; `established` ranks `Organization.founded_year` top-1; `notes` does **not**
   rank `Person.data` top-1 (vacuous demotion).
5. gates: datatype conflict escalates; near-dup with low confidence escalates;
   escalation budget downgrades the lowest-priority question.
6. patch: validation rejects an unknown reuse target, a colliding add_attribute, and
   a relationship whose range does not exist; `apply` does not mutate the input.
7. end-to-end `--no-llm` run over all three fixtures produces valid patches and
   reports and a final ontology that still validates.
