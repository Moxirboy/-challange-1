# Eval plan: how I would measure whether this harness is good

*(Prose plan, not built. ~1 page.)*

## The thing being measured

The harness makes one decision per column: reuse / new / exclude / escalate, plus
a target concept when it reuses and a shape when it adds. So the unit of
evaluation is the *(column, decision)* pair, and the unit of regression is the
*ontology after N files*.

## Ground truth

Hand-label a corpus of CSV-against-ontology pairs. For each column: the correct
disposition, the correct target if reuse, and an `ambiguous` boolean for columns
where a competent human would need to ask. Two annotators, adjudicate
disagreements. Disagreement is itself signal: a column two experts label
differently is one the agent should escalate. So inter-annotator disagreement
becomes the escalation gold set rather than being averaged away.

Seed the corpus from public tabular data (data.gov, Kaggle) mapped against a
public vocabulary subset (schema.org). The expensive part is the labelling, not
the data. Then mutate: rename columns to synonyms, abbreviate them
(`employee_count` → `emp_cnt`), inject export artifacts (`_id`, `Unnamed: 4`,
`__v`, `etl_ts`), inject near-duplicate types into the seed ontology, blank out
descriptions, and introduce datatype conflicts. Each mutation targets one failure
mode and labels itself. It is the only way I know to get hundreds of hard cases
without hundreds of hours.

Target scale: ~40 CSVs / ~500 columns, of which ~15% ambiguous. Enough to move a
percentage point meaningfully, small enough to label in a week.

## Metrics

**Reuse precision / recall.** Of columns mapped to an existing concept, what
fraction hit the *correct* concept (precision). Of columns that had a correct
existing target, what fraction were found (recall). Report both: precision alone
is gamed by never reusing, recall alone by reusing everything. Measure recall on
the renamed and abbreviated slice, since that is where retrieval earns its keep.

**Junk leakage.** Export artifacts, surrogate keys and sync metadata that reached
the ontology as concepts, per 100 columns. This should be ~0, and any regression
is a hard fail rather than a percentage. Report it alongside over-exclusion rate,
real domain columns wrongly dropped. The cheap way to score zero leakage is to
exclude everything, so the two always travel as a pair.

**Near-duplicate rate.** After ingesting the corpus, cluster concepts by embedding
and name similarity, then count clusters with >1 member that a human confirms are
the same concept. This captures semantic garbage accumulation, and it is *only*
visible in the accumulated ontology, because each addition looks locally
reasonable. Run it every 10 files to see whether garbage accrues linearly or
explodes.

**Escalation quality.** Three numbers, because "escalation rate" alone is
meaningless:
- *Precision*: of the questions asked, what fraction were on ambiguous columns.
- *Recall*: of the ambiguous columns, what fraction were asked about.
- *Answerability*: give a domain expert who has not seen the code the question text
  plus the CSV, and record whether they can answer without a clarifying question
  back. Subjective and slow, so sample ~30 questions per run. I trust it most: a
  question needing a follow-up wasted a human's turn.
- Plus decision delta: how often the human answer changed the disposition.
  Questions whose answer changes nothing are noise, even on ambiguous columns.

**Calibration.** Bucket decisions by reported confidence and plot observed
accuracy per bucket (reliability diagram / ECE). Every gate threshold is a
function of confidence, so uncalibrated confidence makes the escalation policy
arbitrary. I would check calibration *before* tuning any threshold.

**Cost per column.** LLM calls, tokens, wall time. A harness that escalates less
by spending 10× is not obviously better.

## Catching a change that makes it worse

Freeze the corpus. Run the pipeline on every commit at a fixed model version and
temperature 0. Diff the *decision set* against the previous run, not just the
aggregate scores. A per-column diff (`homepage_url: reuse→new_attribute`) makes a
regression legible in seconds, where a metric moving from 0.91 to 0.89 does not.
Gate CI on zero new junk leakage, no drop >2pts in reuse precision, and no new
near-duplicate clusters. The aggregate scores are a dashboard. The decision diff
is the test.

The LLM is nondeterministic even at temperature 0, so run the ambiguous slice 3×
and report variance. A decision that flips between runs is a latent bug: the gates
are deterministic, so a flip means the gate band is too wide for the model's
noise. Model upgrades get the same treatment as code changes.

## What's hard to measure honestly

- **Expansion quality is not a scalar.** Whether `country_of_origin` should be a
  string attribute or a relationship to `Place` has no single right answer. It
  depends on whether anyone will ever query across it. I would score granularity
  with a human rubric on a small sample and accept the noise.
- **The corpus is synthetic where it matters most.** Mutations generate the failure
  modes I already thought of. The ones I did not are absent by construction. So I
  would keep a small held-out set of real customer CSVs that never informs
  threshold tuning, and read the synthetic-to-real gap as how much the eval is
  fooling me.
- **Escalation answerability has no cheap proxy.** LLM-as-judge on question quality
  correlates with fluency, not with whether a busy human can act on it. I would
  rather sample 30 real answers than automate this badly.
- **Long-horizon drift.** Whether the ontology stays clean after 500 files takes
  500 labelled files to answer directly. The near-duplicate-rate curve over the
  40-file corpus is a proxy, and I would say so rather than claim the harness is
  proven at scale.
