#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright"]
# ///
"""CLI tool to scrape X post text using your existing logged-in Firefox session."""

import sys
from typing import Literal, cast

from playwright._impl._api_structures import SetCookieParam  # type: ignore[import]
from playwright.sync_api import sync_playwright

from firefox_cookies import find_firefox_profile, load_firefox_cookies

SameSiteValue = Literal["None", "Lax", "Strict"]
SAME_SITE_MAP: dict[int, SameSiteValue] = {0: "None", 1: "Lax", 2: "Strict"}


def extract_x_cookies(profile_path) -> list[SetCookieParam]:
    """Load Firefox cookies for x.com/twitter.com and adapt them for Playwright."""
    raw_cookies = load_firefox_cookies(
        profile_path, lambda host: "x.com" in host or "twitter.com" in host
    )
    cookies: list[SetCookieParam] = []
    for cookie in raw_cookies:
        same_site = SAME_SITE_MAP.get(cookie.same_site or 0, "None")
        cookies.append({
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.host,
            "path": cookie.path,
            "expires": float(cookie.expiry) / 1000 if cookie.expiry > 0 else -1,
            "secure": cookie.is_secure,
            "httpOnly": cookie.is_http_only,
            "sameSite": cast(SameSiteValue, same_site),
        })
    return cookies


def scrape_post(page, url: str) -> str:
    page.goto(url, wait_until="domcontentloaded")
    try:
        page.wait_for_selector('[data-testid="tweetText"]', timeout=15000)
        elements = page.query_selector_all('[data-testid="tweetText"]')
        return elements[0].inner_text() if elements else "(no text found)"
    except Exception:
        return "(timed out or post unavailable)"


def main() -> None:
    urls = sys.argv[1:]
    if not urls:
        print("Usage: uv run open_x.py <url1> [url2] ...")
        print("Example: uv run open_x.py https://x.com/user/status/123456789")
        sys.exit(1)

    try:
        profile_path = find_firefox_profile()
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Using Firefox profile: {profile_path}")

    cookies = extract_x_cookies(profile_path)
    print(f"Loaded {len(cookies)} x.com cookies\n")

    with sync_playwright() as p:
        browser = p.firefox.launch(headless=False)
        context = browser.new_context()
        context.add_cookies(cookies)
        page = context.new_page()

        for url in urls:
            print(f"--- {url}")
            text = scrape_post(page, url)
            print(text)
            print()

        browser.close()


if __name__ == "__main__":
    main()
