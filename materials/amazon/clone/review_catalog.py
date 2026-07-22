from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from html import escape
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlencode


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "review-evidence.json"
SOURCE_AGGREGATE_PROVENANCE = "source_snapshot_aggregate"
LOCAL_REVIEW_PROVENANCE = "local_user_review"
REVIEW_SORTS = frozenset({"recent", "helpful"})


@lru_cache(maxsize=1)
def _fixture() -> dict[str, Any]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if payload.get("schema") != "amazon-clone.review-evidence.v1":
        raise ValueError("Unsupported review evidence schema")
    products = payload.get("products")
    if not isinstance(products, dict):
        raise ValueError("Review evidence products must be an object")
    return payload


def supported_review_asins() -> tuple[str, ...]:
    """Return ASINs with source-observed review aggregates."""

    return tuple(_fixture()["products"])


def get_review_evidence(asin: str) -> dict[str, Any] | None:
    """Return a caller-safe copy of an ASIN's immutable source evidence."""

    product = _fixture()["products"].get(asin.upper())
    return deepcopy(product) if product is not None else None


def _bounded_text(value: object, field: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    result = " ".join(value.split())
    if not result:
        raise ValueError(f"{field} must not be empty")
    if len(result) > maximum:
        raise ValueError(f"{field} is too long")
    return result


def _boolean(review: Mapping[str, Any], field: str, default: bool) -> bool:
    value = review.get(field, default)
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _normalize_local_review(review: Mapping[str, Any]) -> dict[str, Any]:
    if review.get("provenance") != LOCAL_REVIEW_PROVENANCE:
        raise ValueError("Local reviews must carry provenance=local_user_review")

    rating = review.get("rating")
    if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5:
        raise ValueError("rating must be an integer from 1 to 5")
    helpful_count = review.get("helpful_count", 0)
    if (
        isinstance(helpful_count, bool)
        or not isinstance(helpful_count, int)
        or helpful_count < 0
    ):
        raise ValueError("helpful_count must be a non-negative integer")

    created_at = review.get("created_at", "")
    updated_at = review.get("updated_at", created_at)
    if created_at and not isinstance(created_at, str):
        raise ValueError("created_at must be text")
    if updated_at and not isinstance(updated_at, str):
        raise ValueError("updated_at must be text")
    review_id = review.get("id", "")
    if review_id and not isinstance(review_id, (str, int)):
        raise ValueError("id must be text or an integer")

    verified_purchase = _boolean(review, "verified_purchase", False)
    viewer_found_helpful = _boolean(review, "viewer_found_helpful", False)
    owned_by_viewer = _boolean(review, "owned_by_viewer", False)
    can_mark_helpful = _boolean(review, "can_mark_helpful", not owned_by_viewer)
    if owned_by_viewer and can_mark_helpful:
        raise ValueError("review owners cannot mark their own review helpful")

    return {
        "id": str(review_id),
        "provenance": LOCAL_REVIEW_PROVENANCE,
        "author_display_name": _bounded_text(
            review.get("author_display_name"), "author_display_name", maximum=128
        ),
        "rating": rating,
        "title": _bounded_text(review.get("title"), "title", maximum=200),
        "body": _bounded_text(review.get("body"), "body", maximum=10000),
        "created_at": created_at.strip(),
        "updated_at": updated_at.strip(),
        "verified_purchase": verified_purchase,
        "helpful_count": helpful_count,
        "viewer_found_helpful": viewer_found_helpful,
        "owned_by_viewer": owned_by_viewer,
        "can_mark_helpful": can_mark_helpful,
    }


def normalize_local_reviews(
    reviews: Iterable[Mapping[str, Any]], *, star: int | None = None
) -> list[dict[str, Any]]:
    """Validate local rows without allowing them to masquerade as source reviews."""

    if star is not None and (isinstance(star, bool) or star not in range(1, 6)):
        raise ValueError("star filter must be 1 through 5")
    normalized = [_normalize_local_review(review) for review in reviews]
    if star is not None:
        normalized = [review for review in normalized if review["rating"] == star]
    return normalized


def build_review_view(
    asin: str,
    local_reviews: Iterable[Mapping[str, Any]] = (),
    *,
    star: int | None = None,
    product_label: str | None = None,
    source_rating_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build a UI view while keeping source aggregates and local rows separate."""

    evidence = get_review_evidence(asin)
    known_product_label = None
    if product_label is not None:
        known_product_label = _bounded_text(
            product_label, "product_label", maximum=1000
        )
    if source_rating_summary is not None and not isinstance(
        source_rating_summary, Mapping
    ):
        raise ValueError("source_rating_summary must be a mapping")
    captured_summary = (
        evidence["rating_summary"]
        if evidence is not None
        else (dict(source_rating_summary) if source_rating_summary is not None else None)
    )
    if evidence is None and known_product_label is None and captured_summary is None:
        return None
    if star is not None and (isinstance(star, bool) or star not in range(1, 6)):
        raise ValueError("star filter must be 1 through 5")

    all_local = normalize_local_reviews(local_reviews)
    filtered_local = (
        all_local
        if star is None
        else [review for review in all_local if review["rating"] == star]
    )
    rating_total = sum(review["rating"] for review in all_local)
    local_average = round(rating_total / len(all_local), 1) if all_local else None
    local_by_star = {
        value: sum(review["rating"] == value for review in all_local)
        for value in range(5, 0, -1)
    }

    return {
        "asin": asin.upper(),
        "product_label": (
            evidence["product_label"] if evidence is not None else known_product_label
        ),
        "source_aggregate_available": captured_summary is not None,
        "source_rating_summary": captured_summary,
        "source_histogram": evidence.get("histogram") if evidence is not None else None,
        "source_topics": evidence.get("source_topics", []) if evidence is not None else [],
        "source_topic_provenance": (
            evidence.get("topic_provenance") if evidence is not None else None
        ),
        "source_excerpts": evidence.get("source_excerpts", []) if evidence is not None else [],
        "individual_review_evidence": (
            evidence["individual_review_evidence"]
            if evidence is not None
            else {
                "available": False,
                "missing_fields": [
                    "aggregate_rating",
                    "aggregate_count",
                    "author",
                    "title",
                    "date",
                    "rating",
                    "helpful_count",
                ],
            }
        ),
        "local_summary": {
            "rating": local_average,
            "review_count": len(all_local),
            "provenance": LOCAL_REVIEW_PROVENANCE,
            "count_by_star": local_by_star,
        },
        "owned_review": next(
            (review for review in all_local if review["owned_by_viewer"]), None
        ),
        "local_reviews": filtered_local,
        "active_star": star,
    }


def _review_href(
    asin: str,
    *,
    star: int | None,
    sort: str,
    base_path: str,
) -> str:
    query: dict[str, str] = {}
    if star is not None:
        query["reviewStar"] = str(star)
    if sort != "recent":
        query["reviewSort"] = sort
    suffix = f"?{urlencode(query)}" if query else ""
    return f"{base_path}{suffix}#customerReviews"


def _write_panel(
    view: Mapping[str, Any], account_name: str | None, form_error: str | None
) -> str:
    asin = str(view["asin"])
    error_html = (
        f'<div class="review-form-error" role="alert">{escape(form_error)}</div>'
        if form_error
        else ""
    )
    if account_name is None:
        signin_target = f"/product-reviews/{asin}#reviewComposer"
        return (
            '<section id="reviewComposer" class="review-write-panel review-signin-panel">'
            '<h3>Review this product</h3>'
            '<p>Share your experience with other shoppers.</p>'
            '<a class="review-signin-cta" href="/ap/signin?'
            + escape(urlencode({"openid.return_to": signin_target}), quote=True)
            + '">Sign in to write a review</a></section>'
        )

    owned = view["owned_review"]
    selected_rating = int(owned["rating"]) if owned else 5
    rating_options = "".join(
        f'<option value="{value}"{" selected" if value == selected_rating else ""}>'
        f"{value} out of 5 stars</option>"
        for value in range(5, 0, -1)
    )
    existing_title = escape(owned["title"] if owned else "", quote=True)
    existing_body = escape(owned["body"] if owned else "")
    return f"""
    <section id="reviewComposer" class="review-write-panel">
      <h3>{'Edit your review' if owned else 'Write a customer review'}</h3>
      <p>Posting as <strong>{escape(account_name)}</strong>. Your review is stored only in this local clone.</p>
      {error_html}
      <form class="review-write-form" method="post" action="/product-reviews/{asin}">
        <label>Overall rating<select name="rating" required>{rating_options}</select></label>
        <label>Add a headline<input name="headline" maxlength="200" required value="{existing_title}"></label>
        <label>Add a written review<textarea name="body" maxlength="10000" rows="6" required>{existing_body}</textarea></label>
        <button type="submit">{'Update review' if owned else 'Submit review'}</button>
      </form>
    </section>
    """


def _local_review_card(asin: str, review: Mapping[str, Any]) -> str:
    verified = (
        '<span class="review-verified">Verified Purchase</span>'
        if review["verified_purchase"]
        else ""
    )
    date = (
        f'<time datetime="{escape(review["created_at"], quote=True)}">'
        f'{escape(review["created_at"])}</time>'
        if review["created_at"]
        else ""
    )
    edited = (
        '<span class="review-edited">Edited</span>'
        if review["updated_at"] and review["updated_at"] != review["created_at"]
        else ""
    )
    if review["owned_by_viewer"]:
        helpful_action = (
            '<button class="review-helpful-button" type="button" disabled '
            'title="You cannot mark your own review as helpful">Your review</button>'
        )
    elif review["can_mark_helpful"]:
        pressed = "true" if review["viewer_found_helpful"] else "false"
        label = "Helpful ✓" if review["viewer_found_helpful"] else "Helpful"
        helpful_action = (
            f'<form class="review-helpful-form" method="post" action="/product-reviews/{asin}/helpful">'
            f'<input type="hidden" name="reviewId" value="{escape(review["id"], quote=True)}">'
            f'<button class="review-helpful-button" type="submit" aria-pressed="{pressed}">{label}</button>'
            "</form>"
        )
    else:
        helpful_action = ""

    return (
        '<article class="local-review-card" data-provenance="local_user_review" '
        f'data-viewer-found-helpful="{str(review["viewer_found_helpful"]).lower()}" '
        f'data-owned-by-viewer="{str(review["owned_by_viewer"]).lower()}" '
        f'data-can-mark-helpful="{str(review["can_mark_helpful"]).lower()}">'
        f'<div class="review-author">{escape(review["author_display_name"])}</div>'
        f'<div class="review-card-rating" aria-label="{review["rating"]} out of 5 stars">'
        f'{"★" * review["rating"]}{"☆" * (5 - review["rating"])}</div>'
        f'<h4>{escape(review["title"])}</h4>'
        f'<div class="review-card-meta">{verified}{date}{edited}</div>'
        f'<p>{escape(review["body"])}</p>'
        '<div class="review-card-actions">'
        f'<span class="review-helpful">{review["helpful_count"]} people found this helpful</span>'
        f"{helpful_action}</div></article>"
    )


def _source_rating_summary_html(summary: Mapping[str, Any]) -> str:
    """Render a captured aggregate without manufacturing a rating for zero reviews."""

    rating = summary.get("rating")
    rating_count = summary.get("rating_count")
    rating_count_display = summary.get("rating_count_display")
    if rating_count is None:
        if not isinstance(rating_count_display, str) or not rating_count_display.strip():
            raise ValueError(
                "source rating_count needs an exact integer or captured display copy"
            )
        display_count = rating_count_display.strip()
    elif (
        isinstance(rating_count, bool)
        or not isinstance(rating_count, int)
        or rating_count < 0
    ):
        raise ValueError("source rating_count must be a non-negative integer")
    else:
        display_count = (
            rating_count_display.strip()
            if isinstance(rating_count_display, str) and rating_count_display.strip()
            else f"{rating_count:,}"
        )

    if rating is None:
        if rating_count != 0:
            raise ValueError("a missing source rating is valid only with zero ratings")
        zero_copy = summary.get("zero_rating_copy")
        if not isinstance(zero_copy, str) or not zero_copy.strip():
            zero_copy = f"There are {rating_count:,} customer reviews"
        return (
            '<div class="review-no-source-rating" data-source-rating="none">'
            f"{escape(zero_copy.strip())}</div>"
        )

    if (
        isinstance(rating, bool)
        or not isinstance(rating, (int, float))
        or not 1 <= rating <= 5
    ):
        raise ValueError("source rating must be null or a number from 1 to 5")
    count_label = summary.get("count_label")
    if not isinstance(count_label, str) or not count_label.strip():
        raise ValueError("source count_label must be non-empty text")
    display_rating = str(rating)
    return (
        f'<div class="review-average" aria-label="{display_rating} out of 5 stars">'
        '<span aria-hidden="true">★★★★★</span>'
        f"<strong>{display_rating} out of 5</strong></div>"
        f"<div>{escape(display_count)} {escape(count_label.strip())}</div>"
    )


def render_reviews_section(
    asin: str,
    local_reviews: Iterable[Mapping[str, Any]] = (),
    *,
    star: int | None = None,
    sort: str = "recent",
    account_name: str | None = None,
    base_path: str | None = None,
    form_error: str | None = None,
    product_label: str | None = None,
    source_rating_summary: Mapping[str, Any] | None = None,
) -> str:
    """Render source aggregates and separately attributed local review rows."""

    view = build_review_view(
        asin,
        local_reviews,
        star=star,
        product_label=product_label,
        source_rating_summary=source_rating_summary,
    )
    if view is None:
        return ""
    if sort not in REVIEW_SORTS:
        raise ValueError("review sort must be recent or helpful")
    review_base_path = base_path or f"/dp/{view['asin']}"
    if (
        not review_base_path.startswith("/")
        or review_base_path.startswith("//")
        or "\\" in review_base_path
    ):
        raise ValueError("review base path must be a same-origin path")

    source_aggregate_available = bool(view["source_aggregate_available"])
    summary = view["source_rating_summary"]
    source_summary_html = (
        _source_rating_summary_html(summary)
        if source_aggregate_available and isinstance(summary, Mapping)
        else (
            '<div class="review-no-source-aggregate" '
            'data-source-rating="unavailable"><strong>Review summary unavailable</strong>'
            '<span>No source rating or review-count aggregate was captured for '
            'this product.</span></div>'
        )
    )
    histogram = view["source_histogram"]
    histogram_html = ""
    if histogram:
        histogram_rows = []
        for value in range(5, 0, -1):
            percent = histogram["percent_by_star"][str(value)]
            href = _review_href(
                view["asin"],
                star=value,
                sort=sort,
                base_path=review_base_path,
            )
            histogram_rows.append(
                '<a class="review-histogram-row" '
                f'href="{escape(href, quote=True)}" data-review-star-filter="{value}">'
                f"<span>{value} star</span>"
                '<progress class="review-histogram-track" max="100" '
                f'value="{percent}" aria-label="{percent} percent"></progress>'
                f"<span>{percent}%</span></a>"
            )
        histogram_html = (
            '<div class="review-histogram">' + "".join(histogram_rows) + "</div>"
        )

    topics_html = ""
    if view["source_topics"]:
        topics = "".join(
            '<li><span class="review-topic-label">'
            f'{escape(topic["label"])}</span> '
            f'<span class="review-topic-count">{topic["mentions"]:,}</span></li>'
            for topic in view["source_topics"]
        )
        topics_html = (
            '<div class="review-topics"><h3>Customers say</h3>'
            f'<ul aria-label="Common review topics">{topics}</ul></div>'
        )

    if not source_aggregate_available:
        source_boundary_copy = (
            "This product is known to the local catalog, but no source review "
            "aggregate was captured. Source ratings, review counts, authors, and "
            "excerpts are not inferred; local shopper reviews remain separate below."
        )
    elif summary["rating"] is None:
        source_boundary_copy = (
            "The source snapshot explicitly reports zero customer reviews and no "
            "source rating. No source review card or star rating is invented here."
        )
    elif view["source_topics"]:
        source_boundary_copy = (
            "The source snapshot includes aggregate rating and topic facts, but not "
            "enough fields for complete individual review cards. No source review "
            "card is invented here."
        )
    else:
        source_boundary_copy = (
            "The source snapshot includes aggregate rating/count evidence, but no "
            "complete individual review cards. No source review card is invented here."
        )
    source_boundary_html = (
        '<p class="review-source-boundary">'
        f"{escape(source_boundary_copy)}</p>"
    )

    filters: list[str] = []
    for value, label in ((None, "All stars"), (5, "5 star"), (4, "4 star"), (3, "3 star"), (2, "2 star"), (1, "1 star")):
        href = _review_href(
            view["asin"], star=value, sort=sort, base_path=review_base_path
        )
        active = ' aria-current="page"' if star == value else ""
        data_value = "all" if value is None else str(value)
        filters.append(
            f'<a href="{escape(href, quote=True)}" data-review-star-filter="{data_value}"{active}>{label}</a>'
        )

    sort_links = []
    for value, label in (("recent", "Most recent"), ("helpful", "Most helpful")):
        href = _review_href(
            view["asin"], star=star, sort=value, base_path=review_base_path
        )
        active = ' aria-current="page"' if sort == value else ""
        sort_links.append(
            f'<a href="{escape(href, quote=True)}" data-review-sort="{value}"{active}>{label}</a>'
        )

    local_cards = "".join(
        _local_review_card(view["asin"], review)
        for review in view["local_reviews"]
    )
    local_reviews_html = (
        local_cards
        if local_cards
        else '<p class="review-empty">No local customer reviews match this filter yet.</p>'
    )
    local_count = view["local_summary"]["review_count"]
    write_panel = _write_panel(view, account_name, form_error)
    source_provenance = (
        SOURCE_AGGREGATE_PROVENANCE
        if source_aggregate_available
        else "source_aggregate_unavailable"
    )
    source_provenance_label = (
        "Source snapshot aggregate"
        if source_aggregate_available
        else "Source review aggregate unavailable"
    )

    return f"""
    <section id="customerReviews" class="customer-reviews" data-asin="{escape(view['asin'], quote=True)}" data-source-aggregate="{'available' if source_aggregate_available else 'unavailable'}">
      <h2>Customer reviews</h2>
      <div class="review-summary-layout{' review-summary-layout-unavailable' if not source_aggregate_available else ''}">
        <div class="review-source-summary{' review-source-summary-unavailable' if not source_aggregate_available else ''}" data-provenance="{source_provenance}">
          <div class="review-provenance-label">{source_provenance_label}</div>
          {source_summary_html}
          {histogram_html}
        </div>
        {topics_html}
      </div>
      {source_boundary_html}
      {write_panel}
      <div class="review-local-list" data-provenance="local_user_review">
        <div class="review-local-heading"><div><span class="review-provenance-label">Local user reviews</span><h3>Reviews from local shoppers</h3></div><span>{local_count} reviews</span></div>
        <div class="review-toolbar">
          <nav class="review-filters" aria-label="Filter customer reviews">{''.join(filters)}</nav>
          <nav class="review-sorts" aria-label="Sort customer reviews">{''.join(sort_links)}</nav>
        </div>
        {local_reviews_html}
      </div>
    </section>
    """


__all__ = [
    "FIXTURE_PATH",
    "LOCAL_REVIEW_PROVENANCE",
    "SOURCE_AGGREGATE_PROVENANCE",
    "build_review_view",
    "get_review_evidence",
    "normalize_local_reviews",
    "render_reviews_section",
    "supported_review_asins",
]
