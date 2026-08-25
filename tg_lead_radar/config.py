from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CHANNELS = (
    "Remoteit,digitaltender,webfrl,freelead,slavelancers,freelansim_ru,freelancetaverna"
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
    web_channels: str = "Remoteit,digitaltender,webfrl,freelansim_ru,freelancetaverna"
    poll_interval_seconds: int = 300

    # Comma-separated channel/chat usernames (without @).
    channels: str = DEFAULT_CHANNELS
    # Where to send lead cards; "me" = Saved Messages.
    notify_chat: str = "me"

    session_path: str = "data/radar"
    db_path: str = "data/seen.db"

    def channel_list(self) -> list[str]:
        return _split(self.channels)

    def web_channel_list(self) -> list[str]:
        return _split(self.web_channels)


def _split(raw: str) -> list[str]:
    return [c.strip().lstrip("@") for c in raw.split(",") if c.strip()]


def load_settings() -> Settings:
    return Settings()
