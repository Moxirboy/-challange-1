"""Human-in-the-loop question/answer round trip.

Spec: DESIGN.md §7.

Questions are batched per CSV -- one round trip per file, not one per
column. `ask()` prints a readable block per question, resolves each answer
from an answers file, interactive stdin, or a documented default, and
`incorporate()` turns an answer back into a final Decision for that column
without ever silently guessing.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass

from .decide import Decision, SubjectTypeDecision, propose_llm
from .llm import LLM
from .ontology import Ontology
from .profiler import ColumnProfile
from .retrieval import ConceptIndex

# Special answer tokens `incorporate()` understands regardless of where the
# answer came from (typed by a human, read from a file, or a gate default).
_KEEP_ORIGINAL = "keep_original"
_EXCLUDE = "exclude"

# Per-gate default answer: what happens if a question goes unanswered.
# low_confidence/score_margin_ambiguous: the model's own guess is
# structurally valid, just uncertain -- falling back to it is the same
# principle the budget gate uses. datatype_conflict/unknown_target: the
# proposed target is actively unsafe (a real type conflict, or doesn't
# exist) -- silently keeping it would apply a broken mapping, so the safe
# default is to drop the column instead. near_duplicate's entry below is a
# fallback only (no candidates retrieved); build_question() overrides it
# with a dynamic "reuse:<top candidate>" whenever candidates exist -- see
# the comment at that call site for why keep_original is unsafe there.
#
# kind_mismatch joins the _EXCLUDE group: the proposed mapping cannot be
# materialised at all, so keeping it is strictly worse than dropping the
# column. retrieval_override falls back to retrieval's own top candidate,
# because the gate fired precisely because the model departed from it and
# nobody arrived to say the model was right. vacuous_source keeps the
# model's proposal: an unexplained column name is a reason to ask, not
# evidence that the proposal is wrong.
_DEFAULT_BY_GATE: dict[str, str] = {
    "near_duplicate": _KEEP_ORIGINAL,
    "low_confidence": _KEEP_ORIGINAL,
    "datatype_conflict": _EXCLUDE,
    "unknown_target": _EXCLUDE,
    "kind_mismatch": _EXCLUDE,
    "retrieval_override": _KEEP_ORIGINAL,
    "vacuous_source": _KEEP_ORIGINAL,
    "llm_escalate": _KEEP_ORIGINAL,
    "score_margin_ambiguous": _KEEP_ORIGINAL,
}


@dataclass
class Question:
    id: str  # "csv1.q1"
    csv: str
    column: str
    gate: str
    question: str  # answerable by someone who did not write the code
    why: str  # what the harness saw and why it cannot decide
    context: dict  # profile digest + top candidates with scores
    options: list[str]  # concrete choices, always including a free-text escape
    default: str  # what happens if unanswered


def _gate_explanation(decision: Decision, profile: ColumnProfile) -> str:
    gate = decision.escalation_gate
    top = decision.candidates[0] if decision.candidates else None
    if gate == "near_duplicate" and top is not None:
        return (
            f"The harness proposed a new concept ('{decision.new_name or decision.column}') but an existing "
            f"one, {top.card.id}, scored {top.score:.2f} against it -- close enough that minting a new "
            f"concept here risks a silent duplicate. This is judged on the retrieval score alone, not the "
            f"model's self-reported confidence ({decision.confidence:.2f}): confidence on this harness's "
            f"chosen model runs 0.90-1.00 on nearly everything, so it isn't a reliable signal to override "
            f"objective evidence with."
        )
    if gate == "datatype_conflict":
        return (
            f"The column's inferred datatype ('{profile.inferred_datatype}') conflicts "
            f"with the datatype of the reuse target '{decision.target}'. Reusing it as-is would silently "
            f"store the wrong kind of value."
        )
    if gate == "unknown_target":
        return f"The proposed reuse target '{decision.target}' does not exist anywhere in the ontology, even after a retry."
    if gate == "low_confidence":
        return f"The model's confidence ({decision.confidence:.2f}) was below the {0.55} threshold this harness requires to act unsupervised."
    if gate == "score_margin_ambiguous" and top is not None and len(decision.candidates) > 1:
        runner_up = decision.candidates[1]
        return (
            f"The top two retrieval candidates, {top.card.id} ({top.score:.2f}) and {runner_up.card.id} "
            f"({runner_up.score:.2f}), are almost tied -- that closeness is itself evidence the right target "
            f"isn't settled, regardless of what the model proposed."
        )
    if gate == "llm_escalate":
        return "The model itself asked for human input on this column."
    return decision.rationale


def build_question(decision: Decision, profile: ColumnProfile, csv_index: int, csv_name: str, qnum: int) -> Question:
    """Turn a gated Decision into a question a domain expert (not the
    harness's author) can answer without reading any code."""
    top_candidates = decision.candidates[:3]
    candidate_lines = [f"{c.card.id} (score {c.score:.2f}): {c.card.description}" for c in top_candidates]

    gate = decision.escalation_gate or "unknown"
    if gate == "near_duplicate" and top_candidates and not decision.near_dup_datatype_conflict:
        # near_duplicate fires precisely because the model's "new" proposal
        # is objectively risky (a real near-duplicate was retrieved) -- so
        # unlike the other gates, "keep the original guess" is not a safe
        # default here. Verified empirically: defaulting to keep_original on
        # an unanswered near_duplicate question re-proposed a relationship
        # that already existed on the type, which failed patch validation
        # and dropped the *entire* CSV's patch (see hq_city/company in the
        # 2026-08-11 recon run). Trusting the retrieval evidence -- reuse
        # the top candidate -- never creates a new ontology member, so it
        # can't collide, and it's the harness's own stated rationale for
        # escalating in the first place.
        #
        # EXCEPTION (near_dup_datatype_conflict, checked above): when this
        # gate fired via its lowered datatype-conflict bar, the top candidate
        # is a near-duplicate *whose own datatype conflicts with the
        # column's* (e.g. employee_count:integer vs. Organization.size:
        # string). Reusing it by default would silently apply exactly the
        # wrong-datatype mapping this whole harness exists to prevent --
        # verified this would happen on the offline `--no-llm` quickstart.
        # So that case falls through to the same safe default as
        # datatype_conflict itself: exclude, not reuse.
        default = f"reuse:{top_candidates[0].card.id}"
    elif gate == "retrieval_override" and top_candidates:
        # Same logic, one step further: this gate fires because the model
        # picked a target well below retrieval's top hit. With nobody there to
        # endorse that departure, fall back to what retrieval ranked first.
        default = f"reuse:{top_candidates[0].card.id}"
    else:
        default = _DEFAULT_BY_GATE.get(gate, _KEEP_ORIGINAL)

    options: list[str] = []
    if top_candidates:
        options.append(f"reuse:{top_candidates[0].card.id}")
    proposed_name = decision.new_name or decision.column
    options.append(f"new:{proposed_name}")
    options.append("exclude")
    options.append("other: <type a free-text answer>")

    question_text = (
        f"Column '{decision.column}' (sample values: {profile.samples[:5]}) -- "
        f"the harness proposed '{decision.disposition}'"
        + (f" -> {decision.target}" if decision.target else f" ({proposed_name})")
        + f". Is that right, or should it map to one of the candidates below instead?"
    )

    return Question(
        id=f"csv{csv_index}.q{qnum}",
        csv=csv_name,
        column=decision.column,
        gate=gate,
        question=question_text,
        why=_gate_explanation(decision, profile),
        context={
            "samples": list(profile.samples[:5]),
            "inferred_datatype": profile.inferred_datatype,
            "candidates": [{"id": c.card.id, "score": round(c.score, 4)} for c in top_candidates],
            "proposed": {
                "disposition": decision.disposition,
                "target": decision.target,
                "new_name": decision.new_name,
                "confidence": decision.confidence,
            },
        },
        options=options,
        default=default,
    )


def load_answers_file(path: str | None) -> dict[str, str]:
    """Parse `{"csv1.q1": "reuse:Organization.website", ...}`. Shared by
    `ask()` (to resolve answers) and run.py (to classify each escalation's
    `source` for the report without re-implementing this parsing)."""
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    return {str(k): str(v) for k, v in data.items()}


def _print_question_block(q: Question) -> None:
    print(f"\n--- {q.id} ({q.csv} :: {q.column}) [{q.gate}] ---")
    print(f"Q: {q.question}")
    print(f"Why: {q.why}")
    if q.context.get("candidates"):
        print("Candidates:")
        for cand in q.context["candidates"]:
            print(f"  - {cand['id']} (score {cand['score']:.2f})")
    print(f"Sample values: {q.context.get('samples')}")
    print("Options:")
    for opt in q.options:
        print(f"  - {opt}")
    print(f"Default if unanswered: {q.default}")


def ask(questions: list[Question], answers_file: str | None = None, interactive: bool = True) -> dict[str, str]:
    """Resolve one answer per question: answers file > interactive stdin >
    documented default. Prints a readable block per question either way."""
    file_answers = load_answers_file(answers_file)
    resolved: dict[str, str] = {}

    for q in questions:
        _print_question_block(q)

        if q.id in file_answers:
            answer = file_answers[q.id]
            print(f"A: {answer}  [answered from file]")
            resolved[q.id] = answer
            continue

        if interactive and sys.stdin.isatty():
            try:
                answer = input("A: ").strip()
            except EOFError:
                answer = ""
            if answer:
                resolved[q.id] = answer
                continue
            print(f"(empty input -> using default) [unanswered_default: {q.default}]")
            resolved[q.id] = q.default
            continue

        # Name the reason. "Not asked" has three distinct causes and silently
        # collapsing them into one message is what makes this look like a bug.
        if not interactive:
            why = "--interactive not set"
        else:
            why = "--interactive set but stdin is not a TTY"
        print(f"({why} -> using default) [unanswered_default: {q.default}]")
        resolved[q.id] = q.default

    return resolved


def classify_source(question_id: str, answers_data: dict[str, str], interactive: bool) -> str:
    """Deterministically reconstruct which channel answered a question, for
    the report's `escalations[].source` field. Mirrors ask()'s own
    precedence (file > interactive > default) without needing ask() to
    return anything beyond the plain `dict[qid, str]` the spec declares."""
    if question_id in answers_data:
        return "file"
    if interactive and sys.stdin.isatty():
        return "human"
    return "default"


def incorporate(
    question: Question,
    answer: str,
    decision: Decision,
    profile: ColumnProfile,
    csv_name: str,
    subject: SubjectTypeDecision,
    index: ConceptIndex,
    ontology: Ontology,
    prior_decisions: list[Decision],
    llm: LLM | None = None,
    source: str = "human",
) -> Decision:
    """Re-run the decision for `decision.column` with the human's answer as
    authoritative context, and return the resulting final Decision.

    `decision` is the escalated Decision this question was built from; its
    `.proposal`, `.candidates`, `.gates_fired` etc. are preserved so the
    audit trail (which gates fired, what was retrieved) survives into the
    final answer even though the disposition itself changes.

    `source` ("human" | "file" | "default", from `classify_source()`) drives
    `decided_by`: an unanswered question that fell back to its default was
    never actually reviewed by anyone, so it's attributed to "rule" rather
    than "human" even though it flows through this same function.
    """
    decided_by = "rule" if source == "default" else "human"

    resolved = Decision(**{**decision.__dict__})  # shallow copy, mutated below
    resolved.escalated = False
    resolved.downgraded_from_escalation = False

    def _finish(disposition: str, rationale_suffix: str, **fields) -> Decision:
        resolved.disposition = disposition
        resolved.decided_by = decided_by
        resolved.rationale = f"{decision.rationale} [escalation {question.id} ({source}): {rationale_suffix}]"
        for key, value in fields.items():
            setattr(resolved, key, value)
        return resolved

    if answer == _KEEP_ORIGINAL:
        proposal = decision.proposal or {}
        fallback_disposition = proposal.get("disposition", decision.disposition)
        if fallback_disposition == "escalate":
            # The model's own "best guess" was itself "escalate" -- there is
            # nothing safe to fall back to, so (same rule as the budget
            # gate's downgrade path) default to exclude rather than loop.
            fallback_disposition = "exclude"
        return _finish(
            fallback_disposition,
            "kept the harness's original proposal",
            target=proposal.get("target"),
            new_name=proposal.get("new_name"),
            new_datatype=proposal.get("new_datatype"),
            new_range=proposal.get("new_range"),
            on_type=proposal.get("on_type"),
            aligned_with=proposal.get("aligned_with"),
        )

    if answer == _EXCLUDE:
        return _finish("exclude", "answered exclude")

    if answer.startswith("reuse:"):
        target = answer.split(":", 1)[1].strip()
        return _finish("reuse", f"answered reuse:{target}", target=target)

    if answer.startswith("new:"):
        name = answer.split(":", 1)[1].strip()
        is_relationship = bool(decision.proposal.get("new_range")) or decision.disposition == "new_relationship"
        disposition = "new_relationship" if is_relationship else "new_attribute"
        return _finish(
            disposition,
            f"answered new:{name}",
            new_name=name,
            on_type=decision.on_type or subject.subject_type,
            new_datatype=decision.new_datatype or profile.inferred_datatype,
            new_range=decision.new_range,
        )

    # Free text ("other: ..." or anything else typed/read from file).
    raw_answer = answer.split(":", 1)[1].strip() if answer.lower().startswith("other:") else answer

    if llm is not None:
        result = propose_llm(
            profile,
            csv_name,
            subject,
            decision.candidates,
            prior_decisions,
            llm,
            human_answer=raw_answer,
        )
        return _finish(
            result["disposition"],
            f"free-text answer incorporated via LLM re-decide: {raw_answer!r}",
            target=result.get("target"),
            new_name=result.get("new_name"),
            new_datatype=result.get("new_datatype"),
            new_range=result.get("new_range"),
            on_type=result.get("on_type"),
            aligned_with=result.get("aligned_with"),
            confidence=float(result.get("confidence", decision.confidence)),
        )

    # --no-llm heuristic: if the free-text answer names one of the
    # retrieved candidates (by id, case-insensitive substring match), reuse
    # it; otherwise treat it as a request for a new attribute.
    matched = next((c for c in decision.candidates if c.card.id.lower() in raw_answer.lower()), None)
    if matched is not None:
        return _finish("reuse", f"free-text answer matched candidate {matched.card.id}", target=matched.card.id)
    return _finish(
        "new_attribute",
        f"free-text answer ({raw_answer!r}) did not match a candidate; treated as a new attribute",
        new_name=decision.new_name or decision.column,
        on_type=decision.on_type or subject.subject_type,
        new_datatype=decision.new_datatype or profile.inferred_datatype,
    )
