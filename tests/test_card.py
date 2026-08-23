from tg_lead_radar.card import excerpt, format_card, message_link
from tg_lead_radar.templates import DRAFTS, PRIORITY, pick_draft


class TestMessageLink:
    def test_public_channel(self):
        assert message_link("Remoteit", -1001234, 42) == "https://t.me/Remoteit/42"

    def test_private_chat_uses_internal_id(self):
        assert message_link(None, -1001234567890, 7) == "https://t.me/c/1234567890/7"


class TestExcerpt:
    def test_short_text_unchanged(self):
        assert excerpt("Нужен бот") == "Нужен бот"

    def test_long_text_truncated_with_ellipsis(self):
        text = "x" * 1000
        cut = excerpt(text)
        assert len(cut) <= 600
        assert cut.endswith("…")


class TestPickDraft:
    def test_priority_fix_over_bot(self):
        assert pick_draft(["bot", "fix"]) == DRAFTS["fix"]

    def test_single_category(self):
        assert pick_draft(["automation"]) == DRAFTS["automation"]

    def test_all_priorities_have_drafts(self):
        assert set(PRIORITY) == set(DRAFTS)


class TestFormatCard:
    def test_contains_all_parts(self):
        card = format_card(
            channel_title="Remote IT",
            link="https://t.me/Remoteit/42",
            text="Нужен бот для заявок",
            categories=["bot"],
            draft="Здравствуйте!",
        )
        assert "Remote IT" in card
        assert "#bot" in card
        assert "https://t.me/Remoteit/42" in card
        assert "Нужен бот для заявок" in card
        assert "черновик отклика" in card
        assert "Здравствуйте!" in card
