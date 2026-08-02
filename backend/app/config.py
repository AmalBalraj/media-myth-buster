from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    app_env: str = "dev"
    log_level: str = "INFO"
    public_base_url: str = "http://localhost:8100"
    web_base_url: str = "http://localhost:3001"

    database_url: str = "postgresql+asyncpg://myth:myth@localhost:8101/mythbuster"
    redis_url: str = "redis://localhost:6379/7"
    media_dir: Path = Path("./data/media")
    media_retention_days: int = 7

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"

    groq_api_key: str = ""
    groq_asr_model: str = "whisper-large-v3-turbo"

    gemini_api_key: str = ""
    # The 2.5 family still appears in ListModels but is refused for new API keys
    # ("no longer available to new users"), so the listing is not an availability
    # signal — these are pinned to models verified against a fresh key. Pinned
    # rather than `-latest` so a past report's model stays reproducible.
    gemini_video_model: str = "gemini-3.6-flash"
    gemini_light_model: str = "gemini-3.5-flash-lite"

    forensics_url: str = ""
    forensics_token: str = ""

    ig_user_id: str = ""
    ig_access_token: str = ""
    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_webhook_verify_token: str = "change-me"
    meta_graph_version: str = "v23.0"

    enable_ytdlp_fallback: bool = False
    ytdlp_cookies_file: str = ""

    google_factcheck_api_key: str = ""
    searxng_url: str = "http://localhost:8102"
    tavily_api_key: str = ""

    max_media_mb: int = 100
    max_duration_sec: int = 600
    worker_concurrency: int = 2

    # Bumping this invalidates every cached report — do it when pipeline
    # semantics change, not for cosmetic edits.
    pipeline_version: str = "1"

    @property
    def graph_base(self) -> str:
        return f"https://graph.facebook.com/{self.meta_graph_version}"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.media_dir.mkdir(parents=True, exist_ok=True)
    return s


settings = get_settings()
