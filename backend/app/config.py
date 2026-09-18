import json
import os
import threading
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/

# Runtime API key management (Settings page). The key lives in THREE
# places, checked in this order:
#   1. _runtime_api_key  — set this process's lifetime (in memory)
#   2. data_dir/gemini_key — persisted mirror, 0600 (survives restarts;
#      inside Docker this is the app-data volume, so it survives rebuilds)
#   3. GEMINI_API_KEY env var / backend/.env — read-only fallback, never
#      written by the app (writing other people's env files is how keys
#      get accidentally deleted).
_API_KEY_LOCK = threading.Lock()
_KEY_FILE_NAME = "gemini_key"
_runtime_api_key: str | None = None


def _key_file_path() -> Path:
    return settings.data_dir / _KEY_FILE_NAME


def _read_key_file() -> str | None:
    try:
        return _key_file_path().read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _write_key_file(key: str) -> None:
    path = _key_file_path()
    path.write_text(key + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # Windows/FAT: no meaningful perms; the dir is user-local anyway


def _delete_key_file() -> None:
    try:
        _key_file_path().unlink()
    except OSError:
        pass


# ---------- run location targets ----------
# The countries a run should find hiring posts in (e.g. ["India"]). Kept in
# a data-dir file (survives restarts, like the API-key mirror); empty means
# no geo filter — posts from anywhere qualify.
_TARGETS_FILE_NAME = "target_countries.json"


def get_target_countries() -> list[str]:
    """The configured target countries, empty when no geo filter is set.
    Tolerates a corrupted/missing file by returning empty (fail-open: the
    location filter only ever DROPS on positive knowledge, so a read
    failure must not silently start dropping posts)."""
    try:
        raw = (settings.data_dir / _TARGETS_FILE_NAME).read_text(encoding="utf-8").strip()
        data = json.loads(raw) if raw else []
        if not isinstance(data, list):
            return []
        return [str(c).strip() for c in data if str(c).strip()]
    except OSError:
        return []
    except ValueError:
        return []


def set_target_countries(countries: list[str]) -> None:
    (settings.data_dir / _TARGETS_FILE_NAME).write_text(
        json.dumps(countries), encoding="utf-8"
    )


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

    # ---------- runtime key management (Settings page) ----------

    def effective_gemini_api_key(self) -> str | None:
        """The key to actually use: runtime > stored file > env."""
        return _runtime_api_key or _read_key_file() or self.gemini_api_key

    def set_api_key_runtime(self, key: str) -> None:
        """Persist the key (memory + data-dir mirror). The mirror lives in
        data_dir, so in Docker it lands on the app-data volume."""
        global _runtime_api_key
        key = key.strip()
        with _API_KEY_LOCK:
            _runtime_api_key = key
            _write_key_file(key)

    def clear_api_key_runtime(self) -> None:
        """Forget the runtime/stored key. The env fallback is untouched —
        only keys this feature wrote are ever removed."""
        global _runtime_api_key
        with _API_KEY_LOCK:
            _runtime_api_key = None
            _delete_key_file()

    def key_source(self) -> str | None:
        """Where the effective key comes from: runtime|stored|env|None."""
        if _runtime_api_key is not None:
            return "runtime"
        if _read_key_file() is not None:
            return "stored"
        if self.gemini_api_key:
            return "env"
        return None


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
