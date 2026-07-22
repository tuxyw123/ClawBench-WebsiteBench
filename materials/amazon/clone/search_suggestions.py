from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Mapping
from urllib.parse import parse_qsl

from search_catalog import (
    DEFAULT_SEARCH_DEPARTMENT,
    SEARCH_DEPARTMENTS,
    SOURCE_DEPARTMENTS,
    SOURCE_DEPARTMENT_SLUG_BY_RAIL,
)


INVALID_PERCENT_ESCAPE_RE = re.compile(r"%(?![0-9a-fA-F]{2})")
TOKEN_RE = re.compile(r"[a-z0-9]+")
MAX_RAW_QUERY_LENGTH = 1_024
MAX_QUERY_LENGTH = 120
MAX_QUERY_FIELDS = 4
MAX_SUGGESTIONS = 10
MAX_SUGGESTION_VALUE_LENGTH = 180


class SearchSuggestionValidationError(ValueError):
    """Raised when the public suggestion request is ambiguous or malformed."""


@dataclass(frozen=True)
class SearchSuggestionRequest:
    query: str
    department: str = DEFAULT_SEARCH_DEPARTMENT


@dataclass(frozen=True)
class SearchSuggestion:
    value: str
    kind: Literal["query", "department", "product"]
    department: str | None = None


# These are shopping phrases already represented by the frozen local catalog,
# public routes, or directly captured PDP identity.  They improve discovery but
# do not introduce prices, availability, ratings, or other product facts.
CATALOG_QUERY_TERMS: tuple[tuple[str, str | None], ...] = (
    ("portable ssd", "computers"),
    ("portable ssd 1tb", "computers"),
    ("portable ssd 2tb", "computers"),
    ("samsung portable ssd", "computers"),
    ("sandisk portable ssd", "computers"),
    ("external solid state drive", "computers"),
    ("instant pot", "home-kitchen"),
    ("picture frames", "home-kitchen"),
    ("trading card binder", "toys-games"),
    ("ipad screen protector", "computers"),
    ("acne patches", "beauty-personal-care"),
    ("jansport backpack", None),
    ("air filter", None),
    ("top picks singapore", None),
)


def _clean_text(value: str, *, label: str, limit: int) -> str:
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise SearchSuggestionValidationError(f"{label} contains control characters")
    cleaned = " ".join(value.strip().split())
    if len(cleaned) > limit:
        raise SearchSuggestionValidationError(f"{label} is too long")
    return cleaned


def parse_suggestion_request(raw_query: str) -> SearchSuggestionRequest:
    if len(raw_query) > MAX_RAW_QUERY_LENGTH:
        raise SearchSuggestionValidationError("suggestion query is too long")
    if INVALID_PERCENT_ESCAPE_RE.search(raw_query):
        raise SearchSuggestionValidationError("suggestion query has invalid encoding")
    try:
        pairs = parse_qsl(
            raw_query,
            keep_blank_values=True,
            encoding="utf-8",
            errors="strict",
            max_num_fields=MAX_QUERY_FIELDS,
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise SearchSuggestionValidationError("suggestion query is malformed") from exc

    unknown = {name for name, _ in pairs} - {"q", "i"}
    if unknown:
        raise SearchSuggestionValidationError("suggestion query has unsupported parameters")
    query_values = [value for name, value in pairs if name == "q"]
    department_values = [value for name, value in pairs if name == "i"]
    if len(query_values) != 1 or len(department_values) > 1:
        raise SearchSuggestionValidationError("suggestion query has duplicate parameters")

    query = _clean_text(query_values[0], label="suggestion query", limit=MAX_QUERY_LENGTH)
    department = (
        _clean_text(
            department_values[0],
            label="suggestion department",
            limit=64,
        )
        if department_values
        else DEFAULT_SEARCH_DEPARTMENT
    )
    if department not in SEARCH_DEPARTMENTS:
        raise SearchSuggestionValidationError("suggestion department is unsupported")
    return SearchSuggestionRequest(query=query, department=department)


def _normalized(value: str) -> str:
    return " ".join(TOKEN_RE.findall(unicodedata.normalize("NFKC", value).casefold()))


def _bounded_title(value: str) -> str:
    cleaned = " ".join(value.split())
    if len(cleaned) <= MAX_SUGGESTION_VALUE_LENGTH:
        return cleaned
    shortened = cleaned[: MAX_SUGGESTION_VALUE_LENGTH + 1].rsplit(" ", 1)[0]
    return shortened.rstrip(" ,.;:-") + "…"


def build_suggestion_corpus(
    catalog: Mapping[str, Mapping[str, Any]],
) -> tuple[SearchSuggestion, ...]:
    """Build a deterministic corpus from frozen department and product evidence."""

    candidates: list[SearchSuggestion] = []
    for department in SOURCE_DEPARTMENTS:
        candidates.append(
            SearchSuggestion(
                value=str(department["query"]),
                kind="department",
                department=str(department["slug"]),
            )
        )
    candidates.extend(
        SearchSuggestion(value=value, kind="query", department=department)
        for value, department in CATALOG_QUERY_TERMS
    )

    for product in catalog.values():
        title = product.get("title")
        placements = product.get("placements")
        if not isinstance(title, str) or not title.strip():
            continue
        department: str | None = None
        if isinstance(placements, list) and placements and isinstance(placements[0], Mapping):
            rail_key = placements[0].get("railKey")
            if isinstance(rail_key, str):
                department = SOURCE_DEPARTMENT_SLUG_BY_RAIL.get(rail_key)
        candidates.append(
            SearchSuggestion(
                value=_bounded_title(title),
                kind="product",
                department=department,
            )
        )

    deduplicated: list[SearchSuggestion] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = _normalized(candidate.value)
        if not key or key in seen:
            continue
        seen.add(key)
        deduplicated.append(candidate)
    return tuple(deduplicated)


def _match_rank(query: str, candidate: str) -> int | None:
    if candidate.startswith(query):
        return 0
    query_tokens = query.split()
    candidate_tokens = candidate.split()
    if len(query_tokens) == 1 and any(token.startswith(query) for token in candidate_tokens):
        return 1
    if query_tokens and all(token in candidate_tokens for token in query_tokens):
        return 2
    if query in candidate:
        return 3
    return None


def suggest_search_terms(
    request: SearchSuggestionRequest,
    corpus: Iterable[SearchSuggestion],
    *,
    limit: int = MAX_SUGGESTIONS,
) -> tuple[SearchSuggestion, ...]:
    if not isinstance(limit, int) or not 1 <= limit <= MAX_SUGGESTIONS:
        raise SearchSuggestionValidationError("suggestion limit is invalid")
    normalized_query = _normalized(request.query)
    if len(normalized_query) < 2:
        return ()

    kind_rank = {"query": 0, "department": 1, "product": 2}
    ranked: list[tuple[int, int, int, int, SearchSuggestion]] = []
    for source_index, candidate in enumerate(corpus):
        if (
            request.department != DEFAULT_SEARCH_DEPARTMENT
            and candidate.department != request.department
        ):
            continue
        normalized_candidate = _normalized(candidate.value)
        match_rank = _match_rank(normalized_query, normalized_candidate)
        if match_rank is None:
            continue
        ranked.append(
            (
                match_rank,
                kind_rank[candidate.kind],
                len(normalized_candidate),
                source_index,
                candidate,
            )
        )
    ranked.sort(key=lambda item: item[:4])
    return tuple(item[-1] for item in ranked[:limit])
