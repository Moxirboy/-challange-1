"""Deterministic CSV column profiling + junk prefilter.

Nothing in this module calls an LLM or does anything non-deterministic --
that's the point: it's the cheap, always-available layer that narrows down
what actually needs a model call (`decide.py`), and the layer that gives
`retrieval.py` a normalised vocabulary (tokens + synonym expansions) to
match column names against ontology concepts under different surface
spellings (`hq_city` vs `headquarters city`, `msrp` vs `list price`, ...).

The tokenizer and synonym table live here (not in ontology.py) because the
spec ties them to CSV column understanding first; `ontology.py`'s hygiene
audit imports `canonical_attribute_id` / `shares_synonym_cluster` from here
so the two modules share one vocabulary instead of drifting apart.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable

from .models import DATATYPES

# --------------------------------------------------------------------------
# Tokenisation + abbreviation/synonym vocabulary
# --------------------------------------------------------------------------

# Column/attribute-name abbreviations, expanded to their full-word form so
# lexical retrieval can connect e.g. `hq_city` -> "headquarters city" and
# `msrp` -> "manufacturer suggested retail price list price".
ABBREVIATIONS: dict[str, str] = {
    "hq": "headquarters",
    "url": "web address link",
    "msrp": "manufacturer suggested retail price list price",
    "sku": "stock keeping unit",
    "qty": "quantity",
    "amt": "amount",
    "dt": "date time",
    "ts": "date time",
    "num": "number",
    "no": "number",
    "pct": "percent",
    "org": "organization",
    "mfr": "manufacturer",
    "addr": "address",
    "tel": "telephone",
    "yr": "year",
    "id": "identifier",
    "desc": "description",
    "cnt": "count",
    "emp": "employee",
}

# Groups of interchangeable concept words. Used two ways:
#   - BM25 / retrieval: a query or card's token set is expanded to include
#     every member of any group it touches, so different surface words find
#     the same concept.
#   - ontology.py's near-duplicate audit: attribute names are canonicalised
#     to "which group (if any) do they belong to", turning fuzzy synonymy
#     into an exact-set Jaccard comparison.
# Note `vendor`/`supplier`/`company`/`organization` sharing a group is what
# lets the audit recognise "Organization" and "Company" as the same concept
# by *type name*, not just by attribute overlap.
SYNONYM_GROUPS: list[set[str]] = [
    {"website", "url", "homepage", "web", "site"},
    {"founded", "established", "started", "inception"},
    {"sector", "industry", "vertical"},
    {"vendor", "supplier", "company", "organization"},
    {"price", "msrp", "cost", "list"},
    {"manufacturer", "maker", "producer", "brand"},
    # `employee_count` (integer) vs the seed ontology's `Organization.size`
    # (string, described as "Size of the organization") is DESIGN.md's
    # canonical datatype_conflict escalation -- but only if retrieval can
    # find `Organization.size` for a column literally named "employee
    # count" in the first place. Deliberately narrow: no "number"/"quantity"
    # here, since the `num`/`no`/`qty` abbreviations in ABBREVIATIONS would
    # then glue every unrelated numeric-ish column onto `size`.
    {"count", "size", "headcount", "employees", "employee"},
]

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^0-9a-zA-Z]+")


def raw_split(name: str) -> list[str]:
    """Split a name into lowercase word pieces on snake_case, camelCase,
    space, and punctuation boundaries -- *without* abbreviation expansion.

    This is the shared first stage of `tokenize()`, but it's also exposed
    on its own because some callers (ontology.py's datatype-smell scan)
    need to match whole *words*, not substrings -- e.g. `country` must
    never be treated as containing `count`.
    """
    with_boundaries = _CAMEL_BOUNDARY.sub(" ", name)
    normalized = _NON_ALNUM.sub(" ", with_boundaries)
    return [p.lower() for p in normalized.split() if p]


def tokenize(name: str) -> list[str]:
    """Normalise + abbreviation-expand a column/attribute name into tokens."""
    tokens: list[str] = []
    for piece in raw_split(name):
        expansion = ABBREVIATIONS.get(piece)
        if expansion:
            tokens.extend(expansion.split())
        else:
            tokens.append(piece)
    return tokens


def expand_synonyms(tokens: Iterable[str]) -> set[str]:
    """Union a token set with every synonym-group member any token belongs to.

    Used to build the BM25 query/document term sets in retrieval.py.
    """
    token_set = set(tokens)
    expanded = set(token_set)
    for group in SYNONYM_GROUPS:
        if token_set & group:
            expanded |= group
    return expanded


def canonical_attribute_id(name: str) -> str:
    """Reduce an attribute name to a canonical id: names in the same synonym
    group map to the same id, so two attribute-name *sets* can be compared
    with plain Jaccard instead of fuzzy token overlap. Names outside any
    group fall back to their own sorted token string (still order-insensitive,
    still exact-match only against another name with the identical tokens).
    """
    tokens = set(tokenize(name))
    for group in SYNONYM_GROUPS:
        if tokens & group:
            return "|".join(sorted(group))
    return " ".join(sorted(tokens))


def shares_synonym_cluster(a: str, b: str) -> bool:
    """True when two names both touch the same synonym group -- e.g.
    "Organization" and "Company" both hit the vendor/supplier/company/
    organization group. Used as a strong, deterministic signal for type
    name-similarity (ontology.py's near-dup audit) ahead of any fuzzy
    string-distance fallback.
    """
    ta, tb = set(tokenize(a)), set(tokenize(b))
    return any((ta & g) and (tb & g) for g in SYNONYM_GROUPS)


# --------------------------------------------------------------------------
# Datatype inference
# --------------------------------------------------------------------------

_BOOL_VALUES = {"true", "false", "yes", "no"}
_INT_RE = re.compile(r"[+-]?\d+")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _ratio(values: list[str], predicate: Callable[[str], bool]) -> float:
    if not values:
        return 0.0
    hits = sum(1 for v in values if predicate(v))
    return hits / len(values)


def _is_bool(v: str) -> bool:
    return v.lower() in _BOOL_VALUES


def _is_int(v: str) -> bool:
    return _INT_RE.fullmatch(v) is not None


def _is_number(v: str) -> bool:
    try:
        float(v)
        return True
    except ValueError:
        return False


def _is_date(v: str) -> bool:
    if not _DATE_RE.fullmatch(v):
        return False
    try:
        date.fromisoformat(v)
        return True
    except ValueError:
        return False


def _is_datetime(v: str) -> bool:
    # Require an explicit time component so a plain "2026-03-14" is never
    # double-counted as a datetime once the (earlier) date check has already
    # claimed it -- `datetime.fromisoformat` alone would happily accept a
    # bare date too.
    if "T" not in v and " " not in v:
        return False
    try:
        datetime.fromisoformat(v)
        return True
    except ValueError:
        return False


def infer_datatype(values: list[str]) -> str:
    """Try, in order, boolean -> integer -> number -> date -> datetime ->
    string, requiring >=90% agreement across non-null values. An empty
    column (no non-null values) is "string" -- callers should check
    `is_empty` separately rather than reading meaning into that default.
    """
    if not values:
        return "string"
    if _ratio(values, _is_bool) >= 0.9:
        return "boolean"
    if _ratio(values, _is_int) >= 0.9:
        return "integer"
    if _ratio(values, _is_number) >= 0.9:
        return "number"
    if _ratio(values, _is_date) >= 0.9:
        return "date"
    if _ratio(values, _is_datetime) >= 0.9:
        return "datetime"
    return "string"


assert DATATYPES == {"string", "integer", "number", "boolean", "date", "datetime"}, (
    "infer_datatype()'s return values are hardcoded to this set; models.DATATYPES changed"
)


# --------------------------------------------------------------------------
# Shape detection
# --------------------------------------------------------------------------

_URL_RE = re.compile(r"https?://\S+", re.I)
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
# Requires at least one a-f letter so pure-digit strings (years, counts)
# never register as hex ids; requires length >=6 to avoid tiny coincidental
# matches.
_HEX_ID_RE = re.compile(r"(?=[0-9a-f]*[a-f])[0-9a-f]{6,}", re.I)
_CURRENCY_RE = re.compile(r"[$€£]\s?-?\d[\d,]*(\.\d+)?")


def detect_shape(values: list[str]) -> str | None:
    if not values:
        return None
    if _ratio(values, lambda v: bool(_URL_RE.fullmatch(v))) >= 0.9:
        return "url"
    if _ratio(values, lambda v: bool(_EMAIL_RE.fullmatch(v))) >= 0.9:
        return "email"
    if _ratio(values, _is_datetime) >= 0.9:
        return "iso_datetime"
    if _ratio(values, lambda v: bool(_HEX_ID_RE.fullmatch(v))) >= 0.9:
        return "hex_id"
    if _ratio(values, lambda v: bool(_CURRENCY_RE.fullmatch(v))) >= 0.9:
        return "currency"
    return None


# --------------------------------------------------------------------------
# Column / CSV profiles
# --------------------------------------------------------------------------


@dataclass
class PrefilterVerdict:
    action: str  # currently always "exclude" -- absence (None) means "send to the LLM"
    reason: str  # empty_column | unnamed_column | surrogate_key | sync_metadata
    evidence: str


@dataclass
class ColumnProfile:
    name: str
    position: int
    tokens: list[str]
    non_null: int
    null_rate: float
    distinct: int
    uniqueness: float
    inferred_datatype: str
    shape: str | None
    samples: list[str]
    avg_len: float
    is_empty: bool
    is_constant: bool
    freetext: bool
    entity_like: bool
    prefilter: PrefilterVerdict | None


@dataclass
class CsvProfile:
    path: str
    columns: list[ColumnProfile]
    row_count: int
    raw_header: list[str]
    sample_rows: list[dict[str, str]]  # first 3 raw rows, header -> value


def _non_null_values(rows: list[dict[str, str]], name: str) -> list[str]:
    """Stripped, non-empty values for one column, in row order. A missing
    field or a field that's empty/whitespace-only counts as null -- this is
    what `non_null`, `distinct`, datatype inference, and shape detection all
    operate on.
    """
    out: list[str] = []
    for row in rows:
        v = row.get(name)
        if v is None:
            continue
        s = v.strip()
        if s:
            out.append(s)
    return out


def _dedup_samples(values: list[str], limit: int = 5) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
        if len(out) >= limit:
            break
    return out


def _looks_entity_like(values: list[str], datatype: str, uniqueness: float, distinct: int, avg_len: float) -> bool:
    """Heuristic signal (not fully pinned down by the spec): a column reads
    as *naming another entity* -- rather than holding a free literal -- when
    it's short, string-typed, repeats across rows (so it isn't ~all-unique),
    and looks proper-noun-ish (title case). decide.py can use this to prefer
    a relationship over a plain string attribute (policy rule #3).
    """
    if datatype != "string" or distinct <= 1 or avg_len >= 40 or uniqueness > 0.8:
        return False
    sample = values[:10]
    if not sample:
        return False
    titled = sum(1 for v in sample if v[:1].isupper())
    return (titled / len(sample)) >= 0.6


# --------------------------------------------------------------------------
# Junk prefilter
# --------------------------------------------------------------------------

SYNC_METADATA_NAMES = {
    "updated_at",
    "created_at",
    "modified_at",
    "last_modified",
    "_rev",
    "__v",
    "etl_loaded_at",
    "ingested_at",
    "row_number",
    "index",
}
_SURROGATE_NAME_RE = re.compile(r"^_?(id|_id|uuid|guid|pk|row_?id)$", re.I)
_UNNAMED_RE = re.compile(r"^unnamed[:\s_]*\d*$", re.I)


def _prefilter(name: str, col: ColumnProfile) -> PrefilterVerdict | None:
    if col.is_empty:
        return PrefilterVerdict("exclude", "empty_column", f"column '{name}' has no non-null values")

    stripped = name.strip()
    if stripped == "" or _UNNAMED_RE.match(stripped):
        return PrefilterVerdict(
            "exclude", "unnamed_column", f"header '{name}' is blank or an auto-generated placeholder"
        )

    is_idish = bool(_SURROGATE_NAME_RE.match(stripped)) or stripped.startswith("_")
    if is_idish and col.uniqueness == 1.0:
        return PrefilterVerdict(
            "exclude",
            "surrogate_key",
            f"name '{name}' is id-like and every non-null value is unique ({col.distinct}/{col.non_null})",
        )

    if stripped.lower() in SYNC_METADATA_NAMES and (col.is_constant or col.shape == "iso_datetime"):
        why = "constant across rows" if col.is_constant else "an ISO timestamp"
        return PrefilterVerdict(
            "exclude", "sync_metadata", f"name '{name}' is a known sync-metadata field and is {why}"
        )

    return None


def _build_column_profile(name: str, position: int, values: list[str], row_count: int) -> ColumnProfile:
    non_null = len(values)
    null_rate = 1.0 - (non_null / row_count) if row_count else 0.0
    distinct = len(set(values))
    uniqueness = (distinct / non_null) if non_null else 0.0
    inferred_datatype = infer_datatype(values)
    avg_len = (sum(len(v) for v in values) / non_null) if non_null else 0.0
    is_empty = non_null == 0
    is_constant = distinct == 1 and non_null > 0
    freetext = avg_len > 25 and uniqueness > 0.8 and inferred_datatype == "string"

    col = ColumnProfile(
        name=name,
        position=position,
        tokens=tokenize(name),
        non_null=non_null,
        null_rate=null_rate,
        distinct=distinct,
        uniqueness=uniqueness,
        inferred_datatype=inferred_datatype,
        shape=detect_shape(values),
        samples=_dedup_samples(values),
        avg_len=avg_len,
        is_empty=is_empty,
        is_constant=is_constant,
        freetext=freetext,
        entity_like=_looks_entity_like(values, inferred_datatype, uniqueness, distinct, avg_len),
        prefilter=None,
    )
    col.prefilter = _prefilter(name, col)
    return col


def profile_csv(path: str | Path) -> CsvProfile:
    """Read a CSV and profile every column: datatype, shape, cardinality,
    and a junk-prefilter verdict. Pure stdlib `csv`, no pandas.
    """
    path = Path(path)
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        raw_header = list(reader.fieldnames or [])
        rows = [dict(r) for r in reader]

    row_count = len(rows)
    columns = [
        _build_column_profile(name, position, _non_null_values(rows, name), row_count)
        for position, name in enumerate(raw_header)
    ]
    return CsvProfile(
        path=str(path),
        columns=columns,
        row_count=row_count,
        raw_header=raw_header,
        sample_rows=rows[:3],
    )
