from pydantic_settings import BaseSettings, SettingsConfigDict

# Absent on purpose: freelansim_ru (dead since February 2026, Habr Freelance
# shut down) and zakazy_freelance (one repeated ad post, no real orders).
DEFAULT_CHANNELS = (
    "freelance_dev_work,fa_devolopers,job_programming_apps_chatgpt_n8n,"
    "it_zakazy,freelance_zakazy,digitaltender,freelancetaverna,webfrl,"
    "Remoteit,freelead,slavelancers"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- userbot mode (needs my.telegram.org credentials) ---
    api_id: int = 0
    api_hash: str = ""

    # --- web mode (no credentials: polls t.me/s previews of public channels) ---
    # Notifications go through a regular bot (@BotFather token) to your user id.
    bot_token: str = ""
    notify_user_id: int = 0
    web_channels: str = (
        "it_zakazy,freelance_zakazy,digitaltender,freelancetaverna,webfrl,Remoteit"
    )
    poll_interval_seconds: int = 300

    # --- optional local LLM (Ollama) for personalised drafts ---
    llm_enabled: bool = False
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:14b"

    # Channels that publish nothing but orders. Their posts skip the keyword
    # gate and go straight to the model, so a well-paid order phrased in words
    # our stems miss is not lost. Ignored when the LLM is off.
    order_feeds: str = (
        "it_zakazy,freelance_zakazy,freelance_dev_work,fa_devolopers,"
        "job_programming_apps_chatgpt_n8n"
    )

    def order_feed_list(self) -> list[str]:
        return _split(self.order_feeds)

    # Comma-separated channel/chat usernames (without @).
    channels: str = DEFAULT_CHANNELS
    # Where to send lead cards; "me" = Saved Messages.
    notify_chat: str = "me"

    session_path: str = "data/radar"
    db_path: str = "data/seen.db"

    # --- modes (userbot): one process, one session, both modes ---
    orders_enabled: bool = True
    jobs_enabled: bool = False

    # --- jobs mode: QA vacancies exported as raw data, no LLM ---
    # Telegram folder with vacancy channels; JOBS_CHANNELS are added to it
    # (and are the only source when the folder is missing).
    jobs_folder: str = "Вакансии"
    jobs_channels: str = ""
    jobs_backfill_days: int = 14
    jobs_db_path: str = "data/jobs.db"
    jobs_export_dir: str = "data/jobs"
    # Lists, dictionaries and thresholds of the jobs mode and discover.
    jobs_config_path: str = "jobs.toml"
    # Cards into Saved Messages for new vacancies; the export is the main output.
    jobs_notify: bool = False

    # Separate login for scripts/discover_channels.py, so it runs while the
    # radar holds its own session.
    discover_session_path: str = "data/discover"

    def channel_list(self) -> list[str]:
        return _split(self.channels)

    def web_channel_list(self) -> list[str]:
        return _split(self.web_channels)

    def jobs_channel_list(self) -> list[str]:
        return _split(self.jobs_channels)


def _split(raw: str) -> list[str]:
    return [c.strip().lstrip("@") for c in raw.split(",") if c.strip()]


def load_settings() -> Settings:
    return Settings()
