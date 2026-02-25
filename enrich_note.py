#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright", "yt-dlp", "requests"]
# ///
"""CLI tool to enrich a markdown note by fetching text for all X post and YouTube URLs found in it."""

import re
import sys
from pathlib import Path

from get_yt_subtitle import fetch_subtitle_text
from open_x import extract_x_cookies, find_firefox_profile, scrape_post
from playwright.sync_api import sync_playwright

X_URL_PATTERN = re.compile(r"https?://(?:x|twitter)\.com/\w+/status/(\d+)[^\s]*")
YT_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com/watch\?(?:[^\s#]*&)?v=([A-Za-z0-9_-]{11})|youtu\.be/([A-Za-z0-9_-]{11}))[^\s]*"
)

STAGING_DIR = "staging"


def extract_urls(
    md_path: Path,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Return (yt_pairs, x_pairs) where each entry is (url, unique_id)."""
    content = md_path.read_text(encoding="utf-8")
    yt_pairs = [
        (m.group(0), m.group(1) or m.group(2))
        for m in YT_URL_PATTERN.finditer(content)
    ]
    x_pairs = [(m.group(0), m.group(1)) for m in X_URL_PATTERN.finditer(content)]
    return yt_pairs, x_pairs


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: uv run enrich_note.py <path/to/note.md>")
        sys.exit(1)

    md_path = Path(sys.argv[1]).expanduser().resolve()
    if not md_path.exists():
        print(f"Error: file not found: {md_path}", file=sys.stderr)
        sys.exit(1)

    yt_pairs, x_pairs = extract_urls(md_path)
    if not yt_pairs and not x_pairs:
        print("No YouTube or X/Twitter post URLs found in the file.")
        sys.exit(0)

    print(f"Found {len(yt_pairs)} YouTube URL(s) and {len(x_pairs)} X post URL(s) in {md_path.name}")

    output_dir = md_path.parent
    staging_dir = output_dir / STAGING_DIR
    staging_dir.mkdir(parents=True, exist_ok=True)

    # --- YouTube: fetch subtitles ---
    for url, video_id in yt_pairs:
        print(f"--- {url}")
        try:
            title, text = fetch_subtitle_text(url, lang="en")
        except Exception as exc:
            print(f"    warning: could not fetch subtitles: {exc}", file=sys.stderr)
            continue
        out_file = staging_dir / f"yt-{video_id}.md"
        body = f"{url}\n\n{text}"
        if title:
            body = f"# {title}\n\n{body}"
        out_file.write_text(body, encoding="utf-8")
        print(f"    saved → {out_file}")

    # --- X/Twitter: scrape with Playwright ---
    if x_pairs:
        profile_path = find_firefox_profile()
        print(f"Using Firefox profile: {profile_path}")
        cookies = extract_x_cookies(profile_path)
        print(f"Loaded {len(cookies)} x.com cookies\n")

        with sync_playwright() as p:
            browser = p.firefox.launch(headless=False)
            context = browser.new_context()
            context.add_cookies(cookies)
            page = context.new_page()

            for url, post_id in x_pairs:
                print(f"--- {url}")
                text = scrape_post(page, url)
                out_file = staging_dir / f"x-{post_id}.md"
                out_file.write_text(f"{url}\n\n{text}", encoding="utf-8")
                print(f"    saved → {out_file}")

            browser.close()


if __name__ == "__main__":
    main()
