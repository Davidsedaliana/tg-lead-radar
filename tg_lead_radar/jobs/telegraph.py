"""Vacancies published as telegra.ph articles.

Some channels post only a title and a link to an article with the whole
vacancy. The article is fetched through the public Telegraph API, so the
fields and contacts come from the full text, not from the title alone.
"""

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

API = "https://api.telegra.ph/getPage/{path}?return_content=true"
_BLOCK_TAGS = {"p", "h3", "h4", "blockquote", "aside", "figure", "pre", "hr", "ul", "ol"}


@dataclass(frozen=True)
class Article:
    url: str
    title: str
    text: str


def article_path(url: str) -> str | None:
    """"Senior-QA-10-06" for https://telegra.ph/Senior-QA-10-06, else None."""
    parts = urlsplit(url)
    if (parts.hostname or "").lower() not in {"telegra.ph", "te.legra.ph", "graph.org"}:
        return None
    path = parts.path.strip("/")
    return path if path and "/" not in path else None


def _render(node, out: list[str]) -> None:
    if isinstance(node, str):
        out.append(node)
        return
    tag = node.get("tag", "")
    children = node.get("children") or []
    if tag == "br":
        out.append("\n")
        return
    if tag == "li":
        out.append("\n- ")
    elif tag in _BLOCK_TAGS:
        out.append("\n")
    start = len(out)
    for child in children:
        _render(child, out)
    if tag == "a":
        href = (node.get("attrs") or {}).get("href", "")
        label = "".join(out[start:]).strip()
        if href and href.rstrip("/") not in label:
            out.append(f" ({href})")
    if tag in _BLOCK_TAGS:
        out.append("\n")


def nodes_to_text(nodes: list) -> str:
    """Telegraph content nodes as plain text: paragraphs on their own lines,
    list items with "- ", links spelled out as "text (url)"."""
    out: list[str] = []
    for node in nodes:
        _render(node, out)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in "".join(out).split("\n")]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


async def fetch_article(
    url: str, client: httpx.AsyncClient | None = None, timeout: float = 20
) -> Article | None:
    """None when the link is not an article or Telegraph does not answer."""
    path = article_path(url)
    if path is None:
        return None
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=timeout) as own:
                resp = await own.get(API.format(path=path))
        else:
            resp = await client.get(API.format(path=path))
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.info("Telegraph article %s not fetched: %s", url, exc)
        return None
    if not data.get("ok"):
        return None
    result = data["result"]
    return Article(url=url, title=result.get("title", ""), text=nodes_to_text(result["content"]))
