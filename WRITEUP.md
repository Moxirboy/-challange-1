# Write-up

**Core idea.** The LLM only *proposes* a disposition per column; a fixed table of
deterministic gates decides whether to trust it, force an escalation, or force a
safer fallback. Retrieval (BM25 + embeddings over per-concept cards) keeps the
prompt at O(k) regardless of ontology size, so the model never has to "remember"
the whole schema — it only ever judges the top-8 candidates it's actually shown.
This split is what makes the harness's behavior auditable: every escalation and
every silent reuse traces back to a named gate and a retrieval score, not to
"the model felt like it."

**Canonical-type resolution, and the bug that motivated it.** The seed ontology
ships an `Organization`/`Company` near-duplicate on purpose. The first live run
genuinely failed this: retrieval scored `Company.url` at 0.89 against
`Organization.website` at 0.52 for the same column, so columns split across both
twins — the exact semantic-garbage outcome the task is scored on. The fix wasn't a
prompt tweak; it was structural. A startup audit already detected the near-dup
(name similarity + attribute overlap); that signal now feeds retrieval directly —
the non-canonical twin's cards are aliased onto the canonical type at query time,
so a `Company.url` hit surfaces *as* `Organization.website`, not as a competitor.
A real bug, found and fixed with evidence, is more credible here than a story
where it never happened. The same "structure over prompting" instinct kept `notes`
off the vacuous `Person.data` (demoted 0.25× in retrieval, not just told not to
reuse it) — the LLM never even sees it as a live candidate.

**The calibration finding.** This is the strongest result in the project.
`gemini-3.1-flash-lite` — the model this submission actually ships with, because
it's the only model/key combination that completed a full run inside the free-tier
quota after five API keys and four other models (`gemini-3.6-flash`,
`gemini-2.5-flash`, `gemini-2.5-flash-lite`, `gemini-3.5-flash`) were exhausted
trying — reports
0.90–1.00 confidence on nearly every column, correct or not. Two of the original
gates were confidence-gated (`near_duplicate` kept a "new" proposal if confidence
≥ 0.85; `low_confidence` fired below 0.55), and both were effectively dead: a run
with this model produced **zero escalations**, silently waving `manufacturer`
through as a new relationship instead of reusing `Product.made_by`, and minting a
duplicate `headquartered_in` for `hq_city`. The fix was to stop treating
self-reported confidence as a gate signal at all. `near_duplicate` now escalates
on retrieval score alone (≥0.62, or ≥0.55 when the candidate's datatype also
conflicts — that lower bar is what rescues `employee_count` against the
0.61-scoring `Organization.size`); a new `score_margin_ambiguous` gate escalates
when the top two candidates are within 0.05 of each other. The shipped run now
raises four escalation round-trips, including the canonical `employee_count` vs.
`Organization.size` datatype conflict, resolved by a real, previously-collected
human answer replayed from `answers.json`. This is direct evidence for the eval
plan's calibration argument: a threshold defined in terms of model confidence is
only as trustworthy as that confidence is calibrated, and it silently isn't here.

**Where it's weakest.** Escalation thresholds (0.62, 0.55, 0.05) are hand-tuned
against three fixture CSVs with no held-out validation set — they were chosen by
inspecting live retrieval scores on exactly the columns being graded, which is
overfitting by construction. `--no-llm` is a real deterministic-layer test path
but a much weaker decider than the LLM. Sample-row projection only replays a few
rows per CSV, not real materialization. Nothing persists across runs. It's a
single-model submission, and decision quality visibly differs by model —
`gemini-2.5-flash` got `manufacturer` and `employee_count` right on the first try
where `gemini-3.1-flash-lite` needed the gate fix above.

**What's next.** Batch all of a CSV's columns into one LLM call instead of one
call per column (3–6 calls per run instead of ~24 — cheaper, and a model seeing
every column together should notice `hq_city`+`hq_country` are one `Place`, which
per-column calls structurally cannot). Build the eval corpus `EVAL_PLAN.md`
specifies instead of tuning against the visible fixtures. Swap the flat embedding
list for an ANN index once concept count actually matters. Make patch approval a
real reviewable UI instead of a CLI flag.
