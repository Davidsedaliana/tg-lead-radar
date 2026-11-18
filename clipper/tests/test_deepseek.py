"""DeepSeek judge against a local fake of its chat completions endpoint."""

import json

import pytest
from aiohttp import web

from twitch_clipper.judge import AnthropicJudge, DeepSeekJudge, Moment, make_judge

MOMENT = Moment(
    streamer="buster",
    game="Grand Theft Auto VI",
    chat_lines=["АХАХА", "что это было"],
    transcript="",
    frames=[b"\xff\xd8one\xff\xd9", b"\xff\xd8two\xff\xd9"],
    chat_mult=3.2,
    audio_mult=None,
)


def answer(content: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


class FakeDeepSeek:
    def __init__(self, reply) -> None:
        self.reply = reply  # callable(request body) -> (status, json body)
        self.requests: list[dict] = []
        self.auth: list[str] = []

    async def handle(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.requests.append(body)
        self.auth.append(request.headers.get("Authorization", ""))
        status, payload = self.reply(body)
        return web.json_response(payload, status=status)


@pytest.fixture
async def serve():
    runners = []

    async def start(reply) -> tuple[FakeDeepSeek, str]:
        fake = FakeDeepSeek(reply)
        app = web.Application()
        app.router.add_post("/chat/completions", fake.handle)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        runners.append(runner)
        port = site._server.sockets[0].getsockname()[1]
        return fake, f"http://127.0.0.1:{port}"

    yield start
    for runner in runners:
        await runner.cleanup()


def has_images(body: dict) -> bool:
    return any(part["type"] == "image_url" for part in body["messages"][1]["content"])


async def test_request_shape_and_verdict(serve):
    verdict_json = json.dumps({"score": 8, "category": "funny", "summary": "смешной провал"})
    fake, url = await serve(lambda body: (200, answer(verdict_json)))
    judge = DeepSeekJudge("sk-test", base_url=url)

    verdict = await judge.rate(MOMENT)

    assert (verdict.score, verdict.category, verdict.summary) == (8, "funny", "смешной провал")
    assert fake.auth == ["Bearer sk-test"]
    [body] = fake.requests
    assert body["model"] == "deepseek-flash"
    assert body["response_format"] == {"type": "json_object"}
    system, user = body["messages"]
    assert system["role"] == "system" and "JSON" in system["content"]
    images = [p for p in user["content"] if p["type"] == "image_url"]
    assert len(images) == 2
    assert images[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    text = user["content"][-1]["text"]
    assert "Игра: Grand Theft Auto VI" in text and "- АХАХА" in text


async def test_falls_back_to_text_when_images_are_rejected(serve):
    def reply(body):
        if has_images(body):
            return 400, {"error": {"message": "This model does not support image"}}
        return 200, answer('{"score": 6, "category": "other", "summary": "что-то"}')

    fake, url = await serve(reply)
    judge = DeepSeekJudge("k", model="text-only", base_url=url)

    assert (await judge.rate(MOMENT)).score == 6
    assert [has_images(r) for r in fake.requests] == [True, False]
    assert (await judge.rate(MOMENT)).score == 6
    assert [has_images(r) for r in fake.requests] == [True, False, False]  # remembered


@pytest.mark.parametrize(
    "reply",
    [
        lambda body: (401, {"error": {"message": "Authentication Fails"}}),
        lambda body: (402, {"error": {"message": "Insufficient Balance"}}),
        lambda body: (500, {"error": {"message": "oops"}}),
        lambda body: (200, answer("я не знаю")),
        lambda body: (200, {"unexpected": True}),
    ],
)
async def test_problems_give_no_verdict(serve, reply):
    _, url = await serve(reply)
    judge = DeepSeekJudge("k", base_url=url, send_images=False)
    assert await judge.rate(MOMENT) is None


async def test_unreachable_api_gives_no_verdict():
    judge = DeepSeekJudge("k", base_url="http://127.0.0.1:1")
    assert await judge.rate(MOMENT) is None


def test_make_judge():
    env = {"DEEPSEEK_API_KEY": "sk-x", "ANTHROPIC_API_KEY": "sk-ant-x"}
    judge = make_judge("deepseek", "", "https://api.deepseek.com", env)
    assert isinstance(judge, DeepSeekJudge) and judge.model == "deepseek-flash"
    assert make_judge("deepseek", "other-model", "https://x", env).model == "other-model"
    assert isinstance(make_judge("anthropic", "", "", env), AnthropicJudge)
    assert make_judge("", "", "", env) is None
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        make_judge("deepseek", "", "", {})
    with pytest.raises(ValueError, match="unknown"):
        make_judge("gpt", "", "", env)
