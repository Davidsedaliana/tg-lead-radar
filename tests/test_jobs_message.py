from datetime import UTC, datetime
from types import SimpleNamespace

from telethon.tl import types

from tg_lead_radar.jobs.message import (
    Origin,
    PostRef,
    attachments_of,
    build_raw_post,
    button_links,
    expand_text,
    forward_origin,
)

WHEN = datetime(2026, 10, 15, 11, 0, tzinfo=UTC)
CHAT = SimpleNamespace(id=1234567890, username="qa_jobs", title="QA Jobs")


def message(text="", entities=None, msg_id=10, **kwargs) -> types.Message:
    return types.Message(
        id=msg_id,
        peer_id=types.PeerChannel(CHAT.id),
        date=WHEN,
        message=text,
        entities=entities,
        post=True,
        **kwargs,
    )


def document(name="JD.pdf", mime="application/pdf", extra=()) -> types.MessageMediaDocument:
    attrs = [types.DocumentAttributeFilename(name), *extra] if name else list(extra)
    return types.MessageMediaDocument(
        document=types.Document(
            id=1, access_hash=1, file_reference=b"", date=WHEN, mime_type=mime,
            size=2048, dc_id=2, attributes=attrs,
        )
    )


class TestExpandText:
    def test_hidden_link_is_spelled_out(self):
        text = "Откликнуться здесь"
        entity = types.MessageEntityTextUrl(offset=13, length=5, url="https://hh.ru/vacancy/1")
        expanded, urls = expand_text(text, [entity])
        assert expanded == "Откликнуться здесь (https://hh.ru/vacancy/1)"
        assert urls == ["https://hh.ru/vacancy/1"]

    def test_emoji_before_link_does_not_shift_offsets(self):
        # "🔥" is two UTF-16 code units: offsets are counted in those
        text = "🔥 Откликнуться здесь"
        entity = types.MessageEntityTextUrl(offset=16, length=5, url="https://hh.ru/vacancy/1")
        expanded, _ = expand_text(text, [entity])
        assert expanded == "🔥 Откликнуться здесь (https://hh.ru/vacancy/1)"

    def test_several_links_and_line_breaks_kept(self):
        text = "QA Engineer\nАнкета\nhh"
        entities = [
            types.MessageEntityTextUrl(offset=12, length=6, url="https://forms.gle/a"),
            types.MessageEntityTextUrl(offset=19, length=2, url="https://hh.ru/vacancy/2"),
        ]
        expanded, urls = expand_text(text, entities)
        assert expanded == (
            "QA Engineer\nАнкета (https://forms.gle/a)\nhh (https://hh.ru/vacancy/2)"
        )
        assert urls == ["https://forms.gle/a", "https://hh.ru/vacancy/2"]

    def test_link_over_a_line_break_keeps_the_url_on_its_line(self):
        text = "Анкета\nУсловия"
        entity = types.MessageEntityTextUrl(offset=0, length=7, url="https://forms.gle/a")
        assert expand_text(text, [entity])[0] == "Анкета (https://forms.gle/a)\nУсловия"

    def test_label_that_is_the_url_is_not_repeated(self):
        text = "Вакансия: hh.ru/vacancy/3"
        entity = types.MessageEntityTextUrl(
            offset=10, length=15, url="https://hh.ru/vacancy/3?from=tg"
        )
        expanded, urls = expand_text(text, [entity])
        assert expanded == text
        assert urls == ["https://hh.ru/vacancy/3?from=tg"]

    def test_hidden_user_mention(self):
        text = "Пишите Анне"
        expanded, urls = expand_text(text, [types.MessageEntityMentionName(7, 4, 42)])
        assert expanded == "Пишите Анне (tg://user?id=42)"
        assert urls == ["tg://user?id=42"]

    def test_visible_url_entity_gets_scheme(self):
        text = "Анкета: forms.gle/xyz"
        _, urls = expand_text(text, [types.MessageEntityUrl(8, 13)])
        assert urls == ["https://forms.gle/xyz"]

    def test_formatting_entities_change_nothing(self):
        text = "QA Engineer"
        assert expand_text(text, [types.MessageEntityBold(0, 2)]) == (text, [])


class TestButtons:
    def test_url_and_profile_buttons(self):
        markup = types.ReplyInlineMarkup(rows=[
            types.KeyboardInlineButtonRow(buttons=[
                types.KeyboardInlineButton(
                    text="Откликнуться", type=types.InlineButtonTypeUrl("https://hh.ru/v/1")
                ),
                types.KeyboardInlineButton(
                    text="HR", type=types.InlineButtonTypeUserProfile(user_id=7)
                ),
                types.KeyboardInlineButton(
                    text="👍", type=types.InlineButtonTypeCallback(data=b"x")
                ),
            ])
        ])
        assert button_links(markup) == [
            ("Откликнуться", "https://hh.ru/v/1"),
            ("HR", "tg://user?id=7"),
        ]

    def test_old_layer_button_shape(self):
        old = SimpleNamespace(rows=[
            SimpleNamespace(buttons=[SimpleNamespace(text="Apply", url="https://x.io/jobs")])
        ])
        assert button_links(old) == [("Apply", "https://x.io/jobs")]

    def test_no_markup(self):
        assert button_links(None) == []


class TestAttachments:
    def test_document_with_name(self):
        [a] = attachments_of(document())
        assert (a.type, a.file_name, a.mime_type, a.size) == (
            "document", "JD.pdf", "application/pdf", 2048,
        )

    def test_photo(self):
        media = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=1))
        assert [a.type for a in attachments_of(media)] == ["photo"]

    def test_video(self):
        video = types.DocumentAttributeVideo(duration=3, w=1, h=1)
        [a] = attachments_of(document("intro.mp4", "video/mp4", [video]))
        assert (a.type, a.file_name) == ("video", "intro.mp4")

    def test_link_preview_is_not_an_attachment(self):
        assert attachments_of(types.MessageMediaWebPage(webpage=types.WebPageEmpty(id=1))) == []


class TestForward:
    def test_forward_from_channel(self):
        fwd = types.MessageFwdHeader(
            date=WHEN, from_id=types.PeerChannel(555), channel_post=77
        )
        msg = message("QA Engineer", fwd_from=fwd)
        msg._forward = SimpleNamespace(chat=SimpleNamespace(username="origin", title="Origin"))
        origin = forward_origin(msg)
        assert origin == Origin(
            kind="forward", chat_id=555, username="origin", title="Origin",
            msg_id=77, posted_at=WHEN,
        )
        assert origin.message_link == "https://t.me/origin/77"

    def test_forward_from_hidden_user_has_no_link(self):
        fwd = types.MessageFwdHeader(date=WHEN, from_name="Анна")
        origin = forward_origin(message("QA", fwd_from=fwd))
        assert origin.title == "Анна" and origin.message_link is None

    def test_not_forwarded(self):
        assert forward_origin(message("QA")) is None


class TestBuildRawPost:
    def test_full_post(self):
        markup = types.ReplyInlineMarkup(rows=[
            types.KeyboardInlineButtonRow(buttons=[
                types.KeyboardInlineButton(
                    text="Откликнуться", type=types.InlineButtonTypeUrl("https://hh.ru/v/9")
                )
            ])
        ])
        msg = message(
            "QA Engineer\nПодробнее",
            entities=[types.MessageEntityTextUrl(12, 9, "https://career.x.io/qa")],
            reply_markup=markup,
            media=document(),
            views=1500,
        )
        post = build_raw_post(msg, CHAT)
        assert post.text == "QA Engineer\nПодробнее (https://career.x.io/qa)"
        assert post.urls == ("https://career.x.io/qa", "https://hh.ru/v/9")
        assert post.buttons == (("Откликнуться", "https://hh.ru/v/9"),)
        assert [a.file_name for a in post.attachments] == ["JD.pdf"]
        assert post.views == 1500
        assert post.posted_at == WHEN
        assert post.message_link == "https://t.me/qa_jobs/10"

    def test_album_attachments_are_merged_into_the_captioned_post(self):
        caption = message("QA Engineer", msg_id=20, media=document("a.pdf"), grouped_id=5)
        other = message("", msg_id=21, media=document("b.pdf"), grouped_id=5)
        post = build_raw_post(caption, CHAT, album=[caption, other])
        assert [a.file_name for a in post.attachments] == ["a.pdf", "b.pdf"]

    def test_webpage_preview_url_is_kept(self):
        media = types.MessageMediaWebPage(webpage=types.WebPage(
            id=1, url="https://company.com/jobs/1", display_url="company.com", hash=0
        ))
        post = build_raw_post(message("QA", media=media), CHAT)
        assert post.urls == ("https://company.com/jobs/1",)

    def test_private_channel_link(self):
        chat = SimpleNamespace(id=1234567890, username=None, title="Закрытый")
        assert build_raw_post(message("QA"), chat).message_link == (
            "https://t.me/c/1234567890/10"
        )

    def test_post_refs(self):
        post = build_raw_post(
            message("Новая вакансия: t.me/qa_jobs/123 и t.me/c/777/5, канал t.me/qa_jobs"), CHAT
        )
        assert post.post_refs() == [PostRef("qa_jobs", None, 123), PostRef(None, 777, 5)]
