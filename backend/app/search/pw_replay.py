"""Offline replay workbench: parse saved search fixtures without any
LinkedIn session (design §6 — selector iteration costs zero sessions).

Usage (from backend/):
    uv run python -m app.search.pw_replay                     # data_dir fixtures
    uv run python -m app.search.pw_replay --dir tests/fixtures
    uv run python -m app.search.pw_replay --diff A.json B.json

For every fixture the CLI prints the extracted Posts side-by-side with
their parse-sanity verdicts, so a selector change can be judged offline:
run a live search with SAVE_FIXTURES=always once, then iterate the
parser against that exact payload until the extraction is clean.

Playwright HTML fixtures are listed but not parsed until the Playwright
parser lands (branch item 6) — the CLI reports them honestly rather
than pretending to parse.
"""

import argparse
import json
import sys
from pathlib import Path

from .linkedin_mcp import parse_search_payload
from .base import Post


def _load_posts(path: Path) -> tuple[list[Post], str | None]:
    """Parse one fixture file. Returns (posts, error). error is None on
    success, a short message when the fixture can't be parsed (which is
    itself diagnostic — that's what a degraded capture looks like)."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [], f"unreadable: {exc}"
    if path.suffix == ".html":
        return [], "raw HTML fixture — re-capture as playwright-cards JSON"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [], f"not JSON: {exc}"
    if isinstance(data, dict) and data.get("kind") == "playwright-cards":
        try:
            from .playwright_source import parse_card_fixture

            return parse_card_fixture(data), None
        except Exception as exc:  # noqa: BLE001 — CLI must always report
            return [], f"card parse crashed: {exc}"
    if not isinstance(data, dict) or not isinstance(data.get("sections"), dict):
        return [], "payload has no sections dict (degraded capture shape)"
    try:
        return parse_search_payload(data), None
    except Exception as exc:  # noqa: BLE001 — CLI must always report
        return [], f"parse crashed: {exc}"


def _verdict(post: Post) -> str:
    """The sanity gate's one-line verdict for a parsed post."""
    from .base import Post as _P  # noqa: F401 — import keeps types local
    from ..pipeline.sanity import check_post

    report = check_post(post)
    return "OK" if report.ok else "FAIL: " + "; ".join(report.reasons)


def _print_report(path: Path) -> int:
    posts, error = _load_posts(path)
    print(f"\n=== {path.name} ===")
    if error:
        print(f"  ! {error}")
        return 1
    if not posts:
        print("  (no posts parsed — empty or degraded capture)")
        return 1
    fails = 0
    for i, post in enumerate(posts, 1):
        verdict = _verdict(post)
        if verdict != "OK":
            fails += 1
        print(f"  [{i}] {post.author_name or '(no author)'} — {verdict}")
        print(f"      url:    {post.post_url or '(none)'}")
        print(f"      posted: {post.posted_at or '(unknown)'}")
        first_line = (post.text or "").strip().splitlines()[0] if post.text else ""
        print(f"      text:   {first_line[:100]}")
        job = post.raw.get("job_id")
        if job:
            print(f"      job:    {job}")
    print(f"  {len(posts)} posts, {fails} failing sanity")
    return 1 if fails else 0


def _print_diff(a: Path, b: Path) -> int:
    """Compare two fixtures' parse results — the selector-rot answer:
    point it at last week's capture and this week's and read the delta."""
    posts_a, err_a = _load_posts(a)
    posts_b, err_b = _load_posts(b)
    if err_a or err_b:
        print(f"! cannot diff: {a.name}: {err_a} | {b.name}: {err_b}")
        return 1
    ids_a = {p.post_id for p in posts_a}
    ids_b = {p.post_id for p in posts_b}
    by_id_b = {p.post_id: p for p in posts_b}
    changed = 0
    print(f"\n=== diff {a.name} -> {b.name} ===")
    for pid in sorted(ids_b - ids_a):
        changed += 1
        p = by_id_b[pid]
        print(f"  + new parse: {p.author_name or '(no author)'} ({pid[:12]}…)")
    for pid in sorted(ids_a - ids_b):
        changed += 1
        print(f"  - lost post: {pid[:12]}…")
    print(f"  {len(posts_a)} posts before, {len(posts_b)} after, {changed} differences")
    return 1 if changed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pw_replay", description="Replay saved search fixtures offline"
    )
    parser.add_argument("--dir", type=Path, default=None, help="fixtures directory")
    parser.add_argument("--diff", nargs=2, metavar=("A", "B"), help="diff two fixtures")
    args = parser.parse_args(argv)

    if args.diff:
        paths = [Path(p) for p in args.diff]
        return _print_diff(paths[0], paths[1])

    directory = args.dir
    if directory is None:
        from ..config import settings

        directory = settings.data_dir / "fixtures"
    files = sorted(
        (p for p in directory.iterdir() if p.suffix in (".json", ".html")),
        key=lambda p: p.name,
    )
    if not files:
        print(f"No fixtures in {directory} — capture one with SAVE_FIXTURES=always")
        return 0
    failures = 0
    for path in files:
        failures += _print_report(path)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
