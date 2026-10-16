import pytest

from tg_lead_radar.config import Settings
from tg_lead_radar.jobs.config import Terms, Vocabulary, load_jobs_config, term_pattern


class TestTermPattern:
    def test_stem_mode_makes_every_word_a_stem(self):
        assert term_pattern("автоматизац тестир", stem=True).search(
            "Опыт в автоматизации   тестирования"
        )

    def test_stem_matches_at_word_start_only(self):
        assert not term_pattern("qa", stem=True).search("aqa")
        assert term_pattern("qa", stem=True).search("QA-инженер")

    def test_hashtag_matches_at_line_start(self):
        # \b before "#" never matches: the guard must only apply to word chars
        assert term_pattern("#резюме", stem=True).search("#резюме QA Engineer")

    def test_whole_word_by_default(self):
        assert term_pattern("lead").search("QA Lead")
        assert not term_pattern("lead").search("leading company")
        assert not term_pattern("java").search("javascript")

    def test_star_makes_a_stem(self):
        assert term_pattern("сеньор*").search("ищем сеньора")

    def test_symbols(self):
        assert term_pattern("c#").search("Автотесты на C#.")
        assert term_pattern("c++").search("C++ и Python")
        assert term_pattern("💰").search("💰250 000 ₽")


class TestDictionaries:
    def test_terms_report_hits(self):
        terms = Terms.of(["qa", "pytest"], stem=True)
        assert terms.hits("Python + Pytest") == ["pytest"]

    def test_vocabulary_reports_canonical_names_in_config_order(self):
        vocab = Vocabulary.of({"python": ["python", "питон*"], "sql": ["sql"]})
        assert vocab.found("SQL, питона немного") == ["python", "sql"]


def test_default_config_loads():
    cfg = load_jobs_config()
    assert "qa" in [t for t, _ in cfg.filter.stems.items]
    assert cfg.extract.default_currency == "RUB"
    assert cfg.discover.thresholds.alive_max_days == 3


def test_missing_key_is_reported(tmp_path):
    path = tmp_path / "jobs.toml"
    path.write_text("[filter]\nstems = []\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        load_jobs_config(str(path))


def test_settings_defaults_keep_orders_mode_only():
    s = Settings(_env_file=None)
    assert s.orders_enabled and not s.jobs_enabled
    assert not s.jobs_notify
    assert s.jobs_backfill_days == 14


def test_jobs_channel_list():
    s = Settings(jobs_channels="@qa_jobs, it_remote ,", _env_file=None)
    assert s.jobs_channel_list() == ["qa_jobs", "it_remote"]
