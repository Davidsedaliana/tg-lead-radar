from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CHANNELS = "Remoteit,digitaltender,webfrl,freelead,slavelancers"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    api_id: int
    api_hash: str

    # Comma-separated channel/chat usernames (without @).
    channels: str = DEFAULT_CHANNELS
    # Where to send lead cards; "me" = Saved Messages.
    notify_chat: str = "me"

    session_path: str = "data/radar"
    db_path: str = "data/seen.db"

    def channel_list(self) -> list[str]:
        return [c.strip().lstrip("@") for c in self.channels.split(",") if c.strip()]


def load_settings() -> Settings:
    return Settings()
