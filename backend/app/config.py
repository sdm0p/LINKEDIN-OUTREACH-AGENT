from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gemini_api_key: str | None = None
    # gemini-3.1-flash-lite: Flash-Lite models carry the highest Gemini
    # free-tier limits (~15 RPM / ~1000+ RPD vs ~250 RPD for 2.5-flash).
    # Rate limits are per model — swap freely via GEMINI_MODEL in .env.
    gemini_model: str = "gemini-3.1-flash-lite"

    # Runtime data (SQLite db, uploaded resume). Local-only by design.
    data_dir: Path = BASE_DIR / "data"

    # Reliability cap from the plan. Enforced when the draft stage is built.
    max_drafts_per_day: int = 20


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
