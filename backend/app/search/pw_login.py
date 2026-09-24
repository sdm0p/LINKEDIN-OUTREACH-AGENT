"""One-time Playwright login (design §4): open a headed browser on the
persistent profile, let the human log in, wait until LinkedIn shows the
logged-in feed, done — the profile dir keeps the session for every later
run. Run it again any time the session dies.

    uv run python -m app.search.pw_login

Nothing is automated except the waiting: the login itself (email, phone
verification, any checkpoint) is yours to do by hand. Per the recorded
account strategy, this is the DUMMY account's one-time login — aged
manually for 2-4 weeks before any live searching. The MCP session volume
is never touched (different browser, different profile).
"""

import sys
import time

FEED_URL_MARKER = "/feed/"
TIMEOUT_SECONDS = 600  # 10 minutes for email + phone verification


# Windows consoles default to cp1252 and crash on emoji in the success
# message. ASCII-only output keeps the CLI alive everywhere.


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    try:
        from patchright.sync_api import sync_playwright
    except ImportError:
        print(
            "playwright is not installed. Run:\n"
            "  uv add playwright\n"
            "  uv run playwright install chromium\n"
            "then re-run this command."
        )
        return 1
    from ..config import settings

    from . import pw_flow

    profile = settings.pw_profile_dir
    profile.mkdir(parents=True, exist_ok=True)
    print(f"Profile dir : {profile}")
    print("Opening a headed browser — log in by hand (this is the DUMMY")
    print("account's one-time login; nothing is typed programmatically).")
    print("If a captcha/verification appears: solve it by hand — it may ask")
    print("more than once. Home WiFi, no VPN. If challenges keep looping, wait")
    print("20-30 minutes before the next attempt (rapid retries risk a lock).")

    with sync_playwright() as pw:
        # Try the real installed Chrome, then Edge, then bundled Chromium —
        # a mainstream fingerprint gets challenged far less (pw_flow note).
        context = None
        last_error: Exception | None = None
        for channel in pw_flow.CHANNEL_CANDIDATES:
            kwargs = {
                "user_data_dir": str(profile),
                "headless": False,  # a login you cannot see is one you cannot do
                "viewport": {"width": 1440, "height": 900},
                "args": list(pw_flow.STEALTH_ARGS),
            }
            if channel:
                kwargs["channel"] = channel
            try:
                context = pw.chromium.launch_persistent_context(**kwargs)
                print(f"Browser: system {channel}" if channel else "Browser: bundled Chromium")
                break
            except Exception as exc:  # noqa: BLE001 — try the next channel
                last_error = exc
                context = None
        if context is None:
            print(f"Could not launch a browser: {last_error}")
            return 1
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(
            "https://www.linkedin.com/login/",
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        print(f"Waiting up to {TIMEOUT_SECONDS // 60} minutes for the feed…")
        deadline = time.monotonic() + TIMEOUT_SECONDS
        ok = False
        while time.monotonic() < deadline:
            url = (page.url or "").lower()
            if FEED_URL_MARKER in url:
                ok = True
                break
            try:
                page.wait_for_timeout(2000)
            except Exception:  # noqa: BLE001 — user closed/refreshed the tab
                break
        if ok:
            print("\n[OK] Logged in — the session is saved in the profile dir.")
            print("   Runs with SEARCH_SOURCE=playwright will reuse it.")
            print("   Verify anytime: Settings → Check session.")
            print("\nWarming the session for 30s — scroll your feed like a")
            print("person would; a warmed profile sticks better.")
            try:
                page.wait_for_timeout(30_000)
            except Exception:  # noqa: BLE001 — window closed early, fine
                pass
        else:
            print("\n[!] Timed out waiting for the feed. The profile keeps")
            print("   whatever state it has; re-run this command to retry.")
        context.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
