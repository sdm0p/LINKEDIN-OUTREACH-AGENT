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
    # gemini-2.5-flash: separate quota bucket from gemini-3.5-flash (whose
    # free-tier RPD was exhausted). Swap freely via GEMINI_MODEL in .env.
    gemini_model: str = "gemini-2.5-flash"

    # Runtime data (SQLite db, uploaded resume). Local-only by design.
    data_dir: Path = BASE_DIR / "data"

    # Reliability cap from the plan. Enforced when the draft stage is built.
    max_drafts_per_day: int = 20


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
