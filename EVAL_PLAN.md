# Eval plan — how I'd measure whether this harness is actually good

*(Prose plan, not built. ~1 page.)*

## The thing being measured

The harness makes one kind of decision, per column: **reuse / new / exclude / escalate**,
plus a target concept when it reuses and a shape when it adds. Everything worth
measuring is a property of that decision and of the ontology that accumulates from a
sequence of them. So the unit of evaluation is the *(column, decision)* pair, and the
unit of regression is the *ontology after N files*.

## Ground truth

Hand-label a corpus of CSV-against-ontology pairs. For each column: the correct
disposition, the correct target if reuse, and an `ambiguous` boolean marking columns
where a competent human would need to ask. Two annotators, adjudicate disagreements;
the disagreement rate is itself the signal — a column two experts label differently is
by definition one the agent should escalate, so **inter-annotator disagreement becomes
the escalation gold set** rather than being averaged away.

Seed the corpus from public tabular data (data.gov, Kaggle) mapped against a public
vocabulary subset (schema.org), because the expensive part is the labelling, not the
data. Then, critically, **mutate**: rename columns to synonyms, abbreviate them
(`employee_count` → `emp_cnt`), inject export artifacts (`_id`, `Unnamed: 4`,
`__v`, `etl_ts`), inject near-duplicate types into the seed ontology, blank out
descriptions, and introduce datatype conflicts. Each mutation targets one failure mode
and gives a labelled example for free — the label is derived from the mutation. This
is the only way I know to get hundreds of hard cases without hundreds of hours.

Target scale: ~40 CSVs / ~500 columns, of which ~15% ambiguous. Enough to move a
percentage point meaningfully; small enough to label in a week.

## Metrics

**Reuse precision / recall.** Of columns the agent mapped to an existing concept, what
fraction hit the *correct* concept (precision); of columns that had a correct existing
target, what fraction were found (recall). Report both — precision alone is gamed by
never reusing, recall alone by reusing everything. Recall must be measured
specifically on the **renamed/abbreviated** slice, since that's where retrieval earns
its keep; a system that only matches exact names scores fine on the easy slice.

**Junk leakage.** Count of export artifacts, surrogate keys and sync metadata that
reached the ontology as concepts, per 100 columns. This should be ~0 and any
regression is a hard fail, not a percentage. Complementary: **over-exclusion rate**,
real domain columns wrongly dropped — the cheap way to score zero leakage is to
exclude everything, so the two are always reported as a pair.

**Near-duplicate rate.** After ingesting the whole corpus, measure semantic redundancy
in the resulting ontology: cluster concepts by embedding and name similarity, count
clusters with >1 member that a human confirms are the same concept. This is the metric
that captures "semantic garbage accumulation", and it is *only* visible at the level of
the accumulated ontology — no per-column metric detects it, because each individual
addition looks locally reasonable. Run it after every 10 files to see whether garbage
accrues linearly or explodes.

**Escalation quality.** Three numbers, because "escalation rate" alone is meaningless:
- *Precision* — of the questions asked, what fraction were on columns labelled ambiguous.
- *Recall* — of the ambiguous columns, what fraction were asked about.
- *Answerability* — give the question text (and only the question text, plus the CSV) to
  a domain expert who has not seen the code, and record whether they can answer it
  without asking a clarifying question back. This is subjective and slow, so sample
  ~30 questions per eval run. It is also the metric I trust most, because a question
  that needs a follow-up is a question that wasted a human's turn.
- Plus **decision delta**: how often the human answer actually changed the disposition.
  Questions whose answer changes nothing are noise even when they're on genuinely
  ambiguous columns.

**Calibration.** Bucket decisions by the confidence the harness reported and plot
observed accuracy per bucket (reliability diagram / ECE). This one matters more than it
looks: every gate threshold in the harness is a function of confidence, so if
confidence is uncalibrated, the entire escalation policy is arbitrary. I'd check
calibration *before* tuning any threshold.

**Cost per column** — LLM calls, tokens, wall time. A harness that escalates less by
spending 10× is not obviously better.

## Catching a change that makes it worse

Freeze the corpus, run the full pipeline on every commit with a fixed model version and
temperature 0, and diff the *decision set* against the previous run — not just the
aggregate scores. A per-column diff (`homepage_url: reuse→new_attribute`) makes a
regression legible in seconds, where a metric that moves from 0.91 to 0.89 does not.
Gate CI on: zero new junk leakage, no drop >2pts in reuse precision, no new
near-duplicate clusters. Treat the aggregate scores as a dashboard and the decision
diff as the actual test.

Because the LLM is nondeterministic even at temperature 0, run the ambiguous slice
3× and report variance; a decision that flips between runs is a latent bug in the gates
(the gates are deterministic, so a flip means the gate band is too wide for the model's
noise). Model upgrades get the same treatment as code changes — same corpus, same diff.

## What's hard to measure honestly

- **Expansion quality is not a scalar.** Whether `country_of_origin` should be a string
  attribute or a relationship to `Place` has no single right answer; it depends on
  whether anyone will ever query across it. I'd score granularity decisions with a
  human rubric on a small sample and accept the noise, rather than pretend a
  ground-truth label exists.
- **The corpus is synthetic where it matters most.** Mutations generate the failure
  modes I already thought of. The failure modes I didn't think of are, by construction,
  absent — so I'd keep a small held-out set of real customer CSVs that never informs
  threshold tuning, and treat a gap between synthetic and real scores as the honest
  measure of how much the eval is fooling me.
- **Escalation answerability has no cheap proxy.** LLM-as-judge on question quality
  correlates with fluency, not with whether a busy human can act on it. I'd rather
  sample 30 real answers than automate this badly.
- **Long-horizon drift.** The real question — does the ontology stay clean after 500
  files? — takes 500 labelled files to answer directly. The near-duplicate-rate curve
  over the 40-file corpus is a proxy for it, and I'd say so out loud rather than
  claiming the harness is proven at scale.
