"""Patch validation + application.

Spec: DESIGN.md §8.

`validate()` is pure and side-effect free (a list of human-readable error
strings). `apply()` always operates on a deep copy of the input ontology —
the caller's ontology object is never mutated in place, even in `auto` mode.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import (
    DATATYPES,
    AddAttributeOp,
    AddRelationshipOp,
    AddTypeOp,
    ExcludeOp,
    FlagOntologyIssueOp,
    Ontology,
    ReuseOp,
)

PatchOp = ReuseOp | AddAttributeOp | AddRelationshipOp | AddTypeOp | ExcludeOp | FlagOntologyIssueOp
Patch = list[PatchOp]

_SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_PASCAL_CASE_RE = re.compile(r"^[A-Z][A-Za-z0-9]*$")


@dataclass
class AppliedOp:
    """The outcome of trying to apply one patch op.

    `applied` is False when the op was skipped outright (interactive "no",
    or mode == "none"). `mutated` is separately False for op kinds that
    structurally never change the ontology (`exclude`, `flag_ontology_issue`)
    even when they were "applied" in the sense of being accepted/recorded —
    per §8, those two ops never mutate the ontology by design.
    """

    op: PatchOp
    applied: bool
    mutated: bool
    reason: str = ""


# --------------------------------------------------------------------------
# validate()
# --------------------------------------------------------------------------


def _resolve_target(target: str, ontology: Ontology) -> bool:
    """True if `target` ("Type.concept" or a bare "Type") resolves to an
    existing attribute, relationship, or type (case-insensitive, per §1)."""
    if "." in target:
        type_name, concept_name = target.split(".", 1)
        if ontology.attr(type_name, concept_name) is not None:
            return True
        if ontology.rel(type_name, concept_name) is not None:
            return True
        return False
    return ontology.get(target) is not None


def validate(patch: Patch, ontology: Ontology) -> list[str]:
    """Return a list of error strings; empty means the patch is well-formed.

    Validation is against the *original* ontology plus any types the patch
    itself introduces earlier in the list (an `add_type` op can be referenced
    by a later `add_attribute` / `add_relationship` op in the same patch).
    """
    errors: list[str] = []
    types_added_in_patch: dict[str, AddTypeOp] = {}

    for index, op in enumerate(patch):
        label = f"op[{index}] {op.op}"

        confidence = getattr(op, "confidence", None)
        if confidence is not None and not (0.0 <= confidence <= 1.0):
            errors.append(f"{label}: confidence {confidence!r} is not in [0, 1]")

        if isinstance(op, ReuseOp):
            if not _resolve_target(op.target, ontology) and op.target.split(".")[0] not in types_added_in_patch:
                errors.append(f"{label}: reuse target '{op.target}' does not resolve to an existing concept")

        elif isinstance(op, AddAttributeOp):
            type_known = ontology.get(op.on_type) is not None or op.on_type in types_added_in_patch
            if not type_known:
                errors.append(f"{label}: on_type '{op.on_type}' does not exist and is not added earlier in this patch")
            elif ontology.attr(op.on_type, op.name) is not None:
                # A collision here means the near-duplicate gate in decide.py
                # leaked a proposal that should have been forced to `reuse`.
                errors.append(f"{label}: attribute '{op.name}' already exists on '{op.on_type}' (case-insensitive)")
            if op.datatype not in DATATYPES:
                errors.append(f"{label}: datatype '{op.datatype}' not in {sorted(DATATYPES)}")
            if not _SNAKE_CASE_RE.match(op.name):
                errors.append(f"{label}: attribute name '{op.name}' is not snake_case")

        elif isinstance(op, AddRelationshipOp):
            type_known = ontology.get(op.on_type) is not None or op.on_type in types_added_in_patch
            if not type_known:
                errors.append(f"{label}: on_type '{op.on_type}' does not exist and is not added earlier in this patch")
            elif ontology.rel(op.on_type, op.name) is not None:
                errors.append(f"{label}: relationship '{op.name}' already exists on '{op.on_type}' (case-insensitive)")
            range_known = ontology.get(op.range) is not None or op.range in types_added_in_patch
            if not range_known:
                errors.append(f"{label}: range '{op.range}' does not exist in the ontology or this patch")
            if not _SNAKE_CASE_RE.match(op.name):
                errors.append(f"{label}: relationship name '{op.name}' is not snake_case")

        elif isinstance(op, AddTypeOp):
            if not _PASCAL_CASE_RE.match(op.name):
                errors.append(f"{label}: type name '{op.name}' is not PascalCase")
            if ontology.get(op.name) is not None:
                errors.append(f"{label}: type '{op.name}' already exists")
            for attr_dict in op.attributes:
                attr_name = attr_dict.get("name", "")
                attr_datatype = attr_dict.get("datatype", "")
                if not _SNAKE_CASE_RE.match(attr_name):
                    errors.append(f"{label}: nested attribute name '{attr_name}' is not snake_case")
                if attr_datatype not in DATATYPES:
                    errors.append(f"{label}: nested attribute '{attr_name}' has invalid datatype '{attr_datatype}'")
            types_added_in_patch[op.name] = op

        elif isinstance(op, ExcludeOp):
            pass  # nothing to validate structurally; always well-formed.

        elif isinstance(op, FlagOntologyIssueOp):
            if op.severity not in {"warning", "error", "info"}:
                errors.append(f"{label}: unknown severity '{op.severity}'")

        else:  # pragma: no cover - defensive; models.py owns the op set.
            errors.append(f"{label}: unrecognised op type {type(op).__name__}")

    return errors


# --------------------------------------------------------------------------
# apply()
# --------------------------------------------------------------------------


def _mutate_add_attribute(op: AddAttributeOp, ontology: Ontology) -> None:
    from .models import Attribute  # local import: models is Part A's file

    entity_type = ontology.get(op.on_type)
    if entity_type is None:
        raise ValueError(f"add_attribute: on_type '{op.on_type}' not found (should have failed validate())")
    entity_type.attributes.append(
        Attribute(
            name=op.name,
            datatype=op.datatype,
            required=False,
            description=op.description,
            aligned_with=op.aligned_with,
        )
    )


def _mutate_add_relationship(op: AddRelationshipOp, ontology: Ontology) -> None:
    from .models import Relationship

    entity_type = ontology.get(op.on_type)
    if entity_type is None:
        raise ValueError(f"add_relationship: on_type '{op.on_type}' not found (should have failed validate())")
    entity_type.relationships.append(
        Relationship(
            name=op.name,
            range=op.range,
            description=op.description,
            aligned_with=op.aligned_with,
        )
    )


def _mutate_add_type(op: AddTypeOp, ontology: Ontology) -> None:
    from .models import Attribute, EntityType

    attributes = [
        Attribute(
            name=attr_dict["name"],
            datatype=attr_dict["datatype"],
            required=bool(attr_dict.get("required", False)),
            description=attr_dict.get("description", ""),
            aligned_with=attr_dict.get("aligned_with"),
        )
        for attr_dict in op.attributes
    ]
    ontology.types.append(
        EntityType(
            name=op.name,
            description=op.description,
            attributes=attributes,
            relationships=[],
        )
    )


def _describe(op: PatchOp) -> str:
    """One-line human summary of an op, for interactive-mode prompts."""
    if isinstance(op, ReuseOp):
        return f"reuse: {op.source_column} -> {op.target} ({op.rationale})"
    if isinstance(op, AddAttributeOp):
        return f"add_attribute: {op.on_type}.{op.name} ({op.datatype}) — {op.rationale}"
    if isinstance(op, AddRelationshipOp):
        return f"add_relationship: {op.on_type}.{op.name} -> {op.range} — {op.rationale}"
    if isinstance(op, AddTypeOp):
        attr_names = ", ".join(a.get("name", "?") for a in op.attributes)
        return f"add_type: {op.name} [{attr_names}] — {op.rationale}"
    if isinstance(op, ExcludeOp):
        return f"exclude: {op.source_column} — {op.rationale}"
    if isinstance(op, FlagOntologyIssueOp):
        return f"flag_ontology_issue [{op.severity}]: {op.target} — {op.issue}"
    return str(op)  # pragma: no cover - defensive.


def apply(patch: Patch, ontology: Ontology, mode: str) -> tuple[Ontology, list[AppliedOp]]:
    """Apply a validated patch to a *copy* of `ontology`.

    mode:
      "auto"        - apply every op. This is the mode the demo run uses,
                       and it is labelled as such in the transcript (run.py).
      "interactive" - prompt y/N per op on stdin.
      "none"        - emit only; apply nothing.
    """
    if mode not in {"auto", "interactive", "none"}:
        raise ValueError(f"unknown apply mode: {mode!r}")

    working = ontology.deepcopy()
    results: list[AppliedOp] = []

    for op in patch:
        # exclude / flag_ontology_issue never mutate the ontology, by design
        # (§8) — they are recorded, not applied structurally.
        if isinstance(op, (ExcludeOp, FlagOntologyIssueOp)):
            results.append(AppliedOp(op=op, applied=True, mutated=False, reason="op kind never mutates the ontology"))
            continue

        if isinstance(op, ReuseOp):
            # A reuse decision doesn't add anything to the ontology either —
            # it just records which existing concept a column maps to.
            results.append(AppliedOp(op=op, applied=True, mutated=False, reason="reuse does not change ontology structure"))
            continue

        if mode == "none":
            results.append(AppliedOp(op=op, applied=False, mutated=False, reason="mode=none: emit only"))
            continue

        if mode == "interactive":
            answer = _prompt_yes_no(_describe(op))
            if not answer:
                results.append(AppliedOp(op=op, applied=False, mutated=False, reason="declined interactively"))
                continue

        try:
            if isinstance(op, AddAttributeOp):
                _mutate_add_attribute(op, working)
            elif isinstance(op, AddRelationshipOp):
                _mutate_add_relationship(op, working)
            elif isinstance(op, AddTypeOp):
                _mutate_add_type(op, working)
            else:  # pragma: no cover - defensive.
                raise ValueError(f"unhandled patch op type: {type(op).__name__}")
        except ValueError as exc:
            results.append(AppliedOp(op=op, applied=False, mutated=False, reason=str(exc)))
            continue

        results.append(AppliedOp(op=op, applied=True, mutated=True))

    return working, results


def _prompt_yes_no(summary: str) -> bool:
    """Interactive y/N prompt for one op. Non-interactive stdin (no TTY,
    EOF) is treated as "no" — the safe default for an unattended run that
    was accidentally started in interactive mode."""
    try:
        answer = input(f"Apply this op? [y/N] {summary}\n> ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}
