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


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "playwright is not installed. Run:\n"
            "  uv add playwright\n"
            "  uv run playwright install chromium\n"
            "then re-run this command."
        )
        return 1
    from .config import settings

    profile = settings.pw_profile_dir
    profile.mkdir(parents=True, exist_ok=True)
    print(f"Profile dir : {profile}")
    print("Opening a headed browser — log in by hand (this is the DUMMY")
    print("account's one-time login; nothing is typed programmatically).")

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=False,  # a login you cannot see is a login you cannot do
            viewport={"width": 1440, "height": 900},
        )
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
            print("\n✅ Logged in — the session is saved in the profile dir.")
            print("   Runs with SEARCH_SOURCE=playwright will reuse it.")
            print("   Verify anytime: Settings → Check session.")
        else:
            print("\n⚠️ Timed out waiting for the feed. The profile keeps")
            print("   whatever state it has; re-run this command to retry.")
        context.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
