"""Wiley DOI prefixes shared by the desktop UI and downloader.

These select the browser for resolution. The landing site decides which
publisher handles the download because journals can change publishers.
"""
import re
from urllib.parse import unquote, urlsplit


DOI_PREFIXES = ("10.1002", "10.1111")


def is_wiley_doi(doi: str | None) -> bool:
    return bool(doi and doi.partition("/")[0] in DOI_PREFIXES)


def article_doi(url: str) -> str | None:
    """Extract an article DOI from the path of a Wiley journal URL."""
    if not url or not url.lower().startswith(("http://", "https://")):
        return None
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").rstrip(".")
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not (host == "wiley.com" or host.endswith(".wiley.com")):
        return None
    match = re.search(r"/(10\.\d{4,9}/.+)", unquote(parts.path), re.I)
    return match.group(1) if match else None
