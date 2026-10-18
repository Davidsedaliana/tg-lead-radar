"""Jobs mode settings from jobs.toml: lists and thresholds you tune by hand,
kept out of the code so editing them needs no Python."""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def term_pattern(term: str, stem: bool = False) -> re.Pattern[str]:
    """Compile one dictionary entry.

    Every word matches at word start. With stem=True each word is a stem (the
    [filter] lists); otherwise a word is whole unless it ends with "*". Words
    of a phrase allow any whitespace between them. The guards only apply on a
    word-character edge, so "#резюме" and "💰" match right before a digit.
    """
    parts = []
    for word in term.split():
        is_stem = stem or word.endswith("*")
        word = word.rstrip("*")
        part = re.escape(word)
        if is_stem:
            part += r"\w*"
        elif re.match(r"\w", word[-1]):
            part += r"(?!\w)"
        parts.append(part)
    lead = r"(?<!\w)" if re.match(r"\w", term) else ""
    return re.compile(lead + r"\s+".join(parts), re.IGNORECASE)


@dataclass(frozen=True)
class Terms:
    """A list of entries; reports which of them hit."""

    items: tuple[tuple[str, re.Pattern[str]], ...]

    @classmethod
    def of(cls, terms: list[str], stem: bool = False) -> "Terms":
        return cls(tuple((t, term_pattern(t, stem)) for t in terms))

    def hits(self, text: str) -> list[str]:
        return [term for term, pattern in self.items if pattern.search(text)]

    def any(self, text: str) -> bool:
        return any(pattern.search(text) for _, pattern in self.items)


@dataclass(frozen=True)
class Vocabulary:
    """Canonical name -> aliases. Reports canonical names found, in config order."""

    entries: tuple[tuple[str, Terms], ...]

    @classmethod
    def of(cls, table: dict[str, list[str]]) -> "Vocabulary":
        return cls(tuple((name, Terms.of(aliases)) for name, aliases in table.items()))

    def found(self, text: str) -> list[str]:
        return [name for name, terms in self.entries if terms.any(text)]


@dataclass(frozen=True)
class FilterConfig:
    stems: Terms
    anti: Terms
    anti_title: Terms
    # Different stems the body needs when the title area has none.
    body_min_stems: int


@dataclass(frozen=True)
class ExtractConfig:
    default_currency: str | None
    salary_markers: Terms
    salary_skip: Terms
    remote: Terms
    hybrid: Terms
    office: Terms
    city_skip: Terms
    grade_line_markers: Terms
    contact_skip: Terms
    contact_skip_handles: tuple[str, ...]
    contact_cues: Terms
    apply_domains: tuple[str, ...]
    apply_path_markers: tuple[str, ...]
    grades: Vocabulary
    cities: Vocabulary
    stack: Vocabulary


@dataclass(frozen=True)
class DedupConfig:
    window_days: int
    similarity: float
    title_similarity: float
    generic_titles: frozenset[str]


@dataclass(frozen=True)
class CollectorConfig:
    linked_post_max_chars: int
    backfill_max_posts: int
    folder_refresh_minutes: int


@dataclass(frozen=True)
class Thresholds:
    alive_max_days: float
    alive_min_posts_week: int
    alive_min_vacancy_share: float
    dead_min_days: float


@dataclass(frozen=True)
class ScoreWeights:
    freshness: float
    activity: float
    vacancies: float
    engagement: float
    activity_target_posts_week: float
    vacancy_target_share: float
    engagement_target: float
    junk_penalty: float


@dataclass(frozen=True)
class DiscoverConfig:
    queries: tuple[str, ...]
    search_limit: int
    max_candidates: int
    posts_per_channel: int
    pause_seconds: float
    max_flood_wait: int
    include_groups: bool
    ad_markers: Terms
    vacancy_markers: Terms
    thresholds: Thresholds
    score: ScoreWeights


@dataclass(frozen=True)
class JobsConfig:
    filter: FilterConfig
    extract: ExtractConfig
    dedup: DedupConfig
    collector: CollectorConfig
    discover: DiscoverConfig


def resolve_path(path: str) -> Path:
    """Relative paths work from the current directory or the project root,
    so scripts behave the same whatever directory they are started from."""
    candidate = Path(path)
    if candidate.is_absolute() or candidate.exists():
        return candidate
    return PROJECT_ROOT / candidate


def load_jobs_config(path: str = "jobs.toml") -> JobsConfig:
    resolved = resolve_path(path)
    with open(resolved, "rb") as fh:
        raw = tomllib.load(fh)
    try:
        return _parse(raw)
    except KeyError as exc:
        raise SystemExit(f"{resolved}: нет ключа {exc}") from exc


def _parse(raw: dict) -> JobsConfig:
    f, e, d, c, s = (raw[k] for k in ("filter", "extract", "dedup", "collector", "discover"))
    return JobsConfig(
        filter=FilterConfig(
            stems=Terms.of(f["stems"], stem=True),
            anti=Terms.of(f["anti"], stem=True),
            anti_title=Terms.of(f["anti_title"], stem=True),
            body_min_stems=f["body_min_stems"],
        ),
        extract=ExtractConfig(
            default_currency=e["default_currency"].strip().upper() or None,
            salary_markers=Terms.of(e["salary_markers"]),
            salary_skip=Terms.of(e["salary_skip"]),
            remote=Terms.of(e["remote"]),
            hybrid=Terms.of(e["hybrid"]),
            office=Terms.of(e["office"]),
            city_skip=Terms.of(e["city_skip"]),
            grade_line_markers=Terms.of(e["grade_line_markers"]),
            contact_skip=Terms.of(e["contact_skip"]),
            contact_skip_handles=tuple(x.lower() for x in e["contact_skip_handles"]),
            contact_cues=Terms.of(e["contact_cues"]),
            apply_domains=tuple(x.lower() for x in e["apply_domains"]),
            apply_path_markers=tuple(x.lower() for x in e["apply_path_markers"]),
            grades=Vocabulary.of(e["grades"]),
            cities=Vocabulary.of(e["cities"]),
            stack=Vocabulary.of(e["stack"]),
        ),
        dedup=DedupConfig(
            window_days=d["window_days"],
            similarity=d["similarity"],
            title_similarity=d["title_similarity"],
            generic_titles=frozenset(t.lower() for t in d["generic_titles"]),
        ),
        collector=CollectorConfig(**c),
        discover=DiscoverConfig(
            queries=tuple(s["queries"]),
            search_limit=s["search_limit"],
            max_candidates=s["max_candidates"],
            posts_per_channel=s["posts_per_channel"],
            pause_seconds=s["pause_seconds"],
            max_flood_wait=s["max_flood_wait"],
            include_groups=s["include_groups"],
            ad_markers=Terms.of(s["ad_markers"]),
            vacancy_markers=Terms.of(s["vacancy_markers"]),
            thresholds=Thresholds(**s["thresholds"]),
            score=ScoreWeights(**s["score"]),
        ),
    )
