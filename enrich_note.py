#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright", "yt-dlp", "requests"]
# ///
"""CLI tool to enrich a markdown note by fetching text for all X post and YouTube URLs found in it."""

from __future__ import annotations

import argparse
import json
import re
import sys
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from get_yt_subtitle import fetch_subtitle_text, youtube_cookiefile_from_firefox
from open_x import extract_x_cookies, find_firefox_profile, scrape_post
from playwright.sync_api import sync_playwright

X_URL_PATTERN = re.compile(r"https?://(?:x|twitter)\.com/\w+/status/(\d+)[^\s]*")
YT_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com/watch\?(?:[^\s#]*&)?v=([A-Za-z0-9_-]{11})|youtu\.be/([A-Za-z0-9_-]{11}))[^\s]*"
)


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


def slugify(value: str) -> str:
    """Convert a title to a filesystem-friendly slug."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "untitled"


class _TemplateDict(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return ""


@dataclass
class OutputConfig:
    base_dir: Path
    yt_template: str
    x_template: str

    def youtube_path(self, *, video_id: str, title: str) -> Path:
        ctx = _TemplateDict(
            video_id=video_id,
            title=title,
            title_slug=slugify(title) if title else video_id,
        )
        return self.base_dir / self.yt_template.format_map(ctx)

    def x_path(self, *, post_id: str) -> Path:
        ctx = _TemplateDict(post_id=post_id)
        return self.base_dir / self.x_template.format_map(ctx)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch YouTube subtitles and/or X posts referenced inside a markdown note.",
    )
    parser.add_argument("note", help="Path to the markdown note to scan for URLs.")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory where fetched items will be saved. "
        "Relative paths are resolved against the note's parent directory. "
        "Defaults to <note_dir>/staging.",
    )
    parser.add_argument(
        "--yt-template",
        default="yt-{video_id}.md",
        help="Filename template for YouTube outputs. "
        "Supported placeholders: {video_id}, {title}, {title_slug}. "
        "Defaults to 'yt-{video_id}.md'.",
    )
    parser.add_argument(
        "--x-template",
        default="x-{post_id}.md",
        help="Filename template for X outputs. Supported placeholder: {post_id}.",
    )
    parser.add_argument(
        "--json-report",
        action="store_true",
        help="Emit a final JSON summary of processed URLs for machine consumption.",
    )
    parser.add_argument(
        "--yt-use-firefox-cookies",
        action="store_true",
        help="Load YouTube cookies from Firefox to access members-only videos.",
    )
    parser.add_argument(
        "--yt-firefox-profile",
        help="Optional explicit Firefox profile directory for YouTube cookies.",
    )
    return parser.parse_args()


def resolve_output_dir(note_path: Path, raw_dir: str | None) -> Path:
    if raw_dir:
        candidate = Path(raw_dir).expanduser()
        if not candidate.is_absolute():
            candidate = (note_path.parent / candidate).resolve()
        return candidate
    return (note_path.parent / "staging").resolve()


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def main() -> None:
    args = parse_args()
    md_path = Path(args.note).expanduser().resolve()
    if not md_path.exists():
        print(f"Error: file not found: {md_path}", file=sys.stderr)
        sys.exit(1)

    yt_pairs, x_pairs = extract_urls(md_path)
    if not yt_pairs and not x_pairs:
        print("No YouTube or X/Twitter post URLs found in the file.")
        sys.exit(0)

    print(f"Found {len(yt_pairs)} YouTube URL(s) and {len(x_pairs)} X post URL(s) in {md_path.name}")

    output_dir = resolve_output_dir(md_path, args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    config = OutputConfig(output_dir, args.yt_template, args.x_template)

    report: dict[str, list[dict[str, Any]]] = {"youtube": [], "x": []}

    if args.yt_use_firefox_cookies:
        try:
            yt_cookie_ctx = youtube_cookiefile_from_firefox(args.yt_firefox_profile)
        except FileNotFoundError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        yt_cookie_ctx = nullcontext(None)

    # --- YouTube: fetch subtitles ---
    with yt_cookie_ctx as yt_cookie_file:
        for url, video_id in yt_pairs:
            entry: dict[str, Any] = {"url": url, "video_id": video_id}
            report["youtube"].append(entry)
            print(f"--- {url}")
            try:
                title, text = fetch_subtitle_text(url, lang="en", cookiefile=yt_cookie_file)
            except Exception as exc:
                entry["status"] = "error"
                entry["error"] = str(exc)
                print(f"    warning: could not fetch subtitles: {exc}", file=sys.stderr)
                continue
            out_file = config.youtube_path(video_id=video_id, title=title)
            ensure_parent(out_file)
            out_file.write_text(
                ("\n\n".join(filter(None, (f"# {title}" if title else "", url, text)))).strip() + "\n",
                encoding="utf-8",
            )
            entry["status"] = "ok"
            entry["output_path"] = str(out_file)
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
                entry = {"url": url, "post_id": post_id}
                report["x"].append(entry)
                print(f"--- {url}")
                text = scrape_post(page, url)
                out_file = config.x_path(post_id=post_id)
                ensure_parent(out_file)
                out_file.write_text(f"{url}\n\n{text}", encoding="utf-8")
                entry["status"] = "ok"
                entry["output_path"] = str(out_file)
                print(f"    saved → {out_file}")

            browser.close()

    if args.json_report:
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
