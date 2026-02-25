#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright"]
# ///
"""CLI tool to scrape X post text using your existing logged-in Firefox session."""

import glob
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Literal, cast

from playwright._impl._api_structures import SetCookieParam  # type: ignore[import]
from playwright.sync_api import sync_playwright


FIREFOX_PROFILES_DIR = Path.home() / "Library" / "Application Support" / "Firefox" / "Profiles"

SameSiteValue = Literal["None", "Lax", "Strict"]
SAME_SITE_MAP: dict[int, SameSiteValue] = {0: "None", 1: "Lax", 2: "Strict"}


def find_firefox_profile() -> Path:
    matches = glob.glob(str(FIREFOX_PROFILES_DIR / "*.default-release"))
    if not matches:
        matches = glob.glob(str(FIREFOX_PROFILES_DIR / "*.default"))
    if not matches:
        print("Error: No Firefox profile found.", file=sys.stderr)
        sys.exit(1)
    return Path(matches[0])


def extract_x_cookies(profile_path: Path) -> list[SetCookieParam]:
    """Copy cookies.sqlite to a temp file (avoids lock conflicts) and read x.com cookies."""
    cookies_db = profile_path / "cookies.sqlite"
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(tmp_fd)
    try:
        shutil.copy2(str(cookies_db), tmp_path)
        conn = sqlite3.connect(tmp_path)
        cursor = conn.execute(
            "SELECT name, value, host, path, expiry, isSecure, isHttpOnly, sameSite "
            "FROM moz_cookies "
            "WHERE host LIKE '%x.com' OR host LIKE '%twitter.com'"
        )
        cookies: list[SetCookieParam] = []
        for name, value, host, path, expiry, is_secure, is_http_only, same_site in cursor:
            cookies.append({
                "name": name,
                "value": value,
                "domain": host,
                "path": path,
                "expires": float(expiry) / 1000 if expiry > 0 else -1,
                "secure": bool(is_secure),
                "httpOnly": bool(is_http_only),
                "sameSite": cast(SameSiteValue, SAME_SITE_MAP.get(same_site, "None")),
            })
        conn.close()
    finally:
        os.unlink(tmp_path)
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

    profile_path = find_firefox_profile()
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
