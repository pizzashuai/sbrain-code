#!/usr/bin/env python3
"""Helpers for reusing an existing Firefox session (cookies) from Python scripts."""

from __future__ import annotations

import glob
import os
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator


FIREFOX_PROFILES_DIR = Path.home() / "Library" / "Application Support" / "Firefox" / "Profiles"


@dataclass
class FirefoxCookie:
    name: str
    value: str
    host: str
    path: str
    expiry: int
    is_secure: bool
    is_http_only: bool
    same_site: int | None


def find_firefox_profile(explicit: str | None = None) -> Path:
    """Return the best-guess Firefox profile path."""
    if explicit:
        path = Path(explicit).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"Firefox profile not found: {path}")
        return path

    patterns = ("*.default-release", "*.default")
    for pattern in patterns:
        matches = glob.glob(str(FIREFOX_PROFILES_DIR / pattern))
        if matches:
            return Path(matches[0])
    raise FileNotFoundError(
        f"No Firefox profile found under {FIREFOX_PROFILES_DIR}. Launch Firefox once to create one."
    )


def _copy_cookie_db(profile_path: Path) -> Path:
    cookies_db = profile_path / "cookies.sqlite"
    if not cookies_db.exists():
        raise FileNotFoundError(f"cookies.sqlite not found in profile: {profile_path}")
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(tmp_fd)
    shutil.copy2(str(cookies_db), tmp_path)
    return Path(tmp_path)


def load_firefox_cookies(
    profile_path: Path, host_predicate: Callable[[str], bool]
) -> list[FirefoxCookie]:
    """Return cookies from Firefox filtered by host predicate."""
    tmp_db = _copy_cookie_db(profile_path)
    cookies: list[FirefoxCookie] = []
    try:
        conn = sqlite3.connect(tmp_db)
        cursor = conn.execute(
            "SELECT name, value, host, path, expiry, isSecure, isHttpOnly, sameSite FROM moz_cookies"
        )
        for name, value, host, path, expiry, is_secure, is_http_only, same_site in cursor:
            host = host or ""
            if host_predicate(host):
                cookies.append(
                    FirefoxCookie(
                        name=name or "",
                        value=value or "",
                        host=host,
                        path=path or "/",
                        expiry=int(expiry or 0),
                        is_secure=bool(is_secure),
                        is_http_only=bool(is_http_only),
                        same_site=int(same_site) if same_site is not None else None,
                    )
                )
        conn.close()
    finally:
        tmp_db.unlink(missing_ok=True)
    return cookies


def cookies_to_netscape_file(cookies: Iterable[FirefoxCookie]) -> Path:
    """Persist cookies to a temporary Netscape cookie file usable by yt-dlp."""
    fd, tmp_path = tempfile.mkstemp(suffix=".cookies")
    os.close(fd)
    path = Path(tmp_path)
    with path.open("w", encoding="utf-8") as fh:
        fh.write("# Netscape HTTP Cookie File\n")
        for cookie in cookies:
            domain = cookie.host or ""
            include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
            secure = "TRUE" if cookie.is_secure else "FALSE"
            expiry = str(max(int(cookie.expiry), 0))
            prefix = "#HttpOnly_" if cookie.is_http_only else ""
            fh.write(
                f"{prefix}{domain}\t{include_subdomains}\t{cookie.path or '/'}\t"
                f"{secure}\t{expiry}\t{cookie.name}\t{cookie.value}\n"
            )
    return path


@contextmanager
def firefox_cookie_jar(
    host_predicate: Callable[[str], bool], profile_path: Path | None = None
) -> Iterator[Path]:
    profile = profile_path or find_firefox_profile()
    cookies = load_firefox_cookies(profile, host_predicate)
    tmp_path = cookies_to_netscape_file(cookies)
    try:
        yield tmp_path
    finally:
        tmp_path.unlink(missing_ok=True)
