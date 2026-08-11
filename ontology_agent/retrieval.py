"""Hybrid candidate retrieval over the ontology's concept cards.

Spec: DESIGN.md §5.

Recall is done in two cheap, non-LLM stages that both scale independently of
ontology size:
  1. lexical (hand-rolled BM25) + embedding (cosine over a flat matrix today,
     a drop-in for FAISS/pgvector ANN tomorrow) narrow thousands of concepts
     down to a per-column top-k;
  2. a few deterministic priors (datatype / shape) nudge that ranking using
     signal the column profiler already computed for free.

The LLM (decide.py) never sees more than `k` cards per column, so prompt size
is O(k) regardless of how large the ontology grows.

Everything here is pure stdlib: `math` for cosine, hand-rolled BM25, `re` for
tokenisation. No numpy.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .llm import Embedder
from .ontology import ConceptCard
from .profiler import ColumnProfile

# --------------------------------------------------------------------------
# Local tokenizer + synonym table.
#
# Part A's profiler.py owns the *canonical* tokenizer (with the full
# abbreviation table) for ColumnProfile.tokens — that's the query side of
# retrieval and we use it as-is. But nothing in Part A tokenizes
# ConceptCard.text for the *card* side of the index, and the task explicitly
# says not to add that helper to Part A. So retrieval.py carries its own
# small, self-contained tokenizer and a synonym table (the same six groups
# named in DESIGN.md §3) used only for BM25 matching and query expansion.
# --------------------------------------------------------------------------

_SPLIT_RE = re.compile(r"[^a-zA-Z0-9]+")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _tokenize(text: str) -> list[str]:
    """Split snake_case / camelCase / punctuation / spaces, lowercase."""
    tokens: list[str] = []
    for piece in _SPLIT_RE.split(text):
        if not piece:
            continue
        for sub in _CAMEL_BOUNDARY_RE.split(piece):
            if sub:
                tokens.append(sub.lower())
    return tokens


# --------------------------------------------------------------------------
# Stopword filtering.
#
# The hand-rolled BM25 below has no notion of a function word: every token
# that survives tokenization is treated as a content term. Card text is
# free-form English prose ("Size of the organization."), so function words
# like "of" appear in only a handful of cards -- which gives them a very
# HIGH idf (rare term = high signal, per the BM25 formula) exactly backwards
# from what we want. A column whose *name* happens to contain a function
# word (`country_of_origin` -> tokens `country`, `of`, `origin`) then scores
# a spurious match against any card whose description happens to contain
# "of", regardless of topical relevance (e.g. "Size of the organization"
# for `Organization.size`). This is a general retrieval defect, not specific
# to any one column name, so both sides of BM25 (card doc tokens indexed in
# ConceptIndex.rebuild, and query tokens scored in ConceptIndex.search) are
# filtered through the same small stoplist before indexing/scoring.
#
# Kept short and boring -- pure function words, not domain-meaningful short
# tokens (`id`, `no`, `url`, `usd` etc. all stay, since those carry real
# signal in this domain).
# --------------------------------------------------------------------------

_STOPWORDS: frozenset[str] = frozenset(
    {
        "of",
        "the",
        "a",
        "an",
        "and",
        "or",
        "in",
        "on",
        "at",
        "to",
        "for",
        "by",
        "with",
        "from",
        "as",
        "is",
        "are",
        "be",
        "its",
        "it",
        "this",
        "that",
        "primary",
        "e.g.",
    }
)


def _filter_stopwords(tokens: list[str]) -> list[str]:
    """Drop stopwords, but never return an empty list: if a token stream is
    ENTIRELY stopwords (e.g. a query that reduces to nothing after
    filtering), fall back to the unfiltered tokens rather than starving
    BM25 of every query term."""
    filtered = [tok for tok in tokens if tok not in _STOPWORDS]
    return filtered if filtered else tokens


# Synonym groups used to expand both card text and column queries before
# BM25 scoring, so `hq_city` and `headquarters` land near each other even
# though neither word appears in the other's raw text. Kept intentionally
# small and high-precision (mirrors DESIGN.md §3's list) rather than a full
# thesaurus, to avoid dragging in false positives.
_SYNONYM_GROUPS: list[set[str]] = [
    {"website", "url", "homepage", "web", "site"},
    {"founded", "established", "started", "inception"},
    {"sector", "industry", "vertical"},
    {"vendor", "supplier", "company", "organization"},
    {"price", "msrp", "cost"},
    {"manufacturer", "maker", "producer", "brand"},
    # Kept in sync with profiler.SYNONYM_GROUPS' new group (see the comment
    # there): this is the table that actually drives BM25 for the
    # `employee_count` -> `Organization.size` retrieval guarantee, since
    # this module tokenizes/expands the *card* side of the index with its
    # own table rather than profiler.py's.
    {"count", "size", "headcount", "employees", "employee"},
]

_SYNONYM_LOOKUP: dict[str, frozenset[str]] = {}
for _group in _SYNONYM_GROUPS:
    for _word in _group:
        _SYNONYM_LOOKUP[_word] = frozenset(_group - {_word})


def _expand(tokens: list[str]) -> list[str]:
    """Return extra synonym tokens for the given token list, each added once."""
    seen = set(tokens)
    extra: list[str] = []
    for tok in tokens:
        mates = _SYNONYM_LOOKUP.get(tok)
        if not mates:
            continue
        for mate in mates:
            if mate not in seen:
                extra.append(mate)
                seen.add(mate)
    return extra


# --------------------------------------------------------------------------
# Hand-rolled BM25 (k1=1.5, b=0.75 per spec).
# --------------------------------------------------------------------------


class _Bm25Index:
    """A tiny inverted-index BM25 scorer over a fixed set of documents.

    Built once per ConceptIndex.rebuild() call; queried many times (once per
    column). Runs off term->doc frequency counts, not a dense matrix, so it
    is the same shape of implementation a real inverted index would use.
    """

    def __init__(self, doc_tokens: dict[str, list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._doc_len: dict[str, int] = {doc_id: len(toks) for doc_id, toks in doc_tokens.items()}
        self._term_freq: dict[str, dict[str, int]] = {}
        for doc_id, toks in doc_tokens.items():
            counts: dict[str, int] = {}
            for tok in toks:
                counts[tok] = counts.get(tok, 0) + 1
            self._term_freq[doc_id] = counts
        n_docs = len(doc_tokens)
        self.avgdl = (sum(self._doc_len.values()) / n_docs) if n_docs else 0.0
        doc_freq: dict[str, int] = {}
        for toks in doc_tokens.values():
            for term in set(toks):
                doc_freq[term] = doc_freq.get(term, 0) + 1
        self._idf: dict[str, float] = {
            term: math.log((n_docs - freq + 0.5) / (freq + 0.5) + 1.0) for term, freq in doc_freq.items()
        }

    def score(self, doc_id: str, query_terms: set[str]) -> float:
        tf = self._term_freq.get(doc_id, {})
        dl = self._doc_len.get(doc_id, 0)
        if not tf or self.avgdl == 0:
            return 0.0
        total = 0.0
        for term in query_terms:
            f = tf.get(term, 0)
            if f == 0:
                continue
            idf = self._idf.get(term, 0.0)
            denom = f + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
            total += idf * (f * (self.k1 + 1)) / denom
        return total


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity, clamped to [0, 1] (negative similarity treated as 0
    so the signal stays in the same range as the other three)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return max(0.0, dot / (norm_a * norm_b))


# --------------------------------------------------------------------------
# Datatype / shape priors.
# --------------------------------------------------------------------------

_COMPATIBLE_DATATYPE_PAIRS = {frozenset({"integer", "number"}), frozenset({"date", "datetime"})}


def datatype_compatibility(query_datatype: str, query_freetext: bool, card_datatype: str | None) -> float:
    """The `datatype_prior` signal from DESIGN.md §5's table.

    Public (not `_`-prefixed) because decide.py's `datatype_conflict` gate
    (§6) needs the exact same hard-conflict definition used at retrieval
    time — duplicating the rule there would let the two drift apart.

    `card_datatype` is None for type/relationship cards (they carry no
    datatype). There's no such case in the spec's table; we treat it as
    neutral ("string-like/unknown") rather than a match or a conflict, since
    a relationship candidate is neither confirmed nor ruled out by the
    column's inferred datatype.
    """
    if card_datatype is None:
        return 0.15
    if query_datatype == card_datatype:
        return 1.0
    if frozenset({query_datatype, card_datatype}) in _COMPATIBLE_DATATYPE_PAIRS:
        return 0.6

    numeric_family = {"integer", "number", "boolean"}

    # A column whose *own* inferred datatype is confidently numeric/boolean
    # (the profiler already required >=90% of its values to parse that way)
    # being reused against a `string`-typed attribute is DESIGN.md §6's own
    # "canonical case" for datatype_conflict (`employee_count` vs
    # `Organization.size:string`). The query side carries no ambiguity here
    # -- it's unconditionally a hard conflict.
    if query_datatype in numeric_family and card_datatype == "string":
        return 0.0

    # The mirrored direction is §5's own explicit example ("number attr vs
    # freetext column") and is deliberately softer: a plain `string` column
    # can legitimately be reused against a numeric attribute (e.g. a zip
    # code stored as text) -- only a genuinely prose-like (freetext) column
    # is a real conflict there.
    if query_datatype == "string" and card_datatype in numeric_family:
        return 0.0 if query_freetext else 0.15

    # Everything else that touches "string" on either side (datetime vs
    # string, boolean vs string when the numeric-family check above didn't
    # already catch it, etc.) is the softer, ambiguous "string <-> anything"
    # bucket rather than a hard conflict.
    if card_datatype == "string" or query_datatype == "string":
        return 0.15
    return 0.0


def shape_prior(shape: str | None, card: ConceptCard) -> float:
    """The `shape_prior` signal from DESIGN.md §5's table."""
    if shape is None:
        return 0.0
    name = card.name.lower()
    if shape == "url" and any(k in name for k in ("url", "website", "homepage", "site")):
        return 1.0
    if shape == "email" and "email" in name:
        return 1.0
    if shape == "iso_datetime" and card.datatype in {"date", "datetime"}:
        return 1.0
    # hex_id / currency have no rule in the spec's table -> neutral 0.0.
    return 0.0


# --------------------------------------------------------------------------
# Public retrieval types.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ColumnQuery:
    """The retrieval query built from a single ColumnProfile."""

    text: str
    tokens: list[str]
    datatype: str
    shape: str | None
    freetext: bool

    @classmethod
    def from_profile(cls, profile: ColumnProfile) -> "ColumnQuery":
        # This exact string format is part of the contract: it feeds the
        # Embedder's disk cache key, so any drift here silently breaks
        # cache hits and the "reruns are deterministic" guarantee in §4.
        sample_preview = ", ".join(profile.samples[:3])
        text = f"column {profile.name} ({profile.inferred_datatype}) values: {sample_preview}"
        return cls(
            text=text,
            tokens=list(profile.tokens),
            datatype=profile.inferred_datatype,
            shape=profile.shape,
            freetext=profile.freetext,
        )


@dataclass(frozen=True)
class TypeScope:
    """Restricts a search to a CSV's subject type and what it can reach.

    "In scope" = the subject type's own card, its own attributes/relationships,
    plus the type cards (and *their* attributes/relationships) reachable as
    the range of one of the subject type's relationships. Everything else is
    still searchable (a cross-type reuse must remain findable, per §5) but is
    demoted.
    """

    subject_type: str


@dataclass
class Candidate:
    card: ConceptCard
    score: float
    signals: dict[str, float]  # bm25, embedding, datatype_prior, shape_prior
    # Set when this candidate is the result of alias resolution (fix plan
    # point 2): the id of the non-canonical twin card whose score won the
    # merge, e.g. "Company.url" when `card` is `Organization.website`. None
    # for a candidate that was never part of a near-duplicate-type pair.
    # New field, default None, added after the original three so every
    # existing positional `Candidate(card, score, signals)` call keeps working.
    aliased_from: str | None = None


# Combination weights, per DESIGN.md §5's table.
_WEIGHT_BM25 = 0.35
_WEIGHT_EMBEDDING = 0.40
_WEIGHT_DATATYPE = 0.15
_WEIGHT_SHAPE = 0.10

# Post-scoring adjustments (§5).
VACUOUS_DEMOTION = 0.15  # vacuous cards are "demoted, never reused"
OUT_OF_SCOPE_DEMOTION = 0.6  # cross-type reuse stays findable, just deprioritised
# A near-duplicate-type twin concept with NO canonical counterpart to
# resolve to (fix plan point 2's "if a twin concept has no canonical
# counterpart, keep it but demote hard"). Much harsher than
# OUT_OF_SCOPE_DEMOTION: the audit has already told us this whole type
# should be consolidated away, not merely that it's a different type.
ORPHAN_TWIN_DEMOTION = 0.25


class ConceptIndex:
    """Per-concept retrieval index: one BM25 doc + one embedding per card."""

    def __init__(self, cards: list[ConceptCard], embedder: Embedder | None) -> None:
        self.embedder = embedder
        self.cards: list[ConceptCard] = []
        self._bm25: _Bm25Index | None = None
        self._embeddings: dict[str, list[float]] = {}
        self.rebuild(cards)

    def rebuild(self, cards: list[ConceptCard]) -> None:
        """Recompute the index. Called once at startup and again after every
        applied patch, since patches change (and add to) the card set."""
        self.cards = list(cards)
        doc_tokens: dict[str, list[str]] = {}
        for card in self.cards:
            base = _filter_stopwords(_tokenize(card.text))
            doc_tokens[card.id] = base + _expand(list(dict.fromkeys(base)))
        self._bm25 = _Bm25Index(doc_tokens)

        self._embeddings = {}
        if self.embedder is not None and self.cards:
            # Embedder.embed degrades gracefully (returns [] on failure per
            # §4) -- if that happens we simply have no embedding signal and
            # search() renormalises the remaining weights.
            vectors = self.embedder.embed([card.text for card in self.cards])
            if vectors and len(vectors) == len(self.cards):
                self._embeddings = {card.id: vec for card, vec in zip(self.cards, vectors)}

    def _in_scope_ids(self, scope: TypeScope) -> set[str]:
        subject = scope.subject_type
        reachable_types = {subject}
        for card in self.cards:
            if card.kind == "relationship" and card.owner_type == subject and card.range:
                reachable_types.add(card.range)
        ids: set[str] = set()
        for card in self.cards:
            if card.kind == "type" and card.name in reachable_types:
                ids.add(card.id)
            elif card.owner_type is not None and card.owner_type in reachable_types:
                ids.add(card.id)
        return ids

    def search(self, q: ColumnQuery, k: int = 8, scope: TypeScope | None = None) -> list[Candidate]:
        if not self.cards or self._bm25 is None:
            return []

        query_tokens_unique = _filter_stopwords(list(dict.fromkeys(q.tokens)))
        query_terms = set(query_tokens_unique) | set(_expand(query_tokens_unique))

        raw_bm25 = {card.id: self._bm25.score(card.id, query_terms) for card in self.cards}
        max_bm25 = max(raw_bm25.values(), default=0.0)

        embeddings_usable = self.embedder is not None and bool(self._embeddings)
        query_vec: list[float] = []
        if embeddings_usable:
            vectors = self.embedder.embed([q.text])
            query_vec = vectors[0] if vectors else []
            if not query_vec:
                embeddings_usable = False

        in_scope_ids = self._in_scope_ids(scope) if scope is not None else None

        candidates: list[Candidate] = []
        for card in self.cards:
            bm25_signal = (raw_bm25[card.id] / max_bm25) if max_bm25 > 0 else 0.0
            embedding_signal = _cosine(query_vec, self._embeddings.get(card.id, [])) if embeddings_usable else 0.0
            datatype_signal = datatype_compatibility(q.datatype, q.freetext, card.datatype)
            shape_signal = shape_prior(q.shape, card)

            if embeddings_usable:
                combined = (
                    _WEIGHT_BM25 * bm25_signal
                    + _WEIGHT_EMBEDDING * embedding_signal
                    + _WEIGHT_DATATYPE * datatype_signal
                    + _WEIGHT_SHAPE * shape_signal
                )
            else:
                # Embedder unavailable (no embedder configured, or the
                # endpoint degraded and returned []): drop its weight and
                # renormalise the remaining three to sum to 1, per §5.
                remaining_weight = _WEIGHT_BM25 + _WEIGHT_DATATYPE + _WEIGHT_SHAPE
                combined = (
                    _WEIGHT_BM25 * bm25_signal + _WEIGHT_DATATYPE * datatype_signal + _WEIGHT_SHAPE * shape_signal
                ) / remaining_weight

            score = combined
            if card.vacuous:
                score *= VACUOUS_DEMOTION
            if card.orphan_twin:
                score *= ORPHAN_TWIN_DEMOTION
            if in_scope_ids is not None:
                # Scope an alias card by what it RESOLVES TO, not by the
                # twin type it happens to live on. A twin attribute that
                # aliases onto the in-scope canonical type must not eat the
                # 0.6x out-of-scope penalty on top of everything else --
                # that penalty already dragged Company.name (0.6x) below
                # Organization.size in the pre-fix `vendor` misroute (fix
                # plan point 2: an alias "must not compete with the
                # canonical concept -- it must resolve to it", which only
                # holds if it's scoped as the canonical concept too).
                effective_id = card.aliased_to or card.id
                if effective_id not in in_scope_ids:
                    score *= OUT_OF_SCOPE_DEMOTION

            candidates.append(
                Candidate(
                    card=card,
                    score=score,
                    signals={
                        "bm25": bm25_signal,
                        "embedding": embedding_signal,
                        "datatype_prior": datatype_signal,
                        "shape_prior": shape_signal,
                    },
                )
            )

        # --- alias resolution: merge twin-concept candidates onto their
        # canonical concept (fix plan point 2) -------------------------
        # A card whose ConceptCard.aliased_to is set (stamped by
        # ontology.build_cards from resolve_concept_aliases) must not
        # compete with its canonical concept as a separate ranked entry --
        # it must resolve to it. Key every candidate by its "effective id"
        # (aliased_to if set, else its own id) and keep whichever raw
        # candidate scored higher, always surfacing the CANONICAL
        # ConceptCard object as `.card` (never the twin's), so a downstream
        # `reuse` decision can never target the alias id. `self.cards`
        # iterates in ontology.types order, so for an unaliased id the
        # canonical/direct candidate is always seen (and keyed) before any
        # alias that might resolve onto it -- ties (score equal, no `>`)
        # therefore keep the canonical's own candidate rather than the twin.
        by_card_id = {card.id: card for card in self.cards}
        resolved: dict[str, Candidate] = {}
        for cand in candidates:
            is_alias = cand.card.aliased_to is not None
            key = cand.card.aliased_to if is_alias else cand.card.id
            current = resolved.get(key)
            if current is not None and cand.score <= current.score:
                continue  # lower (or tied) score loses the merge, dropped
            canon_card = by_card_id.get(key, cand.card) if is_alias else cand.card
            signals = cand.signals
            aliased_from = None
            if is_alias:
                aliased_from = cand.card.id
                # Numeric-only marker (report.py rounds every signal value)
                # so a reviewer scanning the JSON report's per-candidate
                # breakdown can see this entry came from alias resolution,
                # even though the alias id itself can't live in `signals`
                # (a float dict) -- the id is carried on `aliased_from` and
                # surfaced in decision.rationale by decide.py instead.
                signals = {**cand.signals, "aliased_from_twin": 1.0}
            resolved[key] = Candidate(card=canon_card, score=cand.score, signals=signals, aliased_from=aliased_from)
        candidates = list(resolved.values())

        # Deterministic ordering: score desc, then card id asc as a stable
        # tie-break so reruns (and tests) never depend on dict/set iteration
        # order.
        candidates.sort(key=lambda c: (-c.score, c.card.id))
        return candidates[:k]
