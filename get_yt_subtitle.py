#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["yt-dlp", "requests"]
# ///
"""Fetch subtitles for a YouTube video using yt-dlp."""

from __future__ import annotations

import html
import re
import sys
from typing import Optional

import requests
import yt_dlp  # type: ignore[import]


_TIMECODE_RE = re.compile(r"\d{2}:\d{2}:\d{2}\.\d{3}\s+-->\s+\d{2}:\d{2}:\d{2}\.\d{3}")


def _strip_subtitle_markup(raw: str) -> str:
    """Remove timestamps, indices, and layout cues from subtitle text.

    Handles common VTT/SRT patterns and returns plain text lines.
    """
    lines: list[str] = []
    last_text_line: Optional[str] = None
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # Skip headers and cue settings.
        if stripped.startswith("WEBVTT") or stripped.startswith("NOTE"):
            continue
        if "X-TIMESTAMP-MAP" in stripped:
            continue

        # Skip simple metadata lines that sometimes appear in YouTube exports.
        if stripped.lower().startswith(("kind:", "language:")):
            continue

        # Skip pure numeric indices (SRT).
        if stripped.isdigit():
            continue

        # Skip timecode lines (VTT/SRT).
        if "-->" in stripped or _TIMECODE_RE.fullmatch(stripped):
            continue

        # Drop simple VTT cue settings (position, align, line, size, etc.).
        if any(
            stripped.lower().startswith(prefix)
            for prefix in (
                "position:",
                "align:",
                "line:",
                "size:",
                "region:",
            )
        ):
            continue

        # Remove simple HTML / VTT tags like <c>, <i>, <b>, <00:00:01.000>.
        text_only = re.sub(r"<[^>]+>", "", stripped)

        # Remove stage directions like [music], [applause], [clears throat], etc.
        text_only = re.sub(r"\[[^\]]*\]", "", text_only)

        # Decode HTML entities such as &gt;&gt;.
        text_only = html.unescape(text_only)

        # Strip speaker / cue arrows (>> Hello).
        text_only = re.sub(r"^>+\s*", "", text_only)

        # Normalize internal whitespace.
        text_only = " ".join(text_only.split())

        if text_only:
            # Some YouTube subtitles repeat the same cue text multiple times;
            # avoid emitting consecutive duplicate lines.
            if text_only != last_text_line:
                lines.append(text_only)
                last_text_line = text_only

    if not lines:
        return ""

    # Return a single compact paragraph; no line breaks.
    return " ".join(lines).strip()


def fetch_subtitle_text(video_url: str, lang: str = "en") -> tuple[str, str]:
    """Return (title, plain_subtitle_text) for the given YouTube URL.

    Prefers manually uploaded subtitles, with automatic captions as a fallback.
    """
    ydl_opts: dict[str, object] = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": [lang],
        # Ask yt-dlp for a consistent text-based format (still includes timestamps).
        "subtitlesformat": "vtt",
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)

    title = str(info.get("title") or "").strip()

    subtitle_url: Optional[str] = None

    # Prefer human-made subtitles, then fall back to auto captions.
    for field in ("subtitles", "automatic_captions"):
        tracks = info.get(field) or {}
        if not isinstance(tracks, dict):
            continue
        lang_tracks = tracks.get(lang)
        if isinstance(lang_tracks, list) and lang_tracks:
            # Pick the last format entry (often the best or vtt).
            candidate = lang_tracks[-1]
            if isinstance(candidate, dict) and "url" in candidate:
                subtitle_url = str(candidate["url"])
                break

    if not subtitle_url:
        raise RuntimeError(f"No subtitles found for language '{lang}'")

    resp = requests.get(subtitle_url, timeout=30)
    resp.raise_for_status()
    text = _strip_subtitle_markup(resp.text)
    return title, text


def main() -> None:
    """Test helper by fetching subtitles for a single URL."""
    if len(sys.argv) > 2:
        print("Usage: uv run get_yt_subtitle.py [youtube_url]", file=sys.stderr)
        sys.exit(1)

    video_url = (
        sys.argv[1]
        if len(sys.argv) == 2
        else "https://www.youtube.com/watch?v=akM6P97_0B8&t=1s"
    )

    print(f"Fetching subtitles for: {video_url}")
    try:
        title, text = fetch_subtitle_text(video_url, lang="en")
    except Exception as exc:  # pragma: no cover - simple CLI
        print(f"Error fetching subtitles: {exc}", file=sys.stderr)
        sys.exit(1)

    if title:
        print(f"Title: {title}")
    # Print a preview so the output isn't overwhelming.
    preview_len = 2000
    print(f"\n--- subtitle preview (first {preview_len} chars) ---\n")
    print(text[:preview_len])


if __name__ == "__main__":
    main()

