# Write-up

**Core idea.** The LLM only proposes a disposition per column. A fixed table of
deterministic gates decides whether to trust it, force an escalation, or force a
safer fallback. Retrieval (BM25 + embeddings over per-concept cards) keeps the
prompt at O(k) regardless of ontology size, so the model only judges the top-8
candidates it is shown. Every escalation and every silent reuse traces back to a
named gate and a retrieval score, which is what makes the harness auditable.

**Canonical-type resolution, and the bug that motivated it.** The seed ontology
ships an `Organization`/`Company` near-duplicate on purpose, and the first live
run failed it. Retrieval scored `Company.url` at 0.89 against
`Organization.website` at 0.52 for the same column, so columns split across both
twins. I fixed it structurally, not with a prompt tweak. A startup audit already
detected the near-duplicate (name similarity plus attribute overlap), and that
signal now feeds retrieval: the non-canonical twin's cards are aliased onto the
canonical type at query time, so a `Company.url` hit surfaces as
`Organization.website`. The same move kept `notes` off the vacuous `Person.data`.
I demoted it 0.25× in retrieval, so it never reaches the prompt as a candidate.

**The calibration finding.** `gemini-3.1-flash-lite`, the model this submission
ships with, reports 0.90–1.00 confidence on nearly every column, correct or not.
Two of the original gates were confidence-gated: `near_duplicate` kept a "new"
proposal at confidence ≥ 0.85, and `low_confidence` fired below 0.55. Both were
dead. A run with this model produced zero escalations, waving `manufacturer`
through as a new relationship instead of reusing `Product.made_by`, and minting a
duplicate `headquartered_in` for `hq_city`. So I dropped self-reported confidence
as a gate signal. `near_duplicate` now escalates on retrieval score alone (≥0.62,
or ≥0.55 when the candidate's datatype also conflicts), and that lower bar rescues
`employee_count` against the 0.61-scoring `Organization.size`. A new
`score_margin_ambiguous` gate escalates when the top two candidates are within
0.05. The shipped run raises four escalation round-trips, including the
`employee_count` vs. `Organization.size` conflict, resolved by a real human answer
replayed from `answers.json`. A threshold defined in terms of model confidence is
only as good as that confidence is calibrated, and here it isn't.

**Where it's weakest.** The thresholds (0.62, 0.55, 0.05) are hand-tuned guesses.
I picked them by inspecting live retrieval scores on the exact columns being
graded, with no held-out validation set, so they are overfitted by construction.
`--no-llm` tests the deterministic layers but is a much weaker decider. Sample-row
projection replays a few rows per CSV, not real materialization. Nothing persists
across runs. It is also a single-model submission: `gemini-2.5-flash` got
`manufacturer` and `employee_count` right first try, where `gemini-3.1-flash-lite`
needed the gate fix above.

**What's next.** Batch a CSV's columns into one LLM call instead of one per
column: 3–6 calls per run instead of ~24, and cheaper. A model seeing every column
at once should notice `hq_city` and `hq_country` are one `Place`, which per-column
calls structurally cannot. Build the eval corpus `EVAL_PLAN.md` specifies instead
of tuning against the visible fixtures. Swap the flat embedding list for an ANN
index once concept count matters. Make patch approval a reviewable UI, not a CLI
flag.
