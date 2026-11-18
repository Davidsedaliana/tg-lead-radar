import json

import pytest

from twitch_clipper.bot import streamers_view, vote_keyboard
from twitch_clipper.chat import parse_privmsg
from twitch_clipper.config import Settings, normalize_login
from twitch_clipper.judge import build_content, parse_verdict
from twitch_clipper.store import Store
from twitch_clipper.text import build_caption, guess_category, sample_chat


def test_parse_privmsg():
    msg = parse_privmsg(":vasya!vasya@vasya.tmi.twitch.tv PRIVMSG #Buster :АХАХА что это", ts=5)
    assert msg is not None
    assert (msg.channel, msg.user, msg.text, msg.ts) == ("buster", "vasya", "АХАХА что это", 5)


def test_parse_privmsg_keeps_colons_and_ignores_other_lines():
    msg = parse_privmsg(":a!a@a.tmi.twitch.tv PRIVMSG #x :время 14:35 :)")
    assert msg is not None and msg.text == "время 14:35 :)"
    assert parse_privmsg("PING :tmi.twitch.tv") is None
    assert parse_privmsg(":tmi.twitch.tv 001 justinfan1 :Welcome, GLHF!") is None
    assert parse_privmsg(":a!a@a.tmi.twitch.tv JOIN #x") is None


def test_parse_privmsg_with_tags():
    line = "@badges=;color=#FF0000 :a!a@a.tmi.twitch.tv PRIVMSG #x :привет"
    msg = parse_privmsg(line)
    assert msg is not None and msg.text == "привет"


def test_normalize_login():
    assert normalize_login(" https://www.twitch.tv/Buster/ ") == "buster"
    assert normalize_login("@Evelone192") == "evelone192"


def test_settings_from_env():
    cfg = Settings.from_env(
        {
            "BOT_TOKEN": "x",
            "CHAT_ID": "-100123",
            "STREAMERS": "Buster, twitch.tv/evelone192,",
            "GAMES": "Grand Theft Auto VI, Grand Theft Auto V ",
            "CHAT_MULT": "2.5",
            "VERTICAL": "0",
            "MIN_SCORE": "",
            "ANTHROPIC_API_KEY": "k",
        }
    )
    assert cfg.chat_id == -100123
    assert cfg.streamers == ("buster", "evelone192")
    assert cfg.games == ("Grand Theft Auto VI", "Grand Theft Auto V")
    assert cfg.chat_mult == 2.5
    assert cfg.vertical is False
    assert cfg.min_score == 6  # empty value keeps the default
    assert cfg.judge_enabled is True
    assert Settings.from_env({}).judge_enabled is False


def test_sample_chat_prefers_repeated_messages():
    texts = ["привет", "АХАХА", "ахаха", "F", "что", "ахаха", "F", "один", "два", "три"]
    assert sample_chat(texts, limit=3) == ["привет", "АХАХА", "F"]


def test_sample_chat_cuts_long_messages():
    [line] = sample_chat(["SUBprise " * 40])
    assert len(line) <= 90 and line.endswith("…")


def test_guess_category():
    assert guess_category(["АХАХАХА", "пхахах", "лол", "вау"]) == "funny"
    assert guess_category(["FFFFF", "F", "рип"]) == "fail"
    assert guess_category(["200", "Джаа5"]) == "other"


def test_build_caption_matches_the_expected_layout():
    caption = build_caption(
        "buster", 1795001700, "Europe/Moscow", "funny", 3.04, 2.81, 8,
        "Яркие эмоции, забавный провал", ["дресня", "FFFFF"],
    )  # fmt: skip
    lines = caption.splitlines()
    assert lines[0].startswith("🎬 buster · ")
    assert lines[0].endswith("· 😂 смешно · чат x3.0 · 🔊 x2.8")
    assert lines[1] == "🤖 8/10: Яркие эмоции, забавный провал"
    assert lines[2:] == ["💬 дресня", "💬 FFFFF"]


def test_build_caption_without_score_and_within_limit():
    caption = build_caption("x", 0, "UTC", "other", 5.0, None, None, "", ["я" * 90] * 30)
    assert "🤖" not in caption and "🔊" not in caption
    assert len(caption) <= 1024


def test_store_streamers_and_votes():
    store = Store(":memory:")
    store.seed(("buster", "evelone192"))
    store.seed(("ignored",))  # the table is not empty any more
    assert store.streamers() == [("buster", True), ("evelone192", True)]
    assert store.toggle_streamer("buster") is False
    assert store.enabled_streamers() == {"evelone192"}
    assert store.toggle_streamer("nobody") is None
    assert store.add_streamer("buster") is False
    assert store.remove_streamer("buster") is True

    clip = store.add_clip("evelone192", 3.0, 2.8, 8, "funny", "смешно")
    assert store.vote(clip, 1, 1) == (1, 0)
    assert store.vote(clip, 2, -1) == (1, 1)
    assert store.vote(clip, 1, -1) == (0, 2)  # changed their mind
    assert store.vote(clip, 1, -1) == (0, 1)  # the same button again removes the vote


def test_streamers_view_and_keyboards():
    statuses = {"steel": ("recording", "Grand Theft Auto VI"), "buster": ("other_game", "Chess")}
    text, keyboard = streamers_view([("buster", False), ("steel", True)], statuses)
    assert "🟡 buster · Chess\n" in text and "🔴 steel · Grand Theft Auto VI ✂️" in text
    assert keyboard is not None
    buttons = [row[0] for row in keyboard.inline_keyboard]
    assert [b.text for b in buttons] == ["🎬 Резать buster", "⏹ Не резать steel"]
    assert [b.callback_data for b in buttons] == ["t:buster", "t:steel"]
    assert streamers_view([], {})[1] is None

    row = vote_keyboard(7, likes=2).inline_keyboard[0]
    assert [b.text for b in row] == ["👍 2", "👎"]
    assert [b.callback_data for b in row] == ["v:7:1", "v:7:-1"]


def test_judge_content_and_verdict():
    content = build_content(
        "buster", "Grand Theft Auto VI", ["АХАХА"], "ну всё", [b"\xff\xd8jpeg"], 3.0, None
    )
    assert "Игра: Grand Theft Auto VI" in content[-1]["text"]
    assert [block["type"] for block in content] == ["image", "text"]
    assert "<chat>\n- АХАХА\n</chat>" in content[-1]["text"]
    assert "<transcript>\nну всё\n</transcript>" in content[-1]["text"]
    assert "Громкость" not in content[-1]["text"]

    verdict = parse_verdict(json.dumps({"score": 14, "category": "nope", "summary": " a  b "}))
    assert (verdict.score, verdict.category, verdict.summary) == (10, "other", "a b")
    with pytest.raises(ValueError):
        parse_verdict("not json")
