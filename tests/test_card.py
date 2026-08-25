from tg_lead_radar.card import (
    CATEGORY_EMOJI,
    LeadCard,
    excerpt,
    message_link,
    render_html,
    render_plain,
)
from tg_lead_radar.templates import DRAFTS, PRIORITY, pick_draft


def card(**overrides) -> LeadCard:
    defaults = dict(
        channel_title="Remote IT",
        link="https://t.me/Remoteit/42",
        text="Нужен бот для заявок",
        categories=["bot"],
        draft="Здравствуйте!",
    )
    defaults.update(overrides)
    return LeadCard(**defaults)


class TestMessageLink:
    def test_public_channel(self):
        assert message_link("Remoteit", -1001234, 42) == "https://t.me/Remoteit/42"

    def test_private_chat_uses_internal_id(self):
        assert message_link(None, -1001234567890, 7) == "https://t.me/c/1234567890/7"


class TestExcerpt:
    def test_short_text_unchanged(self):
        assert excerpt("Нужен бот") == "Нужен бот"

    def test_long_text_truncated_with_ellipsis(self):
        cut = excerpt("x" * 1000)
        assert len(cut) <= 600
        assert cut.endswith("…")


class TestPickDraft:
    def test_priority_fix_over_bot(self):
        assert pick_draft(["bot", "fix"]) == DRAFTS["fix"]

    def test_all_priorities_have_drafts(self):
        assert set(PRIORITY) == set(DRAFTS)

    def test_all_priorities_have_emoji(self):
        assert set(PRIORITY) == set(CATEGORY_EMOJI)


class TestRenderPlain:
    def test_contains_all_parts(self):
        text = render_plain(card())
        assert "Remote IT" in text
        assert "#bot" in text
        assert "🤖" in text
        assert "https://t.me/Remoteit/42" in text
        assert "черновик отклика" in text
        assert "Здравствуйте!" in text


class TestRenderHtml:
    def test_structure(self):
        html_text = render_html(card())
        assert '<a href="https://t.me/Remoteit/42">Remote IT</a>' in html_text
        assert html_text.count("<blockquote expandable>") == 2
        assert "Черновик отклика" in html_text

    def test_escapes_user_content(self):
        html_text = render_html(card(text="если a<b то <script>"))
        assert "<script>" not in html_text
        assert "&lt;script&gt;" in html_text

    def test_emoji_falls_back_for_unknown_category(self):
        assert card(categories=["unknown"]).emoji == "🎯"
