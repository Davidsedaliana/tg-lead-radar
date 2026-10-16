"""Cross-post detection.

One vacancy travels through many channels, each adding its own header,
hashtags and "подписывайтесь" footer. Two checks catch that: the hash of the
normalised text for verbatim copies, and similarity of 3-word shingles for
copies with a different wrapper — with a lower bar when the titles match.
"""

import hashlib
import re
from dataclasses import dataclass

from .config import DedupConfig
from .text import strip_links

_HASHTAG_RE = re.compile(r"#\w+")
_NON_WORD_RE = re.compile(r"[\W_]+")
SHINGLE_WORDS = 3


def normalize(text: str) -> str:
    """Words only: no links, handles, hashtags, emoji, punctuation or case."""
    text = _HASHTAG_RE.sub(" ", strip_links(text)).lower().replace("ё", "е")
    return " ".join(_NON_WORD_RE.sub(" ", text).split())


def text_hash(norm: str) -> str:
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def title_key(text: str, generic: frozenset[str]) -> str:
    """The first line that names something: "🔥 Новая вакансия!" does not."""
    for line in text.split("\n"):
        norm = normalize(line)
        if norm and norm not in generic:
            return norm[:80]
    return ""


def shingles(norm: str) -> frozenset[str]:
    words = norm.split()
    if len(words) <= SHINGLE_WORDS:
        return frozenset([" ".join(words)]) if words else frozenset()
    return frozenset(
        " ".join(words[i:i + SHINGLE_WORDS]) for i in range(len(words) - SHINGLE_WORDS + 1)
    )


@dataclass(frozen=True)
class Fingerprint:
    text_hash: str
    title_key: str
    shingles: frozenset[str]
    # Guards: copies of one vacancy never disagree on these.
    grade: tuple[str, ...] | None = None
    salary: tuple | None = None


def fingerprint(text: str, fields: dict, cfg: DedupConfig) -> Fingerprint:
    norm = normalize(text)
    salary = (fields.get("salary_from"), fields.get("salary_to"), fields.get("currency"))
    return Fingerprint(
        text_hash=text_hash(norm),
        title_key=title_key(text, cfg.generic_titles),
        shingles=shingles(norm),
        grade=tuple(fields["grade"]) if fields.get("grade") else None,
        salary=salary if salary[0] is not None or salary[1] is not None else None,
    )


def similarity(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def same_vacancy(a: Fingerprint, b: Fingerprint, cfg: DedupConfig) -> bool:
    if a.text_hash == b.text_hash:
        return True
    # "Middle QA" and "Senior QA" from one company share most of the text.
    if a.grade and b.grade and not set(a.grade) & set(b.grade):
        return False
    if a.salary and b.salary and a.salary != b.salary:
        return False
    same_title = bool(a.title_key) and a.title_key == b.title_key
    threshold = cfg.title_similarity if same_title else cfg.similarity
    return similarity(a.shingles, b.shingles) >= threshold
