from tg_lead_radar.card import (
    CATEGORY_EMOJI,
    LeadCard,
    excerpt,
    fit_stars,
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
    def test_dominant_category_wins(self):
        # the list arrives dominance-ordered from filters.match
        assert pick_draft(["automation", "bot"]) == DRAFTS["automation"]

    def test_unknown_category_falls_through(self):
        assert pick_draft(["unknown", "fix"]) == DRAFTS["fix"]

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


class TestMeta:
    def test_published_formats_to_msk(self):
        c = card(published="2026-08-24T14:20:00+00:00")
        assert "24.08 17:20 мск" in c.meta_line

    def test_bad_published_is_ignored(self):
        assert card(published="not-a-date").meta_line == ""

    def test_fit_shown(self):
        c = card(fit="⭐⭐⭐")
        assert "⭐⭐⭐ под профиль" in c.meta_line

    def test_budget_extracted(self):
        c = card(text="Нужен бот\nВилка: 8 000 – 15 000 ₽ за проект\nУдалённо")
        assert c.budget == "Вилка: 8 000 – 15 000 ₽ за проект"
        assert "💰" in render_html(c)

    def test_budget_trimmed_to_money_part_in_order_feeds(self):
        c = card(text="📁 Скрипты, боты и mini apps | 💰 5 000 ₽ | ⏰ 10 дн.\nСоздать бота")
        assert c.budget == "5 000 ₽"

    def test_channel_footer_dropped_from_excerpt(self):
        c = card(
            text="Нужен бот для заявок\n━━━━━━━━━━━━\n💰 Привёл клиента — тебе 20% → @promo"
        )
        rendered = render_plain(c)
        assert "Нужен бот для заявок" in rendered
        assert "@promo" not in rendered

    def test_no_budget_line_omitted(self):
        c = card(text="Нужен бот, детали в лс")
        assert c.budget is None
        assert "💰" not in render_html(c)


class TestFitStars:
    def test_core_category_is_three_stars(self):
        assert fit_stars(["bot"], {"bot": 1}) == "⭐⭐⭐"

    def test_strong_automation_is_three_stars(self):
        assert fit_stars(["automation"], {"automation": 3}) == "⭐⭐⭐"

    def test_single_automation_hit_is_two_stars(self):
        assert fit_stars(["automation"], {"automation": 1}) == "⭐⭐"

    def test_weak_only_is_one_star(self):
        assert fit_stars(["python"], {"python": 1}) == "⭐"


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
