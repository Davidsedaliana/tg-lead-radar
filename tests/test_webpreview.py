from tg_lead_radar.webpreview import parse_preview

SAMPLE = """
<html><body>
<div class="tgme_widget_message text_not_supported_wrap js-widget_message" data-post="Remoteit/101">
  <div class="tgme_widget_message_bubble">
    <div class="tgme_widget_message_text js-message_text" dir="auto">
      Нужен <b>телеграм-бот</b> для заявок.<br/>Бюджет 40к.
    </div>
    <div class="tgme_widget_message_footer">
      <a class="tgme_widget_message_date" href="https://t.me/Remoteit/101">
        <time datetime="2026-08-24T14:20:11+00:00" class="time">17:20</time>
      </a>
    </div>
  </div>
</div>
<div class="tgme_widget_message js-widget_message" data-post="Remoteit/102">
  <div class="tgme_widget_message_bubble">
    <!-- photo-only post, no text node -->
  </div>
</div>
<div class="tgme_widget_message js-widget_message" data-post="Remoteit/abc">
  <div class="tgme_widget_message_text">broken id</div>
</div>
</body></html>
"""


def test_parses_posts_with_text():
    posts = parse_preview(SAMPLE)
    assert len(posts) == 1
    post = posts[0]
    assert post.channel == "Remoteit"
    assert post.msg_id == 101
    assert "телеграм-бот" in post.text
    assert "Бюджет 40к" in post.text
    assert post.link == "https://t.me/Remoteit/101"
    assert post.published == "2026-08-24T14:20:11+00:00"


def test_empty_page():
    assert parse_preview("<html><body>nothing here</body></html>") == []
