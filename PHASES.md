# Phases

End to end walkthrough of one run, phase by phase. Every output block below is
copied verbatim from the committed `out/transcript.md` and `out/*.report.md`,
produced by the documented live command:

```bash
python3 -m ontology_agent.run \
  --ontology fixtures/seed_ontology.json \
  --csv fixtures/1_vendors.csv fixtures/2_product_catalog.csv fixtures/3_crm_export.csv \
  --out out/ --approve auto --answers answers.json
```

Pipeline in one line:

```
profile -> prefilter -> subject type -> (retrieve -> propose -> gate) per column
  -> budget -> ask -> re-decide -> patch -> apply -> report -> rebuild index -> next CSV
```

---

## Phase 0. Startup

Runs once, before any CSV is opened.

### 0a. Clients

`_build_llm_clients` (`run.py:91`) constructs `LLM.from_env()` and
`Embedder.from_env()`. Both read `.env`, both get `--cache-dir`. The client is
built even with no API key (`llm.py:34`), because a cache hit should still be
servable with the network off.

### 0b. Hygiene audit

The ontology is inspected against itself. Three checks:

**Near duplicate types** (`ontology.py:259`). `_name_similarity` and
`_attribute_overlap` are combined, with an optional embedding boost. Above 0.5
the pair is a duplicate. `_type_richness` (`ontology.py:297`) then picks the
canonical twin by attribute count, relationship count and description presence.

**Vacuous concepts** (`ontology.py:200`). Generic name or placeholder
description.

**Datatype smells.** An attribute named like a quantity but typed `string`.

```
==============================================================================
 STARTUP HYGIENE AUDIT
==============================================================================
  [warning] Organization / Company: 'Organization' and 'Company' look like near-duplicate types (name_similarity=1.00, attribute_overlap=0.60, score=0.80 >= 0.5); canonical type is 'Organization' (richer: more attributes/relationships and/or descriptions) -- retrieval resolves 'Company' concepts onto it; consider deleting 'Company' from the ontology.
  [warning] Person.data: attribute 'Person.data' is vacuous (generic name or placeholder description); demote, don't reuse
  [warning] Organization.size: attribute 'Organization.size' name implies a quantity but its datatype is 'string'; likely should be integer/number
```

**Reading this output.** `name_similarity=1.00` because both names normalise to
the same lemma. `attribute_overlap=0.60` because `name`/`website`/`founded_year`
and `name`/`url`/`year_started` are the same three concepts under two spellings.
`Organization` wins on richness: it has 5 attributes and a relationship, `Company`
has 3 attributes and none.

The third line matters more than it looks. It predicts the `employee_count`
escalation two phases before that column is read. The harness knew
`Organization.size` was mistyped before it ever saw an integer headcount.

These three become `flag_ontology_issue` ops in CSV 1's patch. The canonical
choice is not just reported, it rewires retrieval in phase 3.

### 0c. Card compilation

`build_cards` (`ontology.py:73`) turns every type, attribute and relationship
into one `ConceptCard`. The card text is what BM25 tokenises and what gets
embedded, so its exact shape matters:

```
type          Organization — A company, non-profit… | attributes: name, website, founded_year, …
attribute     Organization.website (string) — Canonical website URL.
relationship  Organization.headquartered_in -> Place — Primary headquarters location.
```

Relationship cards carry `-> Range` and no datatype. Attribute cards carry
`(datatype)` and no range. That asymmetry is what the `kind_mismatch` gate keys
on in phase 5.

---

## Phase 1. Profile

`profile_csv` (`profiler.py:429`), once per CSV, no LLM.

### 1a. Datatype inference

`infer_datatype` (`profiler.py:208`) tries in order, each needing **90% or more
agreement** across non null values: `boolean -> integer -> number -> date ->
datetime -> string`. The 90% floor tolerates a few dirty rows without letting one
stray value flip the type.

### 1b. Derived flags

```python
uniqueness  = distinct / non_null
is_constant = distinct == 1 and non_null > 0
freetext    = avg_len > 25 and uniqueness > 0.8 and datatype == "string"
entity_like = string, distinct > 1, avg_len < 40, uniqueness <= 0.8, title-case-ish
```

`freetext` catches `notes` ("renewal call went well"). `entity_like` is the
weakest of these and I do not rely on it in any gate. On a 7 row fixture
`manufacturer` is 6 distinct (0.86 uniqueness) so it reads as *not* entity like,
while `hq_country` repeats (0.62) so it reads as entity like. That is backwards,
and it is why `kind_mismatch` keys on datatype instead.

### 1c. Prefilter

Four rules (`profiler.py:_prefilter`). Each needs **name evidence AND value
evidence**, never a name alone:

| Rule | Name test | Value test |
|---|---|---|
| `empty_column` | (none) | `is_empty` |
| `unnamed_column` | blank or `_UNNAMED_RE` | (none) |
| `surrogate_key` | id-like regex or leading `_` | `uniqueness == 1.0` |
| `sync_metadata` | in `SYNC_METADATA_NAMES` | `is_constant` or `shape == iso_datetime` |

A column flagged here is excluded before retrieval and before any LLM call. No
tokens are ever spent on it.

```
-- profiling --
  rows=8 columns=7
```

```
  _id: exclude (prefilter: surrogate_key)
  updated_at: exclude (prefilter: sync_metadata)
  Unnamed: 8: exclude (prefilter: empty_column)
```

**Reading this output.** The three CRM artifacts die here, with evidence recorded
in the report:

```
_id         "name '_id' is id-like and every non-null value is unique (6/6)"
updated_at  "name 'updated_at' is a known sync-metadata field and is constant across rows"
Unnamed: 8  "column 'Unnamed: 8' has no non-null values"
```

The conjunction is the defensible part. A column named `id` whose values repeat
is not dropped, because it is probably a foreign key. `updated_at` died because it
is both a known name and constant across all 6 rows. Catching it needed the
values, not the name.

---

## Phase 2. Subject type

One LLM call per CSV (`run.py:203`). Produces `SubjectTypeDecision(subject_type,
is_new, new_type_description, rationale, confidence)`, which becomes a `TypeScope`
biasing every column search that follows.

```
-- subject type --
  Organization (is_new=False, confidence=1.00)
  rationale: The data describes business entities with attributes like sector, employee count, and headquarters location, which aligns perfectly with the definition of an Organization.
```

**Reading this output.** Doing this once per CSV rather than per column is what
stops column 5 from deciding the file is about something different than column 2
did. The other two CSVs resolved to `Product` (1.00) and `Person` (0.95).

Type cards score lower than attribute cards under corpus wide BM25 because they
are long (they list every attribute name). That is why subject type matching gets
its own much lower floor, `SUBJECT_TYPE_MIN_SCORE = 0.05` (`decide.py:56`), rather
than reusing the 0.62 near duplicate bar.

---

## Phase 3. Retrieval

`index.search(q, k=8, scope)` (`retrieval.py:411`), once per column.

### 3a. Query

`ColumnQuery.from_profile` carries `text`, `tokens`, `datatype`, `shape`,
`freetext`. Tokens come from `tokenize` (snake, camel and kebab splitting) then
`expand_synonyms` (`profiler.py:114`).

### 3b. BM25

Hand rolled (`retrieval.py:173`), `k1=1.5`, `b=0.75`, stopwords filtered.

### 3c. The blend

```
score = 0.35*bm25 + 0.40*embedding + 0.15*datatype_prior + 0.10*shape_prior
```

Embedding carries the most weight because synonym matching is the hard part. The
priors are tie breakers, not drivers. With no embedder the embedding term is
dropped and the rest renormalise over 0.60 (`retrieval.py:449`), so offline scores
read higher than live ones. Same ranking, different absolute numbers.

`datatype_compatibility` (`retrieval.py:229`) is deliberately asymmetric:

| Case | Value | Why |
|---|---|---|
| exact match | 1.0 | |
| compatible pair | 0.6 | |
| card has no datatype (type or relationship) | 0.15 | neutral, neither match nor conflict |
| numeric column onto string attribute | 0.0 | hard conflict, the profiler already required 90% numeric |
| string column onto numeric attribute | 0.0 if freetext else 0.15 | soft, a zip code stored as text is legitimate |
| anything else touching string | 0.15 | |

The function is public so `decide.py`'s `datatype_conflict` gate imports the same
definition. Retrieval and gating cannot drift apart.

```
- `Place.country` score=0.789 (bm25=1.0, embedding=0.7226, datatype_prior=1.0, shape_prior=0.0)
- `Organization.headquartered_in` score=0.5909 (bm25=0.8544, embedding=0.6735, datatype_prior=0.15, shape_prior=0.0)
- `Place` score=0.5205 (bm25=0.6855, embedding=0.6453, datatype_prior=0.15, shape_prior=0.0)
- `Organization.name` score=0.4186 (bm25=0.0, embedding=0.6716, datatype_prior=1.0, shape_prior=0.0, aliased_from_twin=1.0)
```

**Reading this output.** Every signal is printed, so any score can be
reconstructed by hand. Check row 1: `0.35(1.0) + 0.40(0.7226) + 0.15(1.0) +
0.10(0.0) = 0.789`. Exactly the printed score.

Row 2 shows the relationship penalty. `Organization.headquartered_in` has strong
lexical and semantic scores (0.8544 and 0.6735, both close to row 1) but
`datatype_prior=0.15` because relationship cards carry no datatype. That single
term is most of the 0.198 gap between rows 1 and 2, and that gap is what the
`retrieval_override` gate later measures.

Row 4 carries `aliased_from_twin=1.0`. That is `Company.name` resolved onto
`Organization.name` at query time. The model never sees the twin as a competing
option, so it cannot split columns across both.

Compare the `employee_count` block, where the asymmetry does its work:

```
- `Organization.size` score=0.6139 (bm25=1.0, embedding=0.6596, datatype_prior=0.0, shape_prior=0.0)
```

A perfect lexical match, dragged to 0.6139 by `datatype_prior=0.0`. Integer
column, string attribute, unconditional hard conflict. Without that term the
score would be 0.762 and the column would have looked like a clean reuse.

---

## Phase 4. Propose

`propose_llm` (`decide.py:347`), once per surviving column.

### 4a. The prompt

Candidates are rendered by `_format_column_candidates` as
`- {card.id} (score; signals): {description}`, plus
`[resolved from near-duplicate concept X]` when aliased. Then the profile digest,
then the decisions already made for earlier columns in this CSV.

The system prompt carries ten numbered policy rules. Several encode fixture traps
directly:

- 3: columns holding the name of another entity are relationships, not string attributes
- 4: two columns describing one sub entity (city plus country) map to a single relationship
- 7: sentinel values (Direct, N/A, Unknown) do not create entities
- 8: escalate only when the answer changes the model
- 9: confidence is the probability a domain expert would agree
- 10: never escalate to ask which of two near duplicate types a column belongs to, retrieval already resolved it

### 4b. Structured output

`response_format: {type: json_schema, strict: true}`, every field required,
`additionalProperties: false`. The model cannot return prose:

```json
{"disposition": "reuse|new_attribute|new_relationship|exclude|escalate",
 "target": null, "new_name": null, "new_datatype": null, "new_range": null,
 "on_type": null, "aligned_with": null,
 "rationale": "...", "confidence": 0.0,
 "question": null, "question_context": null, "options": []}
```

`escalate` is one of the five values, so the model can raise its own question via
`question` and `options`. That is the `llm_escalate` path, separate from the gates.

`--no-llm` swaps only this function for `_propose_heuristic` (`decide.py:383`),
which returns the identical dict shape. Nothing downstream can tell.

**Rule 4 is the one this architecture cannot execute.** A per column call
physically cannot see `hq_city` and `hq_country` together. The rule is in the
prompt, and the harness still fails it. Batching a CSV's columns into one call is
the top item in `WRITEUP.md`'s what is next.

---

## Phase 5. Gates

`_run_gates` (`decide.py:460`) mutates the `Decision` in place. Nine gates. Every
trigger is an external measurement, never the model's self report, except
`low_confidence`, which exists precisely to catch the case where the model does
admit doubt.

| Gate | Priority | Trigger | Default if unanswered |
|---|---|---|---|
| `datatype_conflict` | 0 | reuse where compatibility is 0.0 | exclude |
| `unknown_target` | 0 | target not in ontology, retry once with valid ids listed | exclude |
| `kind_mismatch` | 0 | reuse onto a relationship from a non string or freetext column | exclude |
| `low_confidence` | 1 | confidence below 0.55 | keep proposal |
| `llm_escalate` | 1 | the model chose to ask | keep proposal |
| `near_duplicate` | 2 | new proposal, top candidate at 0.62, or 0.55 if its datatype also conflicts | `reuse:<top>` |
| `retrieval_override` | 2 | reuse target sits 0.15 or more below the top candidate | `reuse:<top>` |
| `score_margin_ambiguous` | 3 | top two within 0.05, both at 0.5 or above | keep proposal |
| `vacuous_source` | 3 | new concept from a column name that means nothing alone | keep proposal |

`vacuous_target` is not in the table because it never escalates. It rewrites the
decision to `new_attribute` and emits a flag.

The dual threshold on `near_duplicate` matters. The bar drops from 0.62 to 0.55
when the top candidate's own datatype conflicts, because a datatype mismatch is
corroborating evidence of sameness: the same concept stored incompatibly. That
lowered bar is what would catch `employee_count` at 0.6139 if
`datatype_conflict` had not already caught it.

```
-- per-column decisions (retrieval + propose + gates) --
  vendor: reuse -> Organization.name confidence=1.00 gates=[]
  homepage_url: reuse -> Organization.website confidence=1.00 gates=[]
  established: reuse -> Organization.founded_year confidence=1.00 gates=[]
  sector: reuse -> Organization.industry confidence=1.00 gates=[]
  hq_city: new_relationship headquartered_in confidence=0.90 gates=['near_duplicate']
  hq_country: reuse -> Organization.headquartered_in confidence=0.95 gates=['retrieval_override']
  employee_count: reuse -> Organization.size confidence=0.95 gates=['datatype_conflict']
```

**Reading this output.** Look at the confidence column: 1.00, 1.00, 1.00, 1.00,
0.90, 0.95, 0.95. The model is equally certain about `vendor -> Organization.name`
(correct) and `employee_count -> Organization.size` (a datatype error). That is
the calibration finding, visible in one column of a table. A gate keyed to
confidence below 0.55 can never fire against this model, which is why
`low_confidence` has never fired in any run.

Every gate that did fire is keyed to something measurable instead:

- `hq_city`: a new proposal with `Place.city` sitting at 0.78, above the 0.62 bar
- `hq_country`: reuse target 0.198 below the top candidate, above the 0.15 bar
- `employee_count`: integer column, string target, compatibility exactly 0.0

`established -> Organization.founded_year` at bm25 1.0 with zero shared characters
is the synonym table earning its keep. No gate needed.

---

## Phase 6. Budget

`apply_escalation_budget` (`decide.py:668`) ranks flagged columns by
`(gate priority, retrieval score margin, column position)` and keeps the top
`--escalation-budget`, default 2. Losers are marked `downgraded_from_escalation`
and fall back to their own proposal.

```
-- escalation budget: 3 flagged, 2 kept (budget=2), 1 downgraded --
```

**Reading this output.** This is the anti over escalation valve, and on this run
it costs accuracy. CSV 1 flagged three columns. `employee_count`
(`datatype_conflict`, priority 0) survives outright. `hq_city` (`near_duplicate`)
and `hq_country` (`retrieval_override`) both sit at priority 2, so the tie breaks
on score margin and column position. `hq_city` wins, `hq_country` is downgraded.

The result is that `hq_country` keeps its wrong mapping,
`reuse -> Organization.headquartered_in`, and the report records it honestly as
`escalated: yes (downgraded)`. The gate detected the defect. The budget suppressed
the question. Same thing happens to `date` in CSV 3.

Giving `retrieval_override` priority 1 would fix the ranking without raising the
budget, on the argument that a wide confident disagreement with retrieval is
stronger evidence than a near duplicate hit. That is not done in this submission.

---

## Phase 7. Ask

`build_question` (`escalate.py:107`) writes for a domain expert who has never seen
the code. `ask` (`escalate.py:196`) resolves in strict precedence: **answers file,
then interactive stdin, then gate default**. Prompting requires both
`--interactive` and a real TTY, and the run prints which of those was missing.

```
--- csv1.q2 (1_vendors.csv :: employee_count) [datatype_conflict] ---
Q: Column 'employee_count' (sample values: ['540', '1230', '88', '2100', '410']) -- the harness proposed 'reuse' -> Organization.size. Is that right, or should it map to one of the candidates below instead?
Why: The column's inferred datatype ('integer') conflicts with the datatype of the reuse target 'Organization.size'. Reusing it as-is would silently store the wrong kind of value.
Candidates:
  - Organization.size (score 0.61)
  - Organization (score 0.44)
  - Organization.founded_year (score 0.41)
Sample values: ['540', '1230', '88', '2100', '410']
Options:
  - reuse:Organization.size
  - new:employee_count
  - exclude
  - other: <type a free-text answer>
Default if unanswered: exclude
```

**Reading this output.** Five parts, each deliberate. `Q` states the proposal so
the answer is a yes or no on something concrete, not an open ended modelling
question. `Why` gives the evidence in domain terms, not code terms. `Candidates`
and `Sample values` let the reader check the harness rather than trust it.
`Options` are machine parseable but always include a free text escape.
`Default if unanswered` is printed **before** the answer, so the reader knows the
cost of walking away.

The `near_duplicate` questions carry a longer `Why` that names the calibration
finding directly:

```
Why: The harness proposed a new concept ('headquartered_in') but an existing one, Place.city, scored 0.78 against it -- close enough that minting a new concept here risks a silent duplicate. This is judged on the retrieval score alone, not the model's self-reported confidence (0.90): confidence on this harness's chosen model runs 0.90-1.00 on nearly everything, so it isn't a reliable signal to override objective evidence with.
```

Defaults differ by gate for a reason. `datatype_conflict` defaults to `exclude`
because the proposed mapping is actively broken, and dropping a column is
recoverable while a silently wrong mapping is not. `near_duplicate` defaults to
`reuse:<top candidate>` because the gate fired precisely because a "new" proposal
looked like a duplicate, and a `reuse` op never adds an ontology member, so it
cannot collide or fail validation.

---

## Phase 8. Incorporate

Answers are parsed as a small grammar: `exclude`, `reuse:<target>`, `new:<...>`,
`keep_original`, or `other:<free text>`. Anything unrecognised is passed back to
`propose_llm` as `human_answer` with "Treat this answer as authoritative context",
producing a second call for that column.

```
A: Do NOT reuse Organization.size. `size` is a different concept: a coarse size band (e.g. 'SMB', 'Enterprise'), which is why it is typed string. `employee_count` is an exact headcount, so add a new attribute `employee_count` of datatype integer on Organization, aligned with https://schema.org/numberOfEmployees. Leave Organization.size unchanged, but flag it as an ontology issue because its description 'Size of the organization' is too vague to distinguish it from headcount.  [answered from file]
```

**Reading this output.** This is the one genuine human answer in the run, replayed
from `answers.json`. It is free text, so it took the `other` path and went back
through the LLM, which turned it into
`new_attribute employee_count:integer on Organization`. The final report row reads
`decided_by: human`.

`classify_source` (`escalate.py:229`) sets `decided_by` to `human` for a real
answer and `rule` for a gate default. Three of this run's five escalations were
resolved by default, so only this one shows `human`.

---

## Phase 9. Patch, apply, report, rebuild

`decision_to_patch_op` (`decide.py:724`) converts each `Decision` to a typed op,
`patch.validate` checks it, `patch.apply(mode)` (`patch.py:220`) applies it to a
**deep copy** so a failed op can never half mutate the live ontology.

```
-- assembling patch --
-- applying patch (mode=auto) --
  10/10 ops applied
-- wrote --
  out/1_vendors.patch.json
  out/1_vendors.report.json
  out/1_vendors.report.md
```

```json
{
  "op": "flag_ontology_issue",
  "target": "Organization / Company",
  "issue": "'Organization' and 'Company' look like near-duplicate types (name_similarity=1.00, attribute_overlap=0.60, score=0.80 >= 0.5); canonical type is 'Organization' (richer: more attributes/relationships and/or descriptions) -- retrieval resolves 'Company' concepts onto it; consider deleting 'Company' from the ontology.",
  "severity": "warning"
}
```

**Reading this output.** `reuse`, `exclude` and `flag_ontology_issue` are recorded
as `applied=True, mutated=False`. They are decisions about columns, not changes to
the schema. That is why `--approve none` still reports "9/10 ops applied": the one
op it skipped was the only mutating one.

Then `run.py:342`:

```python
index.rebuild(build_cards(ontology, embedder=embedder))
```

Every card is recompiled and re embedded, so CSV 2 retrieves against the ontology
CSV 1 just patched. This single line is the entire cross file mechanism the brief
requires, and it is why `--approve none` broke multi CSV runs before the default
changed: with nothing applied, CSV 2 and CSV 3 retrieved against the untouched
seed ontology and decided differently.

---

## Caching

Cuts across every phase. The key is `sha256(system + user + schema)`
(`llm.py:233`), written to `.cache/<hash>.json`. The `tag` is only the json schema
name and is not part of the key.

The lookup happens before the HTTP request (`llm.py:247`), and writes are atomic
(`_atomic_write_json`), so a run killed by a rate limit leaves no half written
entry and a rerun costs nothing for work already done. Embeddings cache per text
(`llm.py:350`), so adding one concept re embeds one card, not the corpus.

```json
"llm_usage": {"calls": 24, "prompt_tokens": 5742, "completion_tokens": 623, "cached_calls": 20}
```

**Reading this output.** 24 calls, 20 served from disk, 4 real network requests.
This run happened right after three new gates were added, and content keying is
why it was nearly free: the per column prompts were byte identical to the previous
run, so they all hit cache. Only the phase 8 re decides minted new keys, because
appending the human answer changes the user text.

---

## Totals for this run

```json
{"columns": 23, "reused": 13, "new": 7, "excluded": 3, "escalated": 5}
```

23 columns across 3 CSVs. 13 mapped onto concepts that already existed, 7 added,
3 excluded as artifacts, 5 escalated of which 1 got a real human answer and 4 took
a documented default. Two of those five were downgraded by the budget before they
could be asked.
