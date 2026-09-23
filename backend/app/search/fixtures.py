"""Fixture-first harness (playwright-source-design.md §6).

Every raw search response can be saved as a fixture so parser work never
needs a live LinkedIn session: record once, iterate the parser offline,
regression-test against real shapes forever. Playwright and MCP sources
both funnel their raw payloads through here, so selector experiments on
the new source cost zero sessions and zero risk to the account.

Storage contract:
- Fixtures live in data_dir/fixtures — inside the gitignored data dir, so
  post content NEVER lands in the repository by accident. The repo keeps
  only hand-built seed fixtures under backend/tests/fixtures/ (synthetic
  text, live-shaped), which the parse tests run against.
- Mode comes from settings.save_fixtures: off (default — nothing is ever
  written), always, on-error (only degraded/unusable responses — the
  selector-rot alarm that fills the DLQ's story with evidence).
- Bounded: newest 30 files kept, oldest evicted. Failure to save is
  swallowed — the harness must never break the search it observes.
"""

import json
import re
import time
from pathlib import Path
from typing import Any

from ..config import settings

FIXTURES_KEEP = 30


def fixtures_dir() -> Path:
    return settings.data_dir / "fixtures"


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:40] or "kw"


def should_save(mode: str, degraded: bool) -> bool:
    """The save decision for one response, given the configured mode."""
    if mode == "always":
        return True
    if mode == "on-error":
        return degraded
    return False


def save_fixture(
    source: str,
    keyword: str,
    recency: str,
    payload: Any,
    kind: str,
) -> Path | None:
    """Write one fixture file; returns its path, or None on any failure.

    kind: "mcp-payload" (JSON dict) or "playwright-html" (HTML str) — the
    extension follows the kind so the replay CLI can pick the parser."""
    try:
        d = fixtures_dir()
        d.mkdir(parents=True, exist_ok=True)
        ext = "html" if kind == "playwright-html" else "json"
        ts = time.strftime("%Y%m%d-%H%M%S")
        path = d / f"{source}_{_slugify(keyword)}_{recency}_{ts}.{ext}"
        if kind == "playwright-html":
            path.write_text(str(payload), encoding="utf-8")
        else:
            # Store parseable JSON as an object so the replay CLI and tests
            # read it directly; garbage stays raw text (double-encoded),
            # which replay reports as a degraded capture — faithful either way.
            store: Any = payload
            if isinstance(payload, str):
                try:
                    store = json.loads(payload)
                except (json.JSONDecodeError, ValueError):
                    store = payload
            path.write_text(
                json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        _enforce_cap(d)
        return path
    except Exception:  # noqa: BLE001 — protect-the-protector
        return None


def _enforce_cap(d: Path) -> None:
    files = sorted(
        (p for p in d.iterdir() if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in files[FIXTURES_KEEP:]:
        try:
            old.unlink()
        except OSError:
            pass


def load_fixtures(directory: Path | None = None) -> list[Path]:
    """All fixture files, oldest first (stable iteration for tests/CLI)."""
    d = directory or fixtures_dir()
    if not d.exists():
        return []
    return sorted(
        (p for p in d.iterdir() if p.suffix in (".json", ".html") and p.is_file()),
        key=lambda p: p.stat().st_mtime,
    )
