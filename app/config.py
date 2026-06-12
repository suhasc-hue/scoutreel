"""Application settings loaded from .env via pydantic-settings."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DB = f"sqlite:///{(PROJECT_ROOT / 'scoutreel.db').as_posix()}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", str(PROJECT_ROOT / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- API keys ---
    youtube_api_key: str = ""
    anthropic_api_key: str = ""  # optional, enables LLM classifier + compliment lines
    use_llm_classifier: bool = False
    anthropic_model: str = "claude-sonnet-4-20250514"

    # --- Gmail / outreach ---
    gmail_credentials_file: str = "credentials.json"
    gmail_token_file: str = "token.json"
    daily_send_cap: int = 15  # hard max 30 is enforced in guardrails regardless

    # --- YouTube quota ---
    daily_yt_quota_budget: int = 9000
    max_searches_per_day: int = 60
    discovery_window_days: int = 14
    tracking_window_days: int = 21

    # --- Crawler ---
    crawler_contact_email: str = "you@yourdomain.com"
    # Optional Brave Search API key — enables the web-search step of the
    # contact pipeline (official API, no search-engine scraping).
    brave_api_key: str = ""

    # --- Phase 4 (feature-flagged) ---
    x_bearer_token: str = ""
    ig_provider_key: str = ""
    enable_x_source: bool = False
    enable_instagram_source: bool = False

    # --- Storage ---
    database_url: str = _DEFAULT_DB

    # --- Scoring constants (tunable) ---
    score_velocity_weight: float = 2.0
    score_engagement_weight: float = 100.0
    score_recency_weight: float = 2.0
    score_comment_weight: float = 3.0
    recency_window_days: float = 14.0

    # --- Outreach identity ---
    user_name: str = "Your Name"
    user_pitch: str = (
        "I work with emerging filmmakers to get their shorts in front of bigger audiences."
    )
    signature: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
