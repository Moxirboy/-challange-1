"""Mapping report (JSON + Markdown) and deterministic sample-row projection.

Spec: DESIGN.md §9.

`project_rows` is the one function in this whole package that is explicitly
required to never call the LLM: it replays the already-made decisions
against the (patched) ontology to show a reviewer exactly what data would
come out the other end.
"""

from __future__ import annotations

import json
import os

from .decide import Decision, SubjectTypeDecision
from .models import FlagOntologyIssueOp
from .ontology import Ontology
from .patch import Patch

# Sentinel relationship values that must never produce an entity (§9,
# expanded per the task's explicit list beyond DESIGN.md's shorter example).
SENTINEL_RELATIONSHIP_VALUES = {"direct", "n/a", "-", "none", "unknown", ""}


def is_sentinel(value: str) -> bool:
    return value.strip().lower() in SENTINEL_RELATIONSHIP_VALUES


def _bare_name(target: str) -> str:
    return target.split(".", 1)[1] if "." in target else target


def _owner_type(target: str) -> str:
    return target.split(".", 1)[0] if "." in target else target


# --------------------------------------------------------------------------
# project_rows -- deterministic, no LLM.
# --------------------------------------------------------------------------


def _project_literal(name: str, raw: str, column: str, attrs: dict[str, str], skipped: list[dict]) -> None:
    if not raw:
        skipped.append({"column": column, "reason": "empty value"})
        return
    attrs[name] = raw


def _project_relationship_column(
    range_type: str,
    rel_name: str,
    raw: str,
    column: str,
    subject_rels: dict[str, str],
    related_entities: dict[str, dict],
    skipped: list[dict],
) -> None:
    """A column whose own value directly *names* the target entity (e.g.
    `distributor` -> the Organization it names). This is the case the
    spec's sentinel-value example (`distributor == "Direct"`) is about."""
    if not raw or is_sentinel(raw):
        skipped.append({"column": column, "reason": f"sentinel/empty relationship value ({raw!r})"})
        return
    entity_id = f"{range_type}:{raw}"
    related_entities[entity_id] = {"id": entity_id, "type": range_type, "attributes": {"name": raw}, "relationships": {}}
    if rel_name:
        subject_rels[rel_name] = entity_id


def _project_grouped_attribute(
    owner_type: str,
    attr_name: str,
    raw: str,
    column: str,
    ontology: Ontology,
    subject_type: str,
    grouped_targets: dict[str, dict],
    skipped: list[dict],
) -> None:
    """Two-or-more columns describing one sub-entity (e.g. hq_city +
    hq_country -> Place) accumulate onto a single sub-entity per row,
    matching the `Place:{city}|{country}` example in §9."""
    if not raw:
        skipped.append({"column": column, "reason": "empty value"})
        return
    group = grouped_targets.setdefault(owner_type, {"rel_name": None, "attrs": {}})
    group["attrs"][attr_name] = raw
    if group["rel_name"] is None:
        subject = ontology.get(subject_type)
        rel = next((r for r in subject.relationships if r.range.lower() == owner_type.lower()), None) if subject else None
        group["rel_name"] = rel.name if rel else None


def project_rows(
    rows: list[dict[str, str]],
    decisions: list[Decision],
    ontology: Ontology,
    subject_type: str,
    n: int = 3,
) -> list[dict]:
    """For each of the first `n` rows, project the entities/values it
    produces under `ontology` (expected to already be the *patched*
    ontology for this CSV — see run.py). Deterministic; makes no network or
    LLM calls.

    Deviates from §9's bare `project_rows(rows, decisions, ontology, n=3)`
    signature by one required parameter, `subject_type`: nothing else this
    function receives can tell it what type the CSV's own rows are (that
    information lives on SubjectTypeDecision, one level up), and without it
    there is no way to name the subject entity or find the relationship
    that links it to a grouped sub-entity like Place.
    """
    results: list[dict] = []

    for row_index, row in enumerate(rows[:n], start=1):
        subject_id = f"{subject_type}:{row_index}"
        subject_attrs: dict[str, str] = {}
        subject_rels: dict[str, str] = {}
        related_entities: dict[str, dict] = {}
        grouped_targets: dict[str, dict] = {}
        skipped: list[dict] = []

        for decision in decisions:
            column = decision.column
            raw = row.get(column, "")
            raw = raw.strip() if isinstance(raw, str) else raw

            if decision.disposition == "exclude":
                skipped.append({"column": column, "reason": f"excluded: {decision.rationale}"})
                continue

            if decision.disposition == "escalate":
                # Should not happen in a correct run (all escalations are
                # resolved or budget-downgraded before reporting); handled
                # defensively so a projection never crashes on it.
                skipped.append({"column": column, "reason": "unresolved escalation at projection time"})
                continue

            if decision.disposition == "reuse":
                target = decision.target or ""
                owner = _owner_type(target)
                bare = _bare_name(target)
                rel = ontology.rel(owner, bare)
                if rel is not None:
                    _project_relationship_column(rel.range, bare, raw, column, subject_rels, related_entities, skipped)
                    continue
                attr = ontology.attr(owner, bare)
                if attr is None:
                    skipped.append({"column": column, "reason": f"reuse target '{target}' not found in ontology"})
                    continue
                if owner.lower() == subject_type.lower():
                    _project_literal(attr.name, raw, column, subject_attrs, skipped)
                else:
                    _project_grouped_attribute(owner, attr.name, raw, column, ontology, subject_type, grouped_targets, skipped)
                continue

            if decision.disposition == "new_attribute":
                name = decision.new_name or column
                owner = decision.on_type or subject_type
                if owner.lower() == subject_type.lower():
                    _project_literal(name, raw, column, subject_attrs, skipped)
                else:
                    _project_grouped_attribute(owner, name, raw, column, ontology, subject_type, grouped_targets, skipped)
                continue

            if decision.disposition == "new_relationship":
                range_type = decision.new_range or "Unknown"
                rel_name = decision.new_name or column
                _project_relationship_column(range_type, rel_name, raw, column, subject_rels, related_entities, skipped)
                continue

            skipped.append({"column": column, "reason": f"unhandled disposition '{decision.disposition}'"})

        entities = [{"id": subject_id, "type": subject_type, "attributes": subject_attrs, "relationships": subject_rels}]

        for owner_type, group in grouped_targets.items():
            values = group["attrs"]
            if not values:
                continue
            # Deterministic, documented key: sort by attribute name (not
            # dict/column insertion order) so the synthetic id is stable
            # across reruns regardless of how the columns were ordered.
            entity_id = f"{owner_type}:" + "|".join(values[k] for k in sorted(values))
            entities.append({"id": entity_id, "type": owner_type, "attributes": values, "relationships": {}})
            if group["rel_name"]:
                subject_rels[group["rel_name"]] = entity_id
            else:
                skipped.append(
                    {
                        "column": f"(grouped:{owner_type})",
                        "reason": f"no relationship from {subject_type} to {owner_type} in the ontology; "
                        f"sub-entity built but not linked",
                    }
                )

        entities.extend(related_entities.values())

        results.append({"row": row_index, "entities": entities, "skipped": skipped})

    return results


# --------------------------------------------------------------------------
# Report assembly.
# --------------------------------------------------------------------------


def _candidate_to_dict(candidate) -> dict:
    return {
        "id": candidate.card.id,
        "score": round(candidate.score, 4),
        "signals": {k: round(v, 4) for k, v in candidate.signals.items()},
    }


def _decision_to_column_dict(decision: Decision) -> dict:
    return {
        "column": decision.column,
        "disposition": decision.disposition,
        "target": decision.target,
        "rationale": decision.rationale,
        "confidence": round(decision.confidence, 4),
        "decided_by": decision.decided_by,
        "gates_fired": list(decision.gates_fired),
        "escalated": decision.escalated,
        "downgraded_from_escalation": decision.downgraded_from_escalation,
        "retrieval": [_candidate_to_dict(c) for c in decision.candidates],
    }


def build_report(
    csv_name: str,
    row_count: int,
    subject: SubjectTypeDecision,
    decisions: list[Decision],
    escalations: list[dict],
    ontology_issues: list[FlagOntologyIssueOp],
    sample_rows: list[dict],
    llm_usage: dict,
) -> dict:
    """Assemble the JSON-shaped report dict per §9."""
    reused = sum(1 for d in decisions if d.disposition == "reuse")
    new = sum(1 for d in decisions if d.disposition in {"new_attribute", "new_relationship"})
    excluded = sum(1 for d in decisions if d.disposition == "exclude")

    return {
        "csv": csv_name,
        "row_count": row_count,
        "subject_type": {
            "name": subject.subject_type,
            "reused": not subject.is_new,
            "rationale": subject.rationale,
            "confidence": round(subject.confidence, 4),
        },
        "columns": [_decision_to_column_dict(d) for d in decisions],
        "escalations": escalations,
        "ontology_issues": [op.to_dict() for op in ontology_issues],
        "sample_rows": sample_rows,
        "stats": {
            "columns": len(decisions),
            "reused": reused,
            "new": new,
            "excluded": excluded,
            "escalated": len(escalations),
            "llm_calls": llm_usage.get("calls", 0),
            "cached_calls": llm_usage.get("cached_calls", 0),
            "prompt_tokens": llm_usage.get("prompt_tokens", 0),
            "completion_tokens": llm_usage.get("completion_tokens", 0),
        },
    }


# --------------------------------------------------------------------------
# Markdown rendering.
# --------------------------------------------------------------------------


def render_markdown(report: dict) -> str:
    lines: list[str] = []
    lines.append(f"# Mapping report — {report['csv']}")
    lines.append("")
    lines.append(f"Row count: {report['row_count']}")
    lines.append("")
    st = report["subject_type"]
    lines.append(f"## Subject type: `{st['name']}` ({'reused' if st['reused'] else 'new'})")
    lines.append(f"- confidence: {st['confidence']}")
    lines.append(f"- rationale: {st['rationale']}")
    lines.append("")

    lines.append("## Columns")
    lines.append("")
    lines.append("| column | disposition | target | confidence | decided_by | gates_fired | escalated |")
    lines.append("|---|---|---|---|---|---|---|")
    for col in report["columns"]:
        gates = ", ".join(col["gates_fired"]) or "-"
        lines.append(
            f"| {col['column']} | {col['disposition']} | {col['target'] or '-'} | {col['confidence']} | "
            f"{col['decided_by']} | {gates} | {'yes (downgraded)' if col['downgraded_from_escalation'] else col['escalated']} |"
        )
    lines.append("")

    for col in report["columns"]:
        if col["retrieval"]:
            lines.append(f"<details><summary>{col['column']} — retrieval candidates</summary>\n")
            for cand in col["retrieval"]:
                sig = ", ".join(f"{k}={v}" for k, v in cand["signals"].items())
                lines.append(f"- `{cand['id']}` score={cand['score']} ({sig})")
            lines.append("\n</details>\n")

    if report["escalations"]:
        lines.append("## Escalations")
        lines.append("")
        for esc in report["escalations"]:
            lines.append(f"### {esc['id']}")
            lines.append(f"- question: {esc['question']}")
            lines.append(f"- answer: {esc['answer']} (source: {esc['source']})")
            lines.append(f"- resulting decision: {esc['resulting_decision']}")
            lines.append("")

    if report["ontology_issues"]:
        lines.append("## Ontology issues")
        lines.append("")
        for issue in report["ontology_issues"]:
            lines.append(f"- [{issue.get('severity', 'warning')}] {issue.get('target')}: {issue.get('issue')}")
        lines.append("")

    if report["sample_rows"]:
        lines.append("## Sample-row projection")
        lines.append("")
        for row in report["sample_rows"]:
            lines.append(f"### Row {row['row']}")
            for entity in row["entities"]:
                lines.append(f"- `{entity['id']}` ({entity['type']}): attrs={entity['attributes']}, rels={entity['relationships']}")
            if row["skipped"]:
                lines.append(f"- skipped: {row['skipped']}")
            lines.append("")

    stats = report["stats"]
    lines.append("## Stats")
    lines.append("")
    for key, value in stats.items():
        lines.append(f"- {key}: {value}")
    lines.append("")

    return "\n".join(lines)


def write_report(out_dir: str, stem: str, patch_ops: Patch, report: dict) -> tuple[str, str, str]:
    """Write `<stem>.patch.json`, `<stem>.report.json`, `<stem>.report.md`
    into `out_dir`; return the three paths written."""
    os.makedirs(out_dir, exist_ok=True)

    patch_path = os.path.join(out_dir, f"{stem}.patch.json")
    report_json_path = os.path.join(out_dir, f"{stem}.report.json")
    report_md_path = os.path.join(out_dir, f"{stem}.report.md")

    with open(patch_path, "w", encoding="utf-8") as f:
        json.dump([op.to_dict() for op in patch_ops], f, indent=2)
        f.write("\n")

    with open(report_json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
        f.write("\n")

    with open(report_md_path, "w", encoding="utf-8") as f:
        f.write(render_markdown(report))

    return patch_path, report_json_path, report_md_path
