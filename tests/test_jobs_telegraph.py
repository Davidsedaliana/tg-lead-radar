import httpx
import pytest

from tg_lead_radar.jobs.telegraph import article_path, fetch_article, nodes_to_text

# Shape of a real getPage answer (Ulbi TV publishes vacancies like this).
CONTENT = [
    {"tag": "p", "children": [{"tag": "strong", "children": ["Компания: "]}, "FinTech Lab"]},
    {"tag": "p", "children": ["ЗП: 250 000 – 320 000 ₽ на руки"]},
    {"tag": "h4", "children": ["Задачи:"]},
    {"tag": "ul", "children": [
        {"tag": "li", "children": ["автотесты на Python (pytest)"]},
        {"tag": "li", "children": ["CI в GitLab"]},
    ]},
    {"tag": "p", "children": [
        "Резюме: ", {"tag": "a", "attrs": {"href": "https://t.me/hr_anna"}, "children": ["Анна"]},
        {"tag": "br"}, "или hr@lab.io",
    ]},
]


class TestArticlePath:
    @pytest.mark.parametrize(
        ("url", "path"),
        [
            ("https://telegra.ph/Senior-QA-10-06", "Senior-QA-10-06"),
            ("https://telegra.ph/Senior-QA-10-06/", "Senior-QA-10-06"),
            ("https://graph.org/QA-Lead-10-07", "QA-Lead-10-07"),
            ("https://hh.ru/vacancy/1", None),
            ("https://telegra.ph/", None),
        ],
    )
    def test_paths(self, url, path):
        assert article_path(url) == path


def test_nodes_to_text():
    assert nodes_to_text(CONTENT) == (
        "Компания: FinTech Lab\n\n"
        "ЗП: 250 000 – 320 000 ₽ на руки\n\n"
        "Задачи:\n\n"
        "- автотесты на Python (pytest)\n"
        "- CI в GitLab\n\n"
        "Резюме: Анна (https://t.me/hr_anna)\n"
        "или hr@lab.io"
    )


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_fetch_article():
    def handler(request):
        assert request.url.path == "/getPage/Senior-QA-10-06"
        assert request.url.params["return_content"] == "true"
        return httpx.Response(200, json={
            "ok": True, "result": {"title": "Senior QA", "content": CONTENT},
        })

    async with _client(handler) as client:
        article = await fetch_article("https://telegra.ph/Senior-QA-10-06", client)
    assert article.title == "Senior QA"
    assert "ЗП: 250 000" in article.text


@pytest.mark.parametrize(
    "response",
    [httpx.Response(500), httpx.Response(200, json={"ok": False, "error": "PAGE_NOT_FOUND"})],
)
async def test_failures_give_none(response):
    async with _client(lambda request: response) as client:
        assert await fetch_article("https://telegra.ph/Gone-10-06", client) is None


async def test_not_an_article():
    assert await fetch_article("https://hh.ru/vacancy/1") is None
