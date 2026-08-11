"""In-memory ontology support: the retrieval index unit (`ConceptCard`),
building cards from an `Ontology`, and the deterministic hygiene audit.

Patch *application* (mutating a copy of the ontology per accepted op) lives
in patch.py, not here -- `Ontology` is a plain dataclass tree
(models.Ontology / EntityType / Attribute / Relationship), so patch.py can
build a `.deepcopy()` and manipulate its lists directly without needing a
bespoke mutation API from this module. This module's contribution to "patch
application" is `build_cards()`, which the caller re-runs after every
applied patch to refresh the retrieval index.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

from .models import EntityType, FlagOntologyIssueOp, Ontology
from .profiler import canonical_attribute_id, raw_split, shares_synonym_cluster

# --------------------------------------------------------------------------
# Concept cards
# --------------------------------------------------------------------------

ConceptKind = Literal["type", "attribute", "relationship"]


@dataclass
class ConceptCard:
    """One retrievable unit: a type, an attribute, or a relationship."""

    kind: ConceptKind
    id: str  # "Organization" | "Organization.website" | "Product.made_by"
    owner_type: str | None  # None for type cards
    name: str  # bare local name
    datatype: str | None  # attributes only
    range: str | None  # relationships only
    description: str
    text: str  # dense text used for lexical + embedding indexing
    vacuous: bool
    # -- Near-duplicate-type alias resolution (see resolve_concept_aliases
    # below). Both default to "this card is not part of any detected
    # near-duplicate pair" so every existing caller that builds a
    # ConceptCard by hand (there are none outside build_cards(), but the
    # defaults keep the dataclass safe to construct positionally-short
    # regardless) keeps working unchanged.
    aliased_to: str | None = None  # id of the canonical card this one resolves to, e.g. "Company.url" -> "Organization.website"
    orphan_twin: bool = False  # on a non-canonical twin type, but has no canonical counterpart to resolve to -> demote hard instead


def _type_text(et: EntityType) -> str:
    attr_names = ", ".join(a.name for a in et.attributes)
    return f"{et.name} — {et.description} | attributes: {attr_names}"


def _attribute_text(et: EntityType, attr_name: str, datatype: str, description: str) -> str:
    cid = f"{et.name}.{attr_name}"
    if description:
        return f"{cid} ({datatype}) — {description}"
    return f"{cid} ({datatype})"


def _relationship_text(et: EntityType, rel_name: str, range_: str, description: str) -> str:
    cid = f"{et.name}.{rel_name}"
    if description:
        return f"{cid} -> {range_} — {description}"
    return f"{cid} -> {range_}"


def build_cards(ontology: Ontology, embedder=None) -> list[ConceptCard]:
    """One card per type, per attribute, per relationship. Call again after
    every applied patch to keep the retrieval index in sync.

    `embedder` is optional (added on top of the original `build_cards(ontology)`
    signature, default None so every existing call site -- run.py calls this
    as `build_cards(ontology)`, with no embedder -- behaves exactly as before
    unless a caller opts in). It is only used to strengthen a *borderline*
    near-duplicate-type call the same way `audit(ontology, embedder=...)`
    already does (see `find_near_duplicate_types`); the Organization/Company
    pair this fix targets scores 0.80 >= the 0.5 threshold from the purely
    deterministic formula alone, so no embedder is required for it.

    NOTE: run.py passes `embedder` to `audit()` but not to this function, so
    in a live LLM run a genuinely *borderline* (0.4-0.5) pair could be
    flagged by the startup audit banner without also being aliased here.
    That asymmetry is a pre-existing call-site wiring gap in run.py (out of
    scope for this fix) rather than a defect in this module -- worth fixing
    alongside any future run.py change.
    """
    cards: list[ConceptCard] = []
    for et in ontology.types:
        cards.append(
            ConceptCard(
                kind="type",
                id=et.name,
                owner_type=None,
                name=et.name,
                datatype=None,
                range=None,
                description=et.description,
                text=_type_text(et),
                vacuous=_is_vacuous(et.name, et.description),
            )
        )
        for attr in et.attributes:
            cards.append(
                ConceptCard(
                    kind="attribute",
                    id=f"{et.name}.{attr.name}",
                    owner_type=et.name,
                    name=attr.name,
                    datatype=attr.datatype,
                    range=None,
                    description=attr.description,
                    text=_attribute_text(et, attr.name, attr.datatype, attr.description),
                    vacuous=_is_vacuous(attr.name, attr.description),
                )
            )
        for rel in et.relationships:
            cards.append(
                ConceptCard(
                    kind="relationship",
                    id=f"{et.name}.{rel.name}",
                    owner_type=et.name,
                    name=rel.name,
                    datatype=None,
                    range=rel.range,
                    description=rel.description,
                    text=_relationship_text(et, rel.name, rel.range, rel.description),
                    vacuous=_is_vacuous(rel.name, rel.description),
                )
            )

    # Stamp near-duplicate-type alias resolution onto the cards themselves
    # (fix plan point 2). This is the ONE place retrieval.py's ConceptIndex
    # learns about aliasing -- ConceptIndex.__init__ only ever receives
    # `cards`, never the Ontology, so carrying the info on each card (rather
    # than threading a second data structure through a constructor whose
    # signature must not change) is what makes this work with run.py's
    # existing, unmodified `ConceptIndex(build_cards(ontology), embedder=...)`
    # call.
    type_alias = resolve_canonical_types(ontology, embedder=embedder)
    if type_alias:
        concept_alias = resolve_concept_aliases(ontology, embedder=embedder, _type_alias=type_alias)
        alias_type_names = set(type_alias)
        for card in cards:
            owner = card.owner_type if card.owner_type is not None else card.id  # type cards alias by their own id
            if owner not in alias_type_names:
                continue
            target = concept_alias.get(card.id)
            if target is not None:
                card.aliased_to = target
            else:
                # On a non-canonical twin type but no synonym-cluster match
                # on the canonical type -- keep the concept findable, but it
                # has nothing to resolve to, so demote it hard instead
                # (retrieval.py's ORPHAN_TWIN_DEMOTION).
                card.orphan_twin = True

    return cards


# --------------------------------------------------------------------------
# Hygiene audit
# --------------------------------------------------------------------------

NEAR_DUP_THRESHOLD = 0.5

# Bare names that are never meaningful on their own -- a column called
# "notes" mapping to `Person.data` would be exactly the kind of semantic
# garbage the harness exists to prevent.
VACUOUS_NAMES = {
    "data",
    "value",
    "info",
    "extra",
    "misc",
    "meta",
    "other",
    "field",
    "notes_field",
    "payload",
}
# Extra generic placeholders that only count as "uninformative" when the
# description is *also* empty (the first VACUOUS_NAMES check already covers
# the unconditional case; this is the narrower third rule in the spec).
_EXTRA_UNINFORMATIVE_NAMES = {"val", "tmp", "temp", "x", "attr", "attribute", "item", "thing", "stuff"}

_VACUOUS_DESC_RE = re.compile(r"^(misc|miscellaneous|other|various|tbd|n/a)\b", re.I)

# Words that imply a numeric quantity. Matched against whole *tokens* (via
# raw_split), never as a raw substring -- `Place.country` must never trip
# this just because "count" is a substring of "country".
SMELL_WORDS = {"count", "size", "qty", "quantity", "amount", "number", "total", "num"}


def _is_vacuous(name: str, description: str) -> bool:
    bare = name.strip().lower()
    if bare in VACUOUS_NAMES:
        return True
    desc = (description or "").strip()
    if desc and _VACUOUS_DESC_RE.match(desc):
        return True
    if not desc and bare in _EXTRA_UNINFORMATIVE_NAMES:
        return True
    return False


def _attribute_overlap(a: EntityType, b: EntityType) -> float:
    set_a = {canonical_attribute_id(attr.name) for attr in a.attributes}
    set_b = {canonical_attribute_id(attr.name) for attr in b.attributes}
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def _name_similarity(a: str, b: str) -> float:
    # A shared synonym cluster (e.g. "organization"/"company" both belong to
    # the vendor/supplier/company/organization group) is a strong,
    # deterministic signal -- prefer it over noisy string-edit distance,
    # which is what actually makes this rule reliably catch Organization vs
    # Company regardless of exact-string-similarity quirks.
    if shares_synonym_cluster(a, b):
        return 1.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _cosine(u: list[float], v: list[float]) -> float:
    dot = sum(x * y for x, y in zip(u, v))
    nu = math.sqrt(sum(x * x for x in u))
    nv = math.sqrt(sum(y * y for y in v))
    if nu == 0 or nv == 0:
        return 0.0
    return dot / (nu * nv)


def _embedding_boost(embedder, text_a: str, text_b: str) -> float:
    """Best-effort cosine similarity, used only to nudge a *borderline*
    near-dup score over the threshold. Never raises: any embedder failure
    (network, missing key, ...) just means no boost, matching
    `Embedder.embed`'s own graceful-degradation contract so the audit stays
    usable offline.
    """
    if embedder is None:
        return 0.0
    try:
        vectors = embedder.embed([text_a, text_b])
    except Exception:
        return 0.0
    if len(vectors) != 2 or not vectors[0] or not vectors[1]:
        return 0.0
    return _cosine(vectors[0], vectors[1])


def find_near_duplicate_types(ontology: Ontology, embedder=None) -> list[tuple[str, str, float]]:
    """Structured near-duplicate type-pair detection (fix plan point 1).

    This is now the ONE place the near-dup score is computed: `audit()`
    builds its `flag_ontology_issue` ops on top of this, and
    `resolve_canonical_types` (below) builds the canonical/alias mapping on
    top of the exact same pairs -- so there is no way for "what counts as a
    near-duplicate" to drift between the audit banner and what retrieval
    actually aliases.

    Returns `(type_a_name, type_b_name, score)` for every pair scoring >=
    NEAR_DUP_THRESHOLD, deterministically ordered (score desc, then both
    names asc) so downstream union-find resolution is reproducible across
    reruns regardless of `ontology.types` list order.
    """
    pairs: list[tuple[str, str, float]] = []
    types = ontology.types
    for i in range(len(types)):
        for j in range(i + 1, len(types)):
            a, b = types[i], types[j]
            name_sim = _name_similarity(a.name, b.name)
            attr_overlap = _attribute_overlap(a, b)
            score = 0.5 * name_sim + 0.5 * attr_overlap

            # The deterministic formula above is authoritative and is what
            # makes the audit reproducible with no network access. Embeddings
            # are only allowed to rescue a genuinely borderline call.
            if embedder is not None and 0.4 <= score < NEAR_DUP_THRESHOLD:
                boost = _embedding_boost(embedder, f"{a.name} {a.description}", f"{b.name} {b.description}")
                if boost >= 0.8:
                    score = NEAR_DUP_THRESHOLD

            if score >= NEAR_DUP_THRESHOLD:
                pairs.append((a.name, b.name, score))
    pairs.sort(key=lambda p: (-p[2], p[0], p[1]))
    return pairs


def _type_richness(et: EntityType) -> tuple[int, int]:
    """Canonical-type tie-break key, first two components (see
    `_pick_canonical_type`'s docstring for the full documented rule):
    (attributes + relationships count, non-empty-description count).
    Larger wins on both.
    """
    n_concepts = len(et.attributes) + len(et.relationships)
    n_desc = (
        sum(1 for a in et.attributes if (a.description or "").strip())
        + sum(1 for r in et.relationships if (r.description or "").strip())
        + (1 if (et.description or "").strip() else 0)
    )
    return (n_concepts, n_desc)


def _pick_canonical_type(a: EntityType, b: EntityType) -> tuple[EntityType, EntityType]:
    """Deterministic canonical-type choice for a near-duplicate pair
    (fix plan point 1). Documented rule, applied in order:
      1. more attributes + relationships wins (richer type keeps more of
         what data would otherwise be split across the twin);
      2. tie-break: more non-empty descriptions (own type description plus
         every attribute/relationship description) wins;
      3. final tie-break: alphabetically-first type name wins -- purely
         mechanical, guarantees the choice never depends on ontology.types
         list order or set/dict iteration order.
    Returns (canonical, alias).
    """
    ra, rb = _type_richness(a), _type_richness(b)
    if ra != rb:
        return (a, b) if ra > rb else (b, a)
    return (a, b) if a.name.lower() <= b.name.lower() else (b, a)


def resolve_canonical_types(ontology: Ontology, embedder=None) -> dict[str, str]:
    """Map every non-canonical (alias) type name to its canonical type name,
    for every near-duplicate pair `find_near_duplicate_types` reports.

    Uses union-find so a *chain* of pairs (A~B, B~C) still resolves every
    alias in the cluster onto one root -- and because every union keeps the
    richer of the two roots (`_pick_canonical_type`), that root is always
    the single richest member of the whole cluster, regardless of the order
    pairs are processed in. Returns only alias entries (a type that is its
    own canonical is simply absent from the returned dict).
    """
    pairs = find_near_duplicate_types(ontology, embedder=embedder)
    if not pairs:
        return {}
    by_name = {et.name: et for et in ontology.types}
    parent: dict[str, str] = {name: name for name in by_name}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # path compression
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        rx, ry = find(x), find(y)
        if rx == ry:
            return
        canon, alias = _pick_canonical_type(by_name[rx], by_name[ry])
        parent[alias.name] = canon.name

    for a_name, b_name, _score in pairs:
        union(a_name, b_name)

    return {name: find(name) for name in by_name if find(name) != name}


def resolve_concept_aliases(
    ontology: Ontology, embedder=None, _type_alias: dict[str, str] | None = None
) -> dict[str, str]:
    """Card-id -> canonical-card-id map for every non-canonical twin
    concept (fix plan point 2): the type card itself (e.g. "Company" ->
    "Organization") plus every attribute/relationship on the alias type that
    has a synonym-cluster match on the canonical type, via the exact same
    `profiler.canonical_attribute_id` normalisation the near-dup audit's
    attribute-overlap Jaccard already uses -- so "is this the same concept"
    is answered identically in both places.

    A twin attribute/relationship with NO canonical-side match is
    deliberately left OUT of this map: `build_cards` treats that absence as
    the signal to mark the card `orphan_twin=True` instead (keep it
    findable, demote it hard) rather than aliasing it to nothing.

    `_type_alias` lets `build_cards` pass in an already-computed
    `resolve_canonical_types(...)` result to avoid a second pass over
    `find_near_duplicate_types` (and, when an embedder is set, a second
    round of embedding calls) -- purely a call-sharing optimisation, not a
    second detection path; the public one-arg contract still recomputes it.
    """
    type_alias = _type_alias if _type_alias is not None else resolve_canonical_types(ontology, embedder=embedder)
    if not type_alias:
        return {}
    by_name = {et.name: et for et in ontology.types}
    concept_alias: dict[str, str] = {}
    for alias_name, canon_name in type_alias.items():
        alias_type = by_name[alias_name]
        canon_type = by_name[canon_name]
        canon_by_cid: dict[str, str] = {}
        for attr in canon_type.attributes:
            canon_by_cid.setdefault(canonical_attribute_id(attr.name), f"{canon_name}.{attr.name}")
        for rel in canon_type.relationships:
            canon_by_cid.setdefault(canonical_attribute_id(rel.name), f"{canon_name}.{rel.name}")

        concept_alias[alias_name] = canon_name  # the type card itself
        for attr in alias_type.attributes:
            target = canon_by_cid.get(canonical_attribute_id(attr.name))
            if target is not None:
                concept_alias[f"{alias_name}.{attr.name}"] = target
        for rel in alias_type.relationships:
            target = canon_by_cid.get(canonical_attribute_id(rel.name))
            if target is not None:
                concept_alias[f"{alias_name}.{rel.name}"] = target
    return concept_alias


def _near_duplicate_flags(ontology: Ontology, embedder) -> list[FlagOntologyIssueOp]:
    flags: list[FlagOntologyIssueOp] = []
    by_name = {et.name: et for et in ontology.types}
    canonical = resolve_canonical_types(ontology, embedder=embedder)
    for a_name, b_name, score in find_near_duplicate_types(ontology, embedder=embedder):
        a, b = by_name[a_name], by_name[b_name]
        name_sim = _name_similarity(a.name, b.name)
        attr_overlap = _attribute_overlap(a, b)

        # One of the pair is always a key in `canonical` (it's built from
        # these exact same pairs) -- name the canonical/alias explicitly so
        # the flag tells a reviewer not just "these look the same" but
        # "consolidate alias_name into canon_name", matching what retrieval
        # is now doing under the hood.
        if a_name in canonical:
            canon_name, alias_name = canonical[a_name], a_name
        elif b_name in canonical:
            canon_name, alias_name = canonical[b_name], b_name
        else:  # pragma: no cover - defensive; union-find guarantees one hit
            canon_name, alias_name = (a_name, b_name) if a_name <= b_name else (b_name, a_name)

        flags.append(
            FlagOntologyIssueOp(
                target=f"{a.name} / {b.name}",
                issue=(
                    f"'{a.name}' and '{b.name}' look like near-duplicate types "
                    f"(name_similarity={name_sim:.2f}, attribute_overlap={attr_overlap:.2f}, "
                    f"score={score:.2f} >= {NEAR_DUP_THRESHOLD}); canonical type is "
                    f"'{canon_name}' (richer: more attributes/relationships and/or "
                    f"descriptions) -- retrieval resolves '{alias_name}' concepts onto it; "
                    f"consider deleting '{alias_name}' from the ontology."
                ),
                severity="warning",
            )
        )
    return flags


def _vacuous_flags(ontology: Ontology) -> list[FlagOntologyIssueOp]:
    flags: list[FlagOntologyIssueOp] = []
    for et in ontology.types:
        if _is_vacuous(et.name, et.description):
            flags.append(
                FlagOntologyIssueOp(
                    target=et.name,
                    issue=f"type '{et.name}' has a vacuous name/description; should not be reused as-is",
                    severity="warning",
                )
            )
        for attr in et.attributes:
            if _is_vacuous(attr.name, attr.description):
                flags.append(
                    FlagOntologyIssueOp(
                        target=f"{et.name}.{attr.name}",
                        issue=(
                            f"attribute '{et.name}.{attr.name}' is vacuous (generic name or "
                            f"placeholder description); demote, don't reuse"
                        ),
                        severity="warning",
                    )
                )
        for rel in et.relationships:
            if _is_vacuous(rel.name, rel.description):
                flags.append(
                    FlagOntologyIssueOp(
                        target=f"{et.name}.{rel.name}",
                        issue=(
                            f"relationship '{et.name}.{rel.name}' is vacuous (generic name or "
                            f"placeholder description); demote, don't reuse"
                        ),
                        severity="warning",
                    )
                )
    return flags


def _datatype_smell_flags(ontology: Ontology) -> list[FlagOntologyIssueOp]:
    flags: list[FlagOntologyIssueOp] = []
    for et in ontology.types:
        for attr in et.attributes:
            if attr.datatype != "string":
                continue
            if any(tok in SMELL_WORDS for tok in raw_split(attr.name)):
                flags.append(
                    FlagOntologyIssueOp(
                        target=f"{et.name}.{attr.name}",
                        issue=(
                            f"attribute '{et.name}.{attr.name}' name implies a quantity but its "
                            f"datatype is 'string'; likely should be integer/number"
                        ),
                        severity="warning",
                    )
                )
    return flags


def audit(ontology: Ontology, embedder=None) -> list[FlagOntologyIssueOp]:
    """Deterministic hygiene audit over the current ontology snapshot:
    near-duplicate types, vacuous concepts, and datatype smells. `embedder`
    is optional and only ever strengthens a borderline near-dup call --
    the audit is fully deterministic and network-free when it's None.

    Returns every currently-detectable issue as a flat list; the caller
    (run.py) decides which CSV's patch each flag gets attached to (the spec
    says "the patch of the CSV that first touches the offending concept, or
    CSV 1 for whole-ontology issues found at startup" -- that placement
    policy belongs to the orchestrator, not this pure detection function).
    """
    return [
        *_near_duplicate_flags(ontology, embedder),
        *_vacuous_flags(ontology),
        *_datatype_smell_flags(ontology),
    ]
