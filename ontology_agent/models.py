"""Core data model: the ontology (types/attributes/relationships) and the
typed patch operations an agent proposes against it.

Everything here is plain `dataclasses` + stdlib `json` -- no pydantic, no
external validation layer. `patch.py` owns *validating* these structures;
this module only owns their shape and (de)serialisation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# The only datatypes an attribute may declare. Kept as a plain set (not an
# Enum) so profiler.py and decide.py can do simple `in DATATYPES` checks
# without importing an enum type just for membership tests.
DATATYPES = {"string", "integer", "number", "boolean", "date", "datetime"}


# --------------------------------------------------------------------------
# Ontology model
# --------------------------------------------------------------------------


@dataclass
class Attribute:
    """A literal-valued property on an EntityType (e.g. Organization.website)."""

    name: str
    datatype: str
    required: bool = False
    description: str = ""
    aligned_with: str | None = None

    def to_dict(self) -> dict:
        d: dict = {"name": self.name, "datatype": self.datatype}
        if self.required:
            d["required"] = True
        if self.description:
            d["description"] = self.description
        if self.aligned_with:
            d["aligned_with"] = self.aligned_with
        return d

    @staticmethod
    def from_dict(d: dict) -> Attribute:
        return Attribute(
            name=d["name"],
            datatype=d["datatype"],
            required=bool(d.get("required", False)),
            description=d.get("description") or "",
            aligned_with=d.get("aligned_with"),
        )


@dataclass
class Relationship:
    """An entity-valued property on an EntityType (e.g. Product.made_by -> Organization)."""

    name: str
    range: str
    description: str = ""
    aligned_with: str | None = None

    def to_dict(self) -> dict:
        d: dict = {"name": self.name, "range": self.range}
        if self.description:
            d["description"] = self.description
        if self.aligned_with:
            d["aligned_with"] = self.aligned_with
        return d

    @staticmethod
    def from_dict(d: dict) -> Relationship:
        return Relationship(
            name=d["name"],
            range=d["range"],
            description=d.get("description") or "",
            aligned_with=d.get("aligned_with"),
        )


@dataclass
class EntityType:
    """One concept in the ontology, e.g. Organization or Product."""

    name: str
    description: str = ""
    attributes: list[Attribute] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "attributes": [a.to_dict() for a in self.attributes],
            "relationships": [r.to_dict() for r in self.relationships],
        }

    @staticmethod
    def from_dict(d: dict) -> EntityType:
        return EntityType(
            name=d["name"],
            description=d.get("description") or "",
            attributes=[Attribute.from_dict(a) for a in d.get("attributes", [])],
            relationships=[Relationship.from_dict(r) for r in d.get("relationships", [])],
        )


@dataclass
class Ontology:
    """The whole in-memory ontology: an ordered list of entity types."""

    types: list[EntityType] = field(default_factory=list)

    def get(self, type_name: str) -> EntityType | None:
        """Case-insensitive lookup by type name."""
        target = type_name.strip().lower()
        for t in self.types:
            if t.name.lower() == target:
                return t
        return None

    def attr(self, type_name: str, attr_name: str) -> Attribute | None:
        """Case-insensitive lookup of an attribute on a named type."""
        t = self.get(type_name)
        if t is None:
            return None
        target = attr_name.strip().lower()
        for a in t.attributes:
            if a.name.lower() == target:
                return a
        return None

    def rel(self, type_name: str, rel_name: str) -> Relationship | None:
        """Case-insensitive lookup of a relationship on a named type."""
        t = self.get(type_name)
        if t is None:
            return None
        target = rel_name.strip().lower()
        for r in t.relationships:
            if r.name.lower() == target:
                return r
        return None

    def to_json(self) -> dict:
        return {"types": [t.to_dict() for t in self.types]}

    @staticmethod
    def from_json(data: dict) -> Ontology:
        return Ontology(types=[EntityType.from_dict(t) for t in data.get("types", [])])

    @staticmethod
    def from_file(path: str | Path) -> Ontology:
        with Path(path).open(encoding="utf-8") as fh:
            data = json.load(fh)
        return Ontology.from_json(data)

    def deepcopy(self) -> Ontology:
        """A fully independent copy, safe to mutate without touching the original.

        Implemented as a JSON round-trip rather than `copy.deepcopy`: it's
        just as correct for plain-data dataclasses, and it doubles as a
        cheap sanity check that the ontology still serialises cleanly.
        """
        return Ontology.from_json(self.to_json())


# --------------------------------------------------------------------------
# Patch operations
#
# Each op is a small dataclass whose `op` field is fixed (declared with
# `init=False` so callers never have to pass it) and whose `to_dict()`
# produces the exact JSON shape from the challenge doc, with `op` first and
# unset optional fields (None / empty string) dropped so the emitted patch
# stays close to the doc's minimal examples.
# --------------------------------------------------------------------------


@dataclass
class ReuseOp:
    source_column: str
    target: str
    rationale: str
    confidence: float
    op: str = field(default="reuse", init=False)

    def to_dict(self) -> dict:
        return {
            "op": self.op,
            "source_column": self.source_column,
            "target": self.target,
            "rationale": self.rationale,
            "confidence": self.confidence,
        }


@dataclass
class AddAttributeOp:
    on_type: str
    name: str
    datatype: str
    rationale: str
    confidence: float
    aligned_with: str | None = None
    source_column: str | None = None
    description: str = ""
    op: str = field(default="add_attribute", init=False)

    def to_dict(self) -> dict:
        d: dict = {
            "op": self.op,
            "on_type": self.on_type,
            "name": self.name,
            "datatype": self.datatype,
            "rationale": self.rationale,
            "confidence": self.confidence,
        }
        if self.aligned_with:
            d["aligned_with"] = self.aligned_with
        if self.source_column:
            d["source_column"] = self.source_column
        if self.description:
            d["description"] = self.description
        return d


@dataclass
class AddRelationshipOp:
    on_type: str
    name: str
    range: str
    rationale: str
    confidence: float
    aligned_with: str | None = None
    source_column: str | None = None
    description: str = ""
    op: str = field(default="add_relationship", init=False)

    def to_dict(self) -> dict:
        d: dict = {
            "op": self.op,
            "on_type": self.on_type,
            "name": self.name,
            "range": self.range,
            "rationale": self.rationale,
            "confidence": self.confidence,
        }
        if self.aligned_with:
            d["aligned_with"] = self.aligned_with
        if self.source_column:
            d["source_column"] = self.source_column
        if self.description:
            d["description"] = self.description
        return d


@dataclass
class AddTypeOp:
    name: str
    attributes: list[dict]
    rationale: str
    confidence: float
    description: str = ""
    aligned_with: str | None = None
    op: str = field(default="add_type", init=False)

    def to_dict(self) -> dict:
        d: dict = {
            "op": self.op,
            "name": self.name,
            "attributes": self.attributes,
            "rationale": self.rationale,
            "confidence": self.confidence,
        }
        if self.description:
            d["description"] = self.description
        if self.aligned_with:
            d["aligned_with"] = self.aligned_with
        return d


@dataclass
class ExcludeOp:
    source_column: str
    rationale: str
    op: str = field(default="exclude", init=False)

    def to_dict(self) -> dict:
        return {"op": self.op, "source_column": self.source_column, "rationale": self.rationale}


@dataclass
class FlagOntologyIssueOp:
    target: str
    issue: str
    severity: str = "warning"
    op: str = field(default="flag_ontology_issue", init=False)

    def to_dict(self) -> dict:
        return {"op": self.op, "target": self.target, "issue": self.issue, "severity": self.severity}


# Convenience alias for code (patch.py, decide.py, report.py) that needs to
# type-hint "any patch op" without importing all six names individually.
PatchOp = ReuseOp | AddAttributeOp | AddRelationshipOp | AddTypeOp | ExcludeOp | FlagOntologyIssueOp
