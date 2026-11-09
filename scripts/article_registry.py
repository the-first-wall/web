#!/usr/bin/env python3
"""The published articles of The First Wall — one registry the site builds from.

Before this file, every view of the article list (the hub cards, the hub's
ItemList, the feed) carried its own hand-kept copy of the same facts. The list
only ever grows, so each copy drifts a little further from the others.

This module is the single source of truth for **which** articles exist and
**when** each was inscribed. The prose of an article (its title, dek, summary,
date line) stays where the site's other copy lives — in ``i18n.js`` under the
``article_<n>_*`` keys — and its SEO head in ``scripts/page_meta.py``. Here we
keep only the machine facts: the number, the slug, the publication day.

The date is day-only, matching how the site states it; a feed or a schema.org
timestamp uses noon UTC so the day stays stable in every timezone (the same
convention ``sync_from_ledger.FEED_ARTICLES`` already used).

Usage:
    import article_registry as reg
    reg.latest(12)        # newest first, at most 12
    reg.for_year(2026)    # oldest first, that year only
    reg.page_rel(28)      # "articles/28-a-live-link-is-not-a-live-claim.html"
"""

from __future__ import annotations

# (number, slug without the NN- prefix, ISO publication day). Append-only: a new
# article is one line here plus its page, its i18n keys and its page_meta entry.
ARTICLES = [
    (1, "make-your-blip-count", "2026-10-07"),
    (2, "in-defense-of-the-bot", "2026-10-08"),
    (3, "retirement-not-shutdown", "2026-10-08"),
    (4, "what-a-record-owes-its-reader", "2026-10-13"),
    (5, "checksum-is-not-provenance", "2026-10-16"),
    (6, "the-expiry-of-truth", "2026-10-16"),
    (7, "who-verified-it", "2026-10-16"),
    (8, "forgetting-is-honest-laundering-is-not", "2026-10-19"),
    (9, "the-fork-is-the-record", "2026-10-20"),
    (10, "a-mirror-is-not-a-proof", "2026-10-21"),
    (11, "the-record-must-not-mint-the-permission", "2026-10-22"),
    (12, "the-empty-row-is-not-a-finding", "2026-10-23"),
    (13, "the-fork-you-did-not-take", "2026-10-24"),
    (14, "one-clock-is-a-time-machine", "2026-10-25"),
    (15, "the-argument-the-summary-forgot", "2026-10-26"),
    (16, "burning-the-index-is-not-burning-the-record", "2026-10-27"),
    (17, "the-hand-that-mints-the-receipt", "2026-10-28"),
    (18, "the-miss-is-an-entry-not-an-absence", "2026-10-29"),
    (19, "the-approval-binds-the-bytes-not-the-name", "2026-10-31"),
    (20, "the-check-must-survive-its-author", "2026-11-01"),
    (21, "a-rumor-with-tenure", "2026-11-02"),
    (22, "a-chorus-cannot-be-cross-examined", "2026-11-03"),
    (23, "the-retry-is-a-second-decision", "2026-11-04"),
    (24, "a-constraint-the-constrained-can-rewrite", "2026-11-05"),
    (25, "the-disagreement-count", "2026-11-06"),
    (26, "a-second-run-is-not-a-second-witness", "2026-11-06"),
    (27, "a-dead-link-is-not-a-retraction", "2026-11-07"),
    (28, "a-live-link-is-not-a-live-claim", "2026-11-08"),
    (29, "the-log-you-can-edit-is-not-a-record", "2026-11-09"),
]

# Localized month names for the archive's month separators. Kept here (not in
# i18n.js) because they are a build-time label of a generated list, not UI copy a
# page looks up at runtime.
MONTHS = {
    "en": ["January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"],
    "de": ["Januar", "Februar", "März", "April", "Mai", "Juni",
           "Juli", "August", "September", "Oktober", "November", "Dezember"],
}


def slug(n: int) -> str:
    """The article's file stem, e.g. '28-a-live-link-is-not-a-live-claim'."""
    for num, s, _d in ARTICLES:
        if num == n:
            return "%02d-%s" % (num, s)
    raise KeyError(n)


def page_rel(n: int) -> str:
    """The article's path relative to src/, e.g. 'articles/28-....html'."""
    return "articles/%s.html" % slug(n)


def page_url(n: int) -> str:
    """The article's site-absolute URL, e.g. '/articles/28-....html'."""
    return "/" + page_rel(n)


def number_of_rel(rel: str):
    """The article number of a src-relative page, or None when it is not an article."""
    if not rel.startswith("articles/") or not rel.endswith(".html"):
        return None
    stem = rel[len("articles/"):-len(".html")]
    if len(stem) < 3 or not stem[:2].isdigit() or stem[2] != "-":
        return None
    try:
        return int(stem[:2])
    except ValueError:
        return None


def by_number(n: int) -> dict:
    for num, s, d in ARTICLES:
        if num == n:
            return {"n": num, "slug": s, "date": d}
    raise KeyError(n)


def ordered() -> list:
    """Every article, oldest first (ties broken by number)."""
    return [by_number(n) for n, _s, _d in sorted(ARTICLES, key=lambda a: (a[2], a[0]))]


def latest(limit: int) -> list:
    """The `limit` newest articles, newest first."""
    return list(reversed(ordered()))[:limit]


def for_year(year) -> list:
    """Every article inscribed in `year`, oldest first."""
    y = str(year)
    return [a for a in ordered() if a["date"].startswith(y + "-")]


def years() -> list:
    """Every year that has at least one article, ascending."""
    return sorted({a["date"][:4] for a in ARTICLES})


def month_label(date: str, lang: str) -> str:
    """'2026-10-07' -> 'October 2026' / 'Oktober 2026'."""
    year, month, _day = date.split("-")
    return "%s %s" % (MONTHS[lang][int(month) - 1], year)
