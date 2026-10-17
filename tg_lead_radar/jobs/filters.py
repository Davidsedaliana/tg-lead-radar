"""Recall-first gate for QA vacancies.

The radar does not judge vacancies — an external agent does. The gate only
has to keep everything that may be a QA vacancy and drop what certainly is
not: ads, courses, candidates' posts and one-off freelance orders.
"""

import re
from dataclasses import dataclass, field

from .config import FilterConfig
from .text import headline_zone, strip_links


@dataclass(frozen=True)
class JobMatch:
    stems: list[str] = field(default_factory=list)  # which [filter] stems hit
    rejected_by: str | None = None  # "anti: …" / "anti_title: …" / "only in body: …"

    @property
    def matched(self) -> bool:
        return bool(self.stems) and self.rejected_by is None


def match_job(text: str, cfg: FilterConfig) -> JobMatch:
    if not text:
        return JobMatch()
    # Links and handles are not words of the post: "@qa_jobs" under every
    # post of a general IT channel would otherwise match all of them.
    scan = strip_links(text)
    stems = cfg.stems.hits(scan)
    if not stems:
        return JobMatch()
    anti = cfg.anti.hits(scan)
    if anti:
        return JobMatch(stems, f"anti: {anti[0]}")
    anti_title = cfg.anti_title.hits(headline_zone(scan))
    if anti_title:
        return JobMatch(stems, f"anti_title: {anti_title[0]}")
    if not cfg.stems.any(title_area(scan)) and len(stems) < cfg.body_min_stems:
        # "взаимодействовать с тестировщиками" in an analyst vacancy
        return JobMatch(stems, f"only in body: {', '.join(stems)}")
    return JobMatch(stems)


def title_area(text: str) -> str:
    """The first three lines with words in them, plus every hashtag of the post."""
    lines = [ln for ln in text.split("\n") if re.search(r"[^\W\d_]", ln)]
    return "\n".join(lines[:3] + re.findall(r"#\w+", text))
