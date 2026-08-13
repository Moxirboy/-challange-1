"""Per-column decision: the LLM proposes, deterministic gates decide.

Spec: DESIGN.md §6.

Step A picks the CSV's subject type (one call). Step B proposes a
disposition per column (one call per non-prefiltered column). Step C is the
deterministic gate table that can keep, escalate, force, or (later, at the
CSV level) budget-downgrade that proposal. `--no-llm` runs the exact same
Step-C gates over a documented heuristic Step A/B, so the gate logic never
branches on whether an LLM is present — only the *proposal* step does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .llm import LLM
from .models import FlagOntologyIssueOp
from .ontology import Ontology
from .profiler import ColumnProfile
from .retrieval import Candidate, ColumnQuery, ConceptIndex, TypeScope, datatype_compatibility

# --------------------------------------------------------------------------
# Gate thresholds. Each is named so the gate table in DESIGN.md §6 maps
# 1:1 onto a constant here — a reviewer should be able to check the numbers
# against the spec without hunting through the function bodies.
# --------------------------------------------------------------------------

NEAR_DUP_SCORE_THRESHOLD = 0.62  # best-candidate score at/above which a "new" proposal looks suspicious
# NOTE: there used to be a NEAR_DUP_KEEP_CONFIDENCE=0.85 escape here ("trust
# self-reported confidence over the near-miss"). Removed 2026-08-11: live runs
# showed gemini-3.1-flash-lite reports 0.90-1.00 confidence on nearly every
# column, which made that escape fire on almost everything and silently waved
# through real near-duplicates (manufacturer -> new relationship instead of
# reusing Product.made_by; hq_city minting a duplicate of headquartered_in).
# Self-reported confidence is not trustworthy as a gate signal -- objective
# retrieval evidence (this score) is. See WRITEUP.md's calibration finding.
NEAR_DUP_DATATYPE_CONFLICT_THRESHOLD = 0.55  # lower bar for near_duplicate when the top candidate's
# datatype actively conflicts with the column's inferred datatype. A datatype mismatch is itself
# independent objective evidence (the same concept, represented incompatibly) -- it doesn't need as
# strong a lexical/semantic score to justify a human check. This is what makes `employee_count`
# (top candidate Organization.size scores 0.61, just under NEAR_DUP_SCORE_THRESHOLD, but is a string
# where employee_count is an integer) escalate instead of silently becoming an unrelated new attribute.
SCORE_MARGIN_AMBIGUOUS_THRESHOLD = 0.05  # top1-top2 gap below which the choice between them is itself unsettled
SCORE_MARGIN_MIN_TOP_SCORE = 0.5  # only treat a tight margin as "ambiguous" when both candidates are
# real signal, not noise -- two mediocre, low-scoring candidates a hair apart just means retrieval
# found nothing good, not that there's a genuine duplicate-vs-new choice to escalate.
LOW_CONFIDENCE_THRESHOLD = 0.55  # below this, the model itself isn't sure enough to act unsupervised
RETRIEVAL_OVERRIDE_GAP = 0.15  # how far below the top candidate a chosen reuse target may sit before the
# model is treated as having overruled retrieval. score_margin_ambiguous catches the opposite case (top
# two too CLOSE to separate); this catches a wide, confident disagreement, which the fixtures showed is
# how this model actually fails. hq_country took Organization.headquartered_in at 0.59 when Place.country
# sat at 0.79 -- a 0.20 gap, far outside the 0.05 margin window, so nothing objected.
DEFAULT_ESCALATION_BUDGET = 2

# Source-side mirror of the ontology-side `vacuous` flag. A column whose name
# carries no domain meaning cannot be mapped from the name alone, and the
# values rarely disambiguate it either (3_crm_export's `date` holds valid dates
# that could be a signup, a last-contact, or a status-change date). Names are
# matched whole, after lowercasing, so `start_date` and `updated_at` are not hit.
# Kept tight on purpose: over-escalation is penalised as hard as
# under-escalation, so `notes` and `code` are NOT here. "Notes" does describe
# its own contents; "date" does not say date of what.
VACUOUS_COLUMN_NAMES: frozenset[str] = frozenset(
    {"date", "time", "value", "amount", "number", "data", "type", "status", "info", "detail", "details", "field", "misc", "other", "flag"}
)

# --no-llm Step A only (see decide_subject_type): a much lower bar than
# NEAR_DUP_SCORE_THRESHOLD because a type card's score is diluted by
# corpus-wide BM25 normalisation against much more concentrated attribute
# cards -- see the long comment at its one call site for the empirical basis.
SUBJECT_TYPE_MIN_SCORE = 0.05

# Ranking priority for the budget gate: lower number = higher priority = more
# likely to survive the budget cut. datatype_conflict is the canonical
# "silently wrong" case (§6), so it always outranks the other two.
GATE_PRIORITY: dict[str, int] = {
    "datatype_conflict": 0,
    "unknown_target": 0,
    "kind_mismatch": 0,  # same band as datatype_conflict: both are "this mapping is structurally broken"
    "low_confidence": 1,
    "retrieval_override": 2,  # same band as near_duplicate: both are "the model disagrees with retrieval"
    "near_duplicate": 2,
    "score_margin_ambiguous": 3,  # weakest objective signal of the three -- yields to a real near-dup hit
    "vacuous_source": 3,  # weak: it flags an unanswerable name, not a demonstrated conflict
}


@dataclass
class SubjectTypeDecision:
    """Step A output (DESIGN.md §6)."""

    subject_type: str
    is_new: bool
    new_type_description: str | None
    rationale: str
    confidence: float


@dataclass
class Decision:
    """The full, gated per-column decision. Not declared verbatim anywhere
    in DESIGN.md §1-4 (it belongs to decide.py, §6), so its shape is ours —
    but every field maps onto something the §6/§9 prose explicitly asks for.
    """

    column: str
    position: int
    disposition: str  # reuse | new_attribute | new_relationship | exclude | escalate
    target: str | None = None
    new_name: str | None = None
    new_datatype: str | None = None
    new_range: str | None = None
    on_type: str | None = None
    aligned_with: str | None = None
    description: str = ""
    rationale: str = ""
    confidence: float = 0.0
    question: str | None = None
    question_context: str | None = None
    options: list[str] = field(default_factory=list)
    decided_by: str = "llm"  # "llm" | "rule" | "human"

    # Step C bookkeeping, all part of the auditable gate trail (§6).
    gates_fired: list[str] = field(default_factory=list)
    escalated: bool = False
    escalation_gate: str | None = None  # highest-priority gate that caused the escalation
    downgraded_from_escalation: bool = False
    score_margin: float | None = None  # top1 - top2 candidate score, for budget ranking
    near_dup_datatype_conflict: bool = False  # near_duplicate fired via the lowered
    # datatype-conflict bar (NEAR_DUP_DATATYPE_CONFLICT_THRESHOLD), i.e. the top
    # candidate's own datatype conflicts with the column's -- escalate.py reads this
    # to pick a safe unanswered-default: reusing that candidate would be exactly the
    # silently-wrong mapping the gate exists to prevent, so it must not be the default.

    # The pre-gate proposal, kept verbatim so a budget downgrade (or an
    # "unanswered, use the default" escalation outcome) can fall back to it
    # without re-calling the LLM.
    proposal: dict = field(default_factory=dict)

    # Retrieval context, carried through to report.py.
    candidates: list[Candidate] = field(default_factory=list)

    # FlagOntologyIssueOp(s) raised by a gate while deciding this column
    # (currently only `vacuous_target`). run.py folds these into the CSV's
    # patch alongside whatever decision.py + AddTypeOp etc. produce.
    emitted_flags: list[FlagOntologyIssueOp] = field(default_factory=list)


# --------------------------------------------------------------------------
# System prompts (verbatim intent from §6).
# --------------------------------------------------------------------------

_SYSTEM_PROMPT_SUBJECT_TYPE = """You are mapping a CSV file onto an existing ontology of entity types.
Decide which single entity type each row of this CSV primarily describes (the "subject type").
Rules:
- Prefer an existing type over inventing a new one. A different surface name for the same real-world
  concept (e.g. "Company" vs "Organization") is not a new type.
- Only propose a new type when the retrieved candidates are all a poor fit for what the rows describe.
- Be calibrated: confidence should reflect the probability a domain expert would agree with your pick."""

_SYSTEM_PROMPT_COLUMN_DECISION = """You are mapping one CSV column onto an ontology, one column at a time.
For each column choose exactly one disposition: reuse, new_attribute, new_relationship, exclude, or escalate.
Policy (apply all of these):
1. Prefer reuse. A different surface name is not a new concept.
2. Never propose a concept that is a near-synonym of a retrieved candidate.
3. Columns holding the name of another entity are relationships, not string attributes.
4. Two columns describing one sub-entity (e.g. city + country) map to a single relationship to that
   entity, not two flat attributes.
5. Export artifacts, surrogate keys and sync metadata are excluded, not modelled.
6. Never reuse a concept whose description is vacuous or placeholder; flag it instead.
7. Sentinel values that are not entities (Direct, N/A, None, Unknown) do not create entities — note
   them in the rationale rather than proposing a relationship just because a column looks name-like.
8. Escalate only when the answer changes the model and neither the column name, the values, nor the
   ontology settle it. Do not escalate what a defensible default covers; state the default in the
   rationale instead.
9. confidence is the probability a domain expert would agree with your disposition. Be calibrated.
10. The subject type for this CSV has already been decided (see "Subject type" above) -- prefer
    concepts owned by that subject type (or a type it relates to) over concepts on an unrelated type.
    Never escalate merely to ask which of two near-duplicate types (e.g. a type and a near-duplicate
    twin, such as "Organization" vs "Company") a column belongs to: retrieval has already resolved
    each twin's concepts onto the canonical type for you -- a candidate marked "resolved from
    near-duplicate concept ..." below is that resolution, not a live choice between two types."""

_SUBJECT_TYPE_SCHEMA = {
    "type": "object",
    "properties": {
        "subject_type": {"type": "string"},
        "is_new": {"type": "boolean"},
        "new_type_description": {"type": ["string", "null"]},
        "rationale": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["subject_type", "is_new", "new_type_description", "rationale", "confidence"],
    "additionalProperties": False,
}

_COLUMN_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "disposition": {
            "type": "string",
            "enum": ["reuse", "new_attribute", "new_relationship", "exclude", "escalate"],
        },
        "target": {"type": ["string", "null"]},
        "new_name": {"type": ["string", "null"]},
        "new_datatype": {"type": ["string", "null"]},
        "new_range": {"type": ["string", "null"]},
        "on_type": {"type": ["string", "null"]},
        "aligned_with": {"type": ["string", "null"]},
        "rationale": {"type": "string"},
        "confidence": {"type": "number"},
        "question": {"type": ["string", "null"]},
        "question_context": {"type": ["string", "null"]},
        "options": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "disposition",
        "target",
        "new_name",
        "new_datatype",
        "new_range",
        "on_type",
        "aligned_with",
        "rationale",
        "confidence",
        "question",
        "question_context",
        "options",
    ],
    "additionalProperties": False,
}


def _tag(*parts: str) -> str:
    """Sanitise a chat_json `tag` (used as a schema name / cache label)."""
    joined = "_".join(parts)
    return re.sub(r"[^a-zA-Z0-9_]+", "_", joined)[:80]


# --------------------------------------------------------------------------
# Step A — subject type.
# --------------------------------------------------------------------------


def _format_type_candidates(candidates: list[Candidate]) -> str:
    lines = []
    for c in candidates:
        if c.card.kind != "type":
            continue
        lines.append(f"- {c.card.name} (score {c.score:.2f}): {c.card.description}")
    return "\n".join(lines) if lines else "(no close type candidates)"


def decide_subject_type(
    csv_name: str,
    profile,  # profiler.CsvProfile
    index: ConceptIndex,
    llm: LLM | None = None,
) -> SubjectTypeDecision:
    """Step A (§6): pick the CSV's subject type, one LLM call per CSV."""
    header = getattr(profile, "raw_header", [])
    header_query = ColumnQuery(
        text=f"csv {csv_name} columns: {', '.join(header)}",
        tokens=list(dict.fromkeys(tok for col in header for tok in re.split(r"[^a-zA-Z0-9]+", col.lower()) if tok)),
        datatype="string",
        shape=None,
        freetext=False,
    )
    type_candidates = [c for c in index.search(header_query, k=20) if c.card.kind == "type"][:5]

    if llm is not None:
        # Best-effort: field name for "first 3 raw rows" isn't pinned down
        # verbatim in DESIGN.md §3's prose, so we defend with getattr rather
        # than assume and crash a real LLM run over a naming mismatch.
        sample_rows = getattr(profile, "sample_rows", [])
        user = (
            f"CSV file: {csv_name}\n"
            f"Header: {header}\n"
            f"Sample rows: {sample_rows}\n\n"
            f"Candidate existing types (top {len(type_candidates)} by retrieval score):\n"
            f"{_format_type_candidates(type_candidates)}"
        )
        result = llm.chat_json(
            system=_SYSTEM_PROMPT_SUBJECT_TYPE,
            user=user,
            schema=_SUBJECT_TYPE_SCHEMA,
            tag=_tag("subject_type", csv_name),
        )
        return SubjectTypeDecision(
            subject_type=result["subject_type"],
            is_new=bool(result["is_new"]),
            new_type_description=result.get("new_type_description"),
            rationale=result["rationale"],
            confidence=float(result["confidence"]),
        )

    # --no-llm heuristic (documented deviation from §10, which only spells
    # out the *column* heuristic): take the top retrieved type card, ranked
    # against the *other type cards* rather than against an absolute bar.
    #
    # NEAR_DUP_SCORE_THRESHOLD (0.62) is calibrated for column-level reuse,
    # where the candidate pool search normalises BM25 against attribute/
    # relationship cards that are short and topically concentrated. A type
    # card's text ("Name — description | attributes: a, b, c...") is much
    # longer and more diffuse, so even the *correct* type card's BM25 signal
    # gets diluted well below 0.62 once it's normalised against that same
    # corpus-wide max (verified empirically: on the fixtures, the correct
    # type is always top-1 among type cards, at raw scores of ~0.29-0.43 --
    # comfortably separated from the runner-up, just never near 0.62). So
    # the right question for Step A isn't "is the top type card confident in
    # absolute terms" but "is there any real signal at all" -- SUBJECT_TYPE_MIN_SCORE
    # is deliberately a much lower bar than NEAR_DUP_SCORE_THRESHOLD for that reason.
    if type_candidates and type_candidates[0].score >= SUBJECT_TYPE_MIN_SCORE:
        top = type_candidates[0]
        return SubjectTypeDecision(
            subject_type=top.card.name,
            is_new=False,
            new_type_description=None,
            rationale=f"--no-llm heuristic: top type candidate ({top.card.name}) scored {top.score:.2f}, "
            f"clearly the best-matching existing type.",
            # Fixed, moderate confidence: as with the column heuristic, this
            # decider was never calibrated to output a probability -- the
            # raw BM25-only score isn't meaningful as one (see note above).
            confidence=0.7,
        )
    fallback_name = "".join(part.capitalize() for part in re.split(r"[^a-zA-Z0-9]+", csv_name.rsplit(".", 1)[0]) if part)
    fallback_name = re.sub(r"^\d+", "", fallback_name) or "UnknownType"
    return SubjectTypeDecision(
        subject_type=fallback_name,
        is_new=True,
        new_type_description=f"Heuristically inferred from {csv_name}; no existing type scored >= {SUBJECT_TYPE_MIN_SCORE}.",
        rationale="--no-llm heuristic: no close type candidate; inventing a type from the file name.",
        confidence=0.5,
    )


# --------------------------------------------------------------------------
# Step B — per-column proposal (LLM branch and --no-llm heuristic branch).
# --------------------------------------------------------------------------


def _format_column_candidates(candidates: list[Candidate]) -> str:
    lines = []
    for c in candidates:
        signals = ", ".join(f"{k}={v:.2f}" for k, v in c.signals.items())
        # Surface alias resolution (fix plan point 2) directly in the
        # prompt: this is what makes policy rule #10 ("don't escalate to
        # ask which twin") actionable rather than just aspirational -- the
        # model sees explicitly that the near-duplicate question is already
        # answered, not merely told not to ask it.
        alias_note = f" [resolved from near-duplicate concept {c.aliased_from}]" if c.aliased_from else ""
        lines.append(f"- {c.card.id} (score {c.score:.2f}; {signals}){alias_note}: {c.card.description}")
    return "\n".join(lines) if lines else "(no candidates retrieved)"


def _format_prior_decisions(prior: list["Decision"]) -> str:
    if not prior:
        return "(none yet)"
    lines = []
    for d in prior:
        lines.append(f"- {d.column}: {d.disposition} -> {d.target or d.new_name} ({d.rationale})")
    return "\n".join(lines)


def propose_llm(
    profile: ColumnProfile,
    csv_name: str,
    subject: SubjectTypeDecision,
    candidates: list[Candidate],
    prior: list["Decision"],
    llm: LLM,
    human_answer: str | None = None,
    retry_note: str | None = None,
) -> dict:
    user = (
        f"CSV: {csv_name}\n"
        f"Subject type: {subject.subject_type} (is_new={subject.is_new})\n"
        f"Column: {profile.name} (position {profile.position})\n"
        f"Inferred datatype: {profile.inferred_datatype}; shape: {profile.shape}\n"
        f"Uniqueness: {profile.uniqueness:.2f}; null_rate: {profile.null_rate:.2f}\n"
        f"Sample values: {profile.samples}\n\n"
        f"Candidates (top {len(candidates)}):\n{_format_column_candidates(candidates)}\n\n"
        f"Decisions already made for earlier columns in this CSV:\n{_format_prior_decisions(prior)}"
    )
    if retry_note:
        user += f"\n\nRETRY: {retry_note}"
    if human_answer:
        user += (
            f"\n\nA human reviewer was asked a clarifying question about this exact column and answered:\n"
            f"{human_answer}\n"
            f"Treat this answer as authoritative context."
        )
    return llm.chat_json(
        system=_SYSTEM_PROMPT_COLUMN_DECISION,
        user=user,
        schema=_COLUMN_DECISION_SCHEMA,
        tag=_tag("column", csv_name, profile.name),
    )


def _propose_heuristic(profile: ColumnProfile, subject: SubjectTypeDecision, candidates: list[Candidate]) -> dict:
    """--no-llm decider (§10): top candidate >= 0.62 -> reuse; else new_attribute.

    This exists purely to exercise the deterministic layers (profiling,
    retrieval, gates, patch, report) end-to-end without network or spend —
    it is not a second engine, so it deliberately never proposes
    new_relationship, exclude (prefiltering already handles that), or
    escalate on its own; the gates downstream can still escalate it.
    """
    if candidates and candidates[0].score >= NEAR_DUP_SCORE_THRESHOLD:
        top = candidates[0]
        # Report-visible trace of alias resolution (fix plan point 2):
        # report.py (out of scope for this fix) only serialises
        # Candidate.card/.score/.signals, never the new .aliased_from field,
        # so this is folded into `rationale` -- a field report.py already
        # writes to both report.json and report.md -- rather than depending
        # on a report.py change to become visible to a reviewer.
        alias_note = f" (retrieval resolved this from near-duplicate concept {top.aliased_from})" if top.aliased_from else ""
        return {
            "disposition": "reuse",
            "target": top.card.id,
            "new_name": None,
            "new_datatype": None,
            "new_range": None,
            "on_type": None,
            "aligned_with": None,
            "rationale": f"--no-llm heuristic: top candidate {top.card.id} scored {top.score:.2f} >= {NEAR_DUP_SCORE_THRESHOLD}.{alias_note}",
            "confidence": min(1.0, top.score),
            "question": None,
            "question_context": None,
            "options": [],
        }
    # Fixed, moderate confidence: the heuristic decider was never calibrated
    # to output a probability, so a flat value (above the low_confidence
    # gate's 0.55 threshold) avoids the offline path drowning in
    # escalations that reflect nothing but the heuristic's ignorance.
    return {
        "disposition": "new_attribute",
        "target": None,
        "new_name": profile.name.lower(),
        "new_datatype": profile.inferred_datatype,
        "new_range": None,
        "on_type": subject.subject_type,
        "aligned_with": None,
        "rationale": "--no-llm heuristic: no candidate scored >= threshold; proposing a new attribute on the subject type.",
        "confidence": 0.6,
        "question": None,
        "question_context": None,
        "options": [],
    }


# --------------------------------------------------------------------------
# Step C — deterministic gates.
# --------------------------------------------------------------------------


def _score_margin(candidates: list[Candidate]) -> float:
    if len(candidates) >= 2:
        return candidates[0].score - candidates[1].score
    if len(candidates) == 1:
        return candidates[0].score
    return 0.0


def _resolve_attribute_datatype(target: str, ontology: Ontology) -> str | None:
    if "." not in target:
        return None
    type_name, attr_name = target.split(".", 1)
    attr = ontology.attr(type_name, attr_name)
    return attr.datatype if attr is not None else None


def _target_is_vacuous(target: str, index: ConceptIndex) -> bool:
    return any(c.id == target and c.vacuous for c in index.cards)


def _run_gates(
    decision: Decision,
    profile: ColumnProfile,
    ontology: Ontology,
    index: ConceptIndex,
    llm: LLM | None,
    csv_name: str,
    subject: SubjectTypeDecision,
    prior: list["Decision"],
) -> None:
    """Mutates `decision` in place, applying every gate in DESIGN.md §6's
    table except `budget` (that one runs once per CSV, after every column
    has been gated — see apply_escalation_budget)."""

    escalation_reasons: list[str] = []

    # --- near_duplicate ---------------------------------------------------
    # Gates on objective retrieval evidence only -- no confidence escape (see
    # the constant-block comment above). A best-candidate score at/above
    # NEAR_DUP_SCORE_THRESHOLD always escalates; the bar drops to
    # NEAR_DUP_DATATYPE_CONFLICT_THRESHOLD when that top candidate's own
    # datatype conflicts with the column's, since the conflict is itself
    # corroborating evidence that this is the same concept under another name.
    if decision.disposition in {"new_attribute", "new_relationship"} and decision.candidates:
        top = decision.candidates[0]
        best = top.score
        conflicting_datatype = False
        if top.card.kind == "attribute":
            candidate_dtype = _resolve_attribute_datatype(top.card.id, ontology)
            if candidate_dtype is not None:
                conflicting_datatype = datatype_compatibility(profile.inferred_datatype, profile.freetext, candidate_dtype) == 0.0
        threshold = NEAR_DUP_DATATYPE_CONFLICT_THRESHOLD if conflicting_datatype else NEAR_DUP_SCORE_THRESHOLD
        if best >= threshold:
            decision.gates_fired.append("near_duplicate")
            decision.near_dup_datatype_conflict = conflicting_datatype
            reason = (
                f"datatype conflict against {top.card.id} at score {best:.2f} (>= {threshold} lowered bar)"
                if conflicting_datatype
                else f"score {best:.2f} >= {threshold}"
            )
            decision.rationale += (
                f" [near_duplicate gate: escalating -- proposal was '{decision.disposition}' but {top.card.id} "
                f"is a near-duplicate ({reason}); objective retrieval evidence outranks self-reported "
                f"confidence ({decision.confidence:.2f})]"
            )
            escalation_reasons.append("near_duplicate")

    # --- score_margin_ambiguous --------------------------------------------
    # Independent of what was proposed: when the top two candidates for a
    # "new" proposal are within a hair of each other's score, that closeness
    # is itself objective evidence the choice isn't settled -- regardless of
    # confidence. Restricted to new_attribute/new_relationship (not reuse):
    # calibration on the fixtures showed clean reuses (e.g. contact_name ->
    # Person.full_name) can have a tight margin against an unrelated card
    # from a different type just from lexical noise ("name" appears on both);
    # that isn't the kind of ambiguity this gate is meant to catch.
    if (
        decision.disposition in {"new_attribute", "new_relationship"}
        and len(decision.candidates) >= 2
        and decision.candidates[0].score >= SCORE_MARGIN_MIN_TOP_SCORE
        and (decision.candidates[0].score - decision.candidates[1].score) < SCORE_MARGIN_AMBIGUOUS_THRESHOLD
        and "near_duplicate" not in decision.gates_fired
    ):
        top, runner_up = decision.candidates[0], decision.candidates[1]
        decision.gates_fired.append("score_margin_ambiguous")
        decision.rationale += (
            f" [score_margin gate: top candidates {top.card.id} ({top.score:.2f}) and {runner_up.card.id} "
            f"({runner_up.score:.2f}) are within {SCORE_MARGIN_AMBIGUOUS_THRESHOLD} of each other -- "
            f"genuine retrieval ambiguity]"
        )
        escalation_reasons.append("score_margin_ambiguous")

    # --- datatype_conflict --------------------------------------------------
    if decision.disposition == "reuse" and decision.target:
        target_datatype = _resolve_attribute_datatype(decision.target, ontology)
        if target_datatype is not None:
            compat = datatype_compatibility(profile.inferred_datatype, profile.freetext, target_datatype)
            if compat == 0.0:
                decision.gates_fired.append("datatype_conflict")
                escalation_reasons.append("datatype_conflict")

    # --- kind_mismatch ------------------------------------------------------
    # A reuse target is either an attribute (literal-valued) or a relationship
    # (entity-valued). Reusing a relationship for a column of plain literals
    # produces a mapping that cannot be materialised: the value is not an
    # entity reference. datatype_conflict cannot see this, because it only
    # resolves attribute datatypes and returns None for a relationship.
    # Deliberately narrow: it keys on datatype, NOT on profile.entity_like.
    # entity_like requires uniqueness <= 0.8, so on small fixtures a real
    # entity column reads as non-entity (2_product_catalog's `manufacturer` is
    # 6 distinct in 7 rows = 0.86) while a repeating literal column reads as
    # entity-like (`hq_country` = 0.62). Keying on it produced a false positive
    # on the one relationship reuse in the fixtures that is actually correct.
    # A non-string or free-text column, by contrast, can never be an entity
    # reference, so this version has no false positives to trade away.
    if decision.disposition == "reuse" and decision.target and "." in decision.target:
        type_name, concept_name = decision.target.split(".", 1)
        target_is_relationship = ontology.rel(type_name, concept_name) is not None
        cannot_be_a_reference = profile.inferred_datatype != "string" or profile.freetext
        if target_is_relationship and cannot_be_a_reference:
            decision.gates_fired.append("kind_mismatch")
            decision.rationale += (
                f" [kind_mismatch gate: '{decision.target}' is a relationship (entity-valued) but column "
                f"'{profile.name}' holds literals (inferred {profile.inferred_datatype}, freetext="
                f"{profile.freetext}, samples {profile.samples[:3]}); reusing it would emit an "
                f"unresolvable entity reference]"
            )
            escalation_reasons.append("kind_mismatch")

    # --- retrieval_override -------------------------------------------------
    # The model was shown ranked candidates and picked one well below the top.
    # A confident disagreement with retrieval is worth a human look; note this
    # is the mirror image of score_margin_ambiguous, which fires when the top
    # two are too close rather than when the chosen one is too far down.
    if decision.disposition == "reuse" and decision.target and len(decision.candidates) >= 2:
        top = decision.candidates[0]
        chosen = next((c for c in decision.candidates if c.card.id == decision.target), None)
        if chosen is not None and chosen.card.id != top.card.id:
            gap = top.score - chosen.score
            if gap >= RETRIEVAL_OVERRIDE_GAP:
                decision.gates_fired.append("retrieval_override")
                decision.rationale += (
                    f" [retrieval_override gate: model chose {chosen.card.id} ({chosen.score:.2f}) over "
                    f"top-ranked {top.card.id} ({top.score:.2f}), a gap of {gap:.2f} >= {RETRIEVAL_OVERRIDE_GAP}]"
                )
                escalation_reasons.append("retrieval_override")

    # --- vacuous_source -----------------------------------------------------
    # Mirror of vacuous_target, on the CSV side. A column named `date` or
    # `status` cannot be mapped from its name, and its values usually do not
    # settle it either. Only fires on a "new" proposal: a confident reuse onto
    # an existing concept means the meaning was recovered from somewhere else.
    if decision.disposition in {"new_attribute", "new_relationship"} and profile.name.strip().lower() in VACUOUS_COLUMN_NAMES:
        decision.gates_fired.append("vacuous_source")
        decision.rationale += (
            f" [vacuous_source gate: column name '{profile.name}' carries no domain meaning on its own, "
            f"and adding it as a new concept would put an unexplained field in the ontology]"
        )
        escalation_reasons.append("vacuous_source")

    # --- vacuous_target (forces new_attribute; never escalates) -----------
    if decision.disposition == "reuse" and decision.target and _target_is_vacuous(decision.target, index):
        decision.gates_fired.append("vacuous_target")
        owner_type, concept_name = decision.target.split(".", 1) if "." in decision.target else (subject.subject_type, decision.target)
        decision.emitted_flags.append(
            FlagOntologyIssueOp(
                target=decision.target,
                issue=f"Column '{profile.name}' was about to reuse '{decision.target}', but that concept is vacuous "
                f"(placeholder name/description). Forced to new_attribute instead.",
                severity="warning",
            )
        )
        decision.disposition = "new_attribute"
        decision.on_type = owner_type
        decision.new_name = profile.name.lower()
        decision.new_datatype = profile.inferred_datatype
        decision.target = None
        decision.rationale += " [vacuous_target gate: forced new_attribute, target concept was vacuous]"

    # --- low_confidence -----------------------------------------------------
    if decision.confidence < LOW_CONFIDENCE_THRESHOLD:
        decision.gates_fired.append("low_confidence")
        escalation_reasons.append("low_confidence")

    # --- unknown_target (retry once, then escalate) -------------------------
    if decision.disposition == "reuse" and decision.target and not _resolves_in_ontology(decision.target, ontology):
        if llm is not None:
            restated = ", ".join(c.card.id for c in decision.candidates) or "(no candidates)"
            retried = propose_llm(
                profile,
                csv_name,
                subject,
                decision.candidates,
                prior,
                llm,
                retry_note=f"Your previous reuse target '{decision.target}' does not exist. "
                f"Valid candidate ids are exactly: {restated}. Pick one of these, or choose a different disposition.",
            )
            decision.disposition = retried["disposition"]
            decision.target = retried.get("target")
            decision.new_name = retried.get("new_name")
            decision.new_datatype = retried.get("new_datatype")
            decision.new_range = retried.get("new_range")
            decision.on_type = retried.get("on_type")
            decision.aligned_with = retried.get("aligned_with")
            decision.rationale = retried.get("rationale", decision.rationale)
            decision.confidence = float(retried.get("confidence", decision.confidence))
            decision.proposal = dict(retried)

        still_unknown = decision.disposition == "reuse" and decision.target and not _resolves_in_ontology(decision.target, ontology)
        if still_unknown:
            decision.gates_fired.append("unknown_target")
            escalation_reasons.append("unknown_target")

    # --- roll up escalation ---------------------------------------------
    if escalation_reasons:
        decision.escalated = True
        # Highest-priority gate wins the ranking key used by the budget gate.
        decision.escalation_gate = min(escalation_reasons, key=lambda g: GATE_PRIORITY.get(g, 99))


def _resolves_in_ontology(target: str, ontology: Ontology) -> bool:
    if "." not in target:
        return ontology.get(target) is not None
    type_name, concept_name = target.split(".", 1)
    return ontology.attr(type_name, concept_name) is not None or ontology.rel(type_name, concept_name) is not None


def decide_column(
    profile: ColumnProfile,
    csv_name: str,
    subject: SubjectTypeDecision,
    index: ConceptIndex,
    ontology: Ontology,
    prior_decisions: list[Decision],
    llm: LLM | None = None,
) -> Decision:
    """Steps B + C for one column."""
    query = ColumnQuery.from_profile(profile)
    candidates = index.search(query, k=8, scope=TypeScope(subject.subject_type))

    if llm is not None:
        proposal = propose_llm(profile, csv_name, subject, candidates, prior_decisions, llm)
        decided_by = "llm"
    else:
        proposal = _propose_heuristic(profile, subject, candidates)
        decided_by = "rule"

    decision = Decision(
        column=profile.name,
        position=profile.position,
        disposition=proposal["disposition"],
        target=proposal.get("target"),
        new_name=proposal.get("new_name"),
        new_datatype=proposal.get("new_datatype"),
        new_range=proposal.get("new_range"),
        on_type=proposal.get("on_type"),
        aligned_with=proposal.get("aligned_with"),
        rationale=proposal.get("rationale", ""),
        confidence=float(proposal.get("confidence", 0.0)),
        question=proposal.get("question"),
        question_context=proposal.get("question_context"),
        options=list(proposal.get("options") or []),
        decided_by=decided_by,
        candidates=candidates,
        score_margin=_score_margin(candidates),
        proposal=dict(proposal),
    )

    # If the LLM itself already chose "escalate", that's a direct escalation
    # (not gate-triggered) — still route it through the same downstream
    # machinery (budget, escalate.py) by marking it escalated with a
    # synthetic gate name so it participates in ranking.
    if decision.disposition == "escalate":
        decision.escalated = True
        decision.escalation_gate = decision.escalation_gate or "llm_escalate"
        GATE_PRIORITY.setdefault("llm_escalate", 1)  # same priority band as low_confidence

    _run_gates(decision, profile, ontology, index, llm, csv_name, subject, prior_decisions)
    return decision


# --------------------------------------------------------------------------
# Budget gate (§6, applied once per CSV over every gated column decision).
# --------------------------------------------------------------------------


def apply_escalation_budget(decisions: list[Decision], budget: int) -> list[Decision]:
    """Keep at most `budget` escalations per CSV; downgrade the rest.

    Ranking key (ascending — first `budget` entries win): (gate_priority,
    score_margin, column position). Smaller margin means the top candidate
    and the runner-up were closer together, i.e. the retrieval signal itself
    was more ambiguous — that is worth a human's time more than a column
    where one candidate clearly dominated. Column position is the final,
    purely mechanical tie-break, making the sort total and therefore
    deterministic across reruns.
    """
    escalated = [d for d in decisions if d.escalated]
    if len(escalated) <= budget:
        return decisions

    ranked = sorted(
        escalated,
        key=lambda d: (GATE_PRIORITY.get(d.escalation_gate or "", 99), d.score_margin or 0.0, d.position),
    )
    kept_ids = {id(d) for d in ranked[:budget]}

    for d in escalated:
        if id(d) in kept_ids:
            continue
        # Downgrade: fall back to the LLM/heuristic's own best non-escalate
        # guess (the pre-gate proposal), penalising confidence since it is
        # now an unsupervised guess rather than a human-confirmed answer.
        d.gates_fired.append("budget")
        d.escalated = False
        d.downgraded_from_escalation = True
        proposal = d.proposal or {}
        fallback_disposition = proposal.get("disposition", "escalate")
        if fallback_disposition == "escalate":
            # The LLM's own best guess was itself "escalate" with nothing
            # else to fall back on -> the safest mechanical default is
            # exclude, so a downgraded column never silently mutates the
            # ontology on a guess the model refused to make.
            fallback_disposition = "exclude"
        d.disposition = fallback_disposition
        d.target = proposal.get("target")
        d.new_name = proposal.get("new_name")
        d.new_datatype = proposal.get("new_datatype")
        d.new_range = proposal.get("new_range")
        d.on_type = proposal.get("on_type")
        d.aligned_with = proposal.get("aligned_with")
        d.confidence = float(proposal.get("confidence", d.confidence)) * 0.8
        d.rationale = (proposal.get("rationale", d.rationale)) + " [downgraded_from_escalation: over the CSV's escalation budget]"

    return decisions


# --------------------------------------------------------------------------
# Decision -> PatchOp (used by run.py after escalation is fully resolved).
# --------------------------------------------------------------------------


def decision_to_patch_op(decision: Decision):
    """Convert a final (post-escalation, post-budget) Decision into the
    corresponding patch op, or None for dispositions that don't produce one
    ("escalate" should never reach here in a correct run — see run.py)."""
    from .models import AddAttributeOp, AddRelationshipOp, ExcludeOp, ReuseOp

    if decision.disposition == "reuse":
        return ReuseOp(
            source_column=decision.column,
            target=decision.target,
            rationale=decision.rationale,
            confidence=decision.confidence,
        )
    if decision.disposition == "new_attribute":
        return AddAttributeOp(
            on_type=decision.on_type,
            name=decision.new_name,
            datatype=decision.new_datatype or "string",
            rationale=decision.rationale,
            confidence=decision.confidence,
            aligned_with=decision.aligned_with,
            source_column=decision.column,
            description=decision.description,
        )
    if decision.disposition == "new_relationship":
        return AddRelationshipOp(
            on_type=decision.on_type,
            name=decision.new_name,
            range=decision.new_range,
            rationale=decision.rationale,
            confidence=decision.confidence,
            aligned_with=decision.aligned_with,
            source_column=decision.column,
            description=decision.description,
        )
    if decision.disposition == "exclude":
        return ExcludeOp(source_column=decision.column, rationale=decision.rationale)
    return None  # "escalate" (should be resolved before this is called)
